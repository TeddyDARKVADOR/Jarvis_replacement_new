"""
cognition/runtime.py — ce que main.py voit de la cognition. Rien de plus.

    attach(jarvis) -> Cognition | None

Une seule classe, quatre entrées appelées par des hooks d'une ligne dans
main.py, et une sortie (prompt_block). Toutes obéissent à la même règle :

    UNE PANNE ICI NE DOIT JAMAIS ATTEINDRE LA CONVERSATION.

D'où :
  - les écritures passent par une file et un fil de fond : la boucle audio ne
    paie jamais un fsync SQLite ;
  - chaque méthode publique est enveloppée dans _safe : une exception devient
    une ligne de journal et une valeur neutre (None, "") ;
  - attach() renvoie None hors mode local, ou si quoi que ce soit échoue à
    l'ouverture. `if self._cognition:` dans main.py suffit alors à tout couper.
"""
from __future__ import annotations

import functools
import queue
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from . import policy
from .constitution import ConstitutionDrift, load as load_constitution, register
from .memory import Memory, slot_for
from .store import DEFAULT_DB, Store

BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_PATH = BASE_DIR / "memory" / "long_term.json"

# Budget du bloc ajouté au prompt système à chaque connexion. Mesuré par le
# selftest. Le dernier commit du projet réduisait les coûts de tokens : on
# n'ajoute que ce qui change le comportement.
PROMPT_BUDGET = 900

_active: "Cognition | None" = None


def get_active() -> "Cognition | None":
    """Pour les plugins : l'instance attachée au JARVIS local, ou None."""
    return _active


def _safe(default=None):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **kw):
            try:
                return fn(self, *a, **kw)
            except Exception as e:
                self._log(f"cognition: {fn.__name__} a échoué ({type(e).__name__}: {e})")
                if self.debug:
                    traceback.print_exc()
                return default
        return wrapper
    return deco


class Cognition:
    def __init__(self, db_path: Path | str = DEFAULT_DB, *, logger=None,
                 legacy_path: Path | None = LEGACY_PATH, background: bool = True,
                 constitution_path: Path | None = None, debug: bool = False):
        self.debug = debug
        self._logger = logger or (lambda m: print(f"[Cognition] {m}"))
        self.store = Store(db_path)
        self.memory = Memory(self.store)
        self.constitution = (load_constitution(constitution_path) if constitution_path
                             else load_constitution())
        try:
            if register(self.store, self.constitution, rationale="chargée au démarrage"):
                self._log(f"constitution v{self.constitution.version} enregistrée")
        except ConstitutionDrift as e:
            # Signalé, pas bloquant : JARVIS doit démarrer même si on a oublié
            # d'incrémenter le numéro. Le selftest, lui, échoue.
            self._log(f"⚠️ {e}")
        self.session_id = self._new_session()
        # save_memory arrive PENDANT le tour, la transcription de l'utilisateur
        # seulement à turn_complete : la preuve est donc évaluée au tour suivant.
        self._pending_saves: list[tuple[str, str, str]] = []
        self._pending_lock = threading.Lock()
        self._q: queue.Queue = queue.Queue(maxsize=5000)
        self._worker = None
        if background:
            self._worker = threading.Thread(target=self._drain, name="cognition-writer",
                                            daemon=True)
            self._worker.start()
        if legacy_path:
            self._submit(self._import_legacy, legacy_path)
        self._submit(self.memory.purge_raw_turns)

    # ── Plomberie ────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        try:
            self._logger(msg)
        except Exception:
            pass

    @staticmethod
    def _new_session() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def _submit(self, fn, *a, **kw) -> None:
        if self._worker is None:
            fn(*a, **kw)          # mode synchrone (tests)
            return
        try:
            self._q.put_nowait((fn, a, kw))
        except queue.Full:
            self._log("file d'écriture pleine — événement abandonné")

    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, a, kw = item
            try:
                fn(*a, **kw)
            except Exception as e:
                self._log(f"écriture échouée : {type(e).__name__}: {e}")
            finally:
                self._q.task_done()

    def flush(self, timeout: float = 5.0) -> None:
        """Attend que la file soit vide (tests, arrêt propre)."""
        if self._worker is None:
            return
        done = threading.Event()
        self._submit(done.set)
        done.wait(timeout)

    def close(self) -> None:
        self._flush_saves()
        self.flush()
        if self._worker is not None:
            self._q.put(None)
            self._worker.join(timeout=2)
        self.store.close()

    def _import_legacy(self, path: Path) -> None:
        n = self.memory.import_legacy(path)
        if n:
            self._log(f"{n} fait(s) importé(s) de long_term.json (lecture seule)")

    # ── Entrées (hooks main.py) ──────────────────────────────────────────────

    @_safe()
    def observe_turn(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        kind = "user_turn" if role == "user" else "assistant_turn"
        self._submit(self.memory.log_event, kind, session_id=self.session_id,
                     actor=role, summary=text)
        self._flush_saves()

    def _flush_saves(self) -> None:
        with self._pending_lock:
            pending, self._pending_saves = self._pending_saves, []
        for cat, key, value in pending:
            self._submit(self._record_model_save, cat, key, value, self.session_id)

    @_safe()
    def observe_tool(self, name: str, args: dict, result) -> None:
        text = str(result or "")
        failed = text.startswith(("Tool '", "Unknown tool")) or " failed:" in text[:80]
        # Les arguments sont tronqués et jamais les contenus de fichiers : le
        # journal dit ce qui a été fait, pas ce qui a été lu.
        safe_args = {k: (str(v)[:120]) for k, v in (args or {}).items()}
        self._submit(self.memory.log_event, "tool_result", session_id=self.session_id,
                     actor=name, summary=text[:300], payload={"args": safe_args},
                     outcome="error" if failed else "ok")

    @_safe()
    def on_save_memory(self, category: str, key: str, value: str) -> None:
        """Le modèle a appelé save_memory. La V1 a déjà écrit long_term.json ;
        ici on enregistre le même fait AVEC sa provenance, vérifiée contre ce
        que l'utilisateur a réellement dit pendant cette session."""
        if not key or not value:
            return
        with self._pending_lock:
            self._pending_saves.append((category or "notes", str(key), str(value)))

    def _record_model_save(self, category: str, key: str, value: str, sid: str) -> None:
        turns = self.memory.recent_user_turns(sid, limit=8)
        source, ratio = policy.source_for_model_save(value, turns)
        ev = self.memory.log_event("memory_write", session_id=sid, actor="model",
                                   summary=f"{category}/{key}",
                                   payload={"source": source, "evidence": round(ratio, 2)})
        self.memory.add(f"{key.replace('_', ' ')}: {value}", kind="semantic", source=source,
                        slot=slot_for(category, key),
                        importance=0.8 if category == "identity" else 0.6, event_id=ev)
        if source == "model_inferred":
            self._log(f"souvenir non vérifié : {category}/{key} "
                      f"(preuve {ratio:.0%} dans ce que l'utilisateur a dit)")

    @_safe()
    def archive_session(self, summary: str) -> None:
        """Le résumé V1 est consommé au briefing suivant puis effacé. Ici il est
        gardé : c'est la première brique de l'autobiographie."""
        summary = (summary or "").strip()
        self._flush_saves()
        if not summary:
            return
        sid = self.session_id
        self._submit(self._archive, sid, summary)
        self.session_id = self._new_session()

    def _archive(self, sid: str, summary: str) -> None:
        n_turns = self.store.query(
            "SELECT count(*) n FROM events WHERE session_id=? AND kind IN ('user_turn','assistant_turn')",
            (sid,))[0]["n"]
        ev = self.memory.log_event("system", session_id=sid, actor="cognition",
                                   summary="session archivée", payload={"turns": n_turns})
        day = datetime.now().strftime("%Y-%m-%d")
        self.memory.add(f"[{day}] {summary}", kind="episodic", source="model_summary",
                        importance=min(1.0, 0.3 + n_turns / 40), event_id=ev)

    # ── Sorties ──────────────────────────────────────────────────────────────

    @_safe(default="")
    def prompt_block(self) -> str:
        """Ajouté au prompt système à chaque connexion, sous PROMPT_BUDGET."""
        parts = [self.constitution.prompt_core()]
        episodes = self.memory.recent("episodic", limit=3)
        if episodes:
            lines = ["[EARLIER CONVERSATIONS — your own archive, summaries not transcripts]"]
            for e in episodes:
                lines.append("- " + e["content"][:200])
            lines.append("Older ones: call recall_episodes.")
            parts.append("\n".join(lines))
        out = "\n\n".join(parts).strip()
        if len(out) > PROMPT_BUDGET:
            out = out[:PROMPT_BUDGET].rsplit("\n", 1)[0]
        return out + "\n"

    @_safe(default="Mémoire épisodique indisponible.")
    def recall(self, query: str, limit: int = 5) -> str:
        rows = self.memory.search(query or "", limit=limit)
        if not rows:
            return f"Aucun souvenir trouvé pour « {query} »." if query else "Aucun souvenir archivé."
        lines = []
        for r in rows:
            tag = "" if r["verified"] else " [UNVERIFIED — inferred, never confirmed by the user]"
            if r["status"] == "contested":
                tag = " [CONTESTED — contradicted by a later statement]"
            lines.append(f"- ({r['kind']}, {r['created_at'][:10]}) {r['content'][:240]}{tag}")
        return "Memories found:\n" + "\n".join(lines)

    @_safe(default={})
    def stats(self) -> dict:
        ev = self.store.query("SELECT kind, count(*) n FROM events GROUP BY kind")
        return {"session": self.session_id, "constitution": self.constitution.version,
                "events": {r["kind"]: r["n"] for r in ev}, "memories": self.memory.counts()}


def attach(jarvis, db_path: Path | str | None = None,
           legacy_path: Path | None = None) -> Cognition | None:
    """Appelé une fois par JarvisLive.__init__. Mode local uniquement (pour l'instant).
    Les chemins ne servent qu'au selftest, qui ne touche jamais la vraie base."""
    global _active
    ui = getattr(jarvis, "ui", None)
    if not getattr(ui, "local_mode", False):
        return None

    def _log(msg: str) -> None:
        print(f"[Cognition] {msg}")
        try:
            ui.write_log(f"SYS: {msg}")
        except Exception:
            pass

    try:
        _active = Cognition(db_path or DEFAULT_DB, logger=_log,
                            legacy_path=legacy_path or LEGACY_PATH)
    except Exception as e:
        _log(f"désactivée : {type(e).__name__}: {e}")
        _active = None
    return _active
