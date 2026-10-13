"""
cognition/runtime.py — ce que main.py voit de la cognition. Rien de plus.

    attach(jarvis) -> Cognition | None

Entrées (hooks d'une ligne dans main.py) :
    observe_turn, observe_tool, on_save_memory, archive_session
Sorties :
    prompt_block()      à chaque connexion
    response["internal_state"]   quand l'état franchit un seuil pendant la session
Pour les plugins :
    recall(), self_report()
Pour les générateurs de secours (IA locales comprises) :
    generate()          le prompt système reçoit la constitution ET l'état affectif

Une seule règle traverse tout le fichier :

    UNE PANNE ICI NE DOIT JAMAIS ATTEINDRE LA CONVERSATION.

  - les écritures passent par une file et un fil de fond (« writer ») ; les
    appels aux modèles par un second fil (« llm »), pour qu'une réponse lente
    de Groq ne retarde pas le journal ;
  - chaque méthode publique est enveloppée dans _safe : une exception devient
    une ligne de journal et une valeur neutre ;
  - attach() renvoie None hors mode local, ou si l'ouverture échoue.
"""
from __future__ import annotations

import functools
import queue
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import affect as A
from . import policy, reflection, selfmodel
from .autonomy import Autonomy
from .constitution import ConstitutionDrift, load as load_constitution, register
from .goals import GoalError, Goals
from .memory import Memory, slot_for
from .store import DEFAULT_DB, Store

BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_PATH = BASE_DIR / "memory" / "long_term.json"

# Budget du bloc ajouté au prompt système à chaque connexion (mesuré par le
# selftest). Le projet surveille ses coûts de tokens : on n'ajoute que ce qui
# change le comportement.
PROMPT_BUDGET = 1100

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
                self._log(f"{fn.__name__} a échoué ({type(e).__name__}: {e})")
                if self.debug:
                    traceback.print_exc()
                return default() if callable(default) else default
        return wrapper
    return deco


#: Combien de temps un objectif reste « en cours » après une note de progrès.
GOAL_FOCUS_S = 600.0
#: Ces outils ne servent pas un objectif : se regarder, se souvenir, répondre.
GOAL_NEUTRAL_TOOLS = {"goals", "self_state", "capabilities", "recall_memory", "recall_episodes",
                      "close_camera", "usage_report", "save_memory"}


class _Lane:
    """Un fil de fond et sa file. Synchrone si `background=False` (tests)."""

    def __init__(self, name: str, background: bool, log):
        self._log = log
        self._q: queue.Queue = queue.Queue(maxsize=5000)
        self._t = None
        if background:
            self._t = threading.Thread(target=self._drain, name=name, daemon=True)
            self._t.start()

    def submit(self, fn, *a, **kw) -> None:
        if self._t is None:
            try:
                fn(*a, **kw)
            except Exception as e:
                self._log(f"tâche échouée : {type(e).__name__}: {e}")
            return
        try:
            self._q.put_nowait((fn, a, kw))
        except queue.Full:
            self._log("file pleine — tâche abandonnée")

    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, a, kw = item
            try:
                fn(*a, **kw)
            except Exception as e:
                self._log(f"tâche échouée : {type(e).__name__}: {e}")

    def flush(self, timeout: float) -> None:
        if self._t is None:
            return
        done = threading.Event()
        self.submit(done.set)
        done.wait(timeout)

    def stop(self) -> None:
        if self._t is not None:
            self._q.put(None)
            self._t.join(timeout=2)


class Cognition:
    def __init__(self, db_path: Path | str = DEFAULT_DB, *, logger=None,
                 legacy_path: Path | None = LEGACY_PATH, background: bool = True,
                 constitution_path: Path | None = None, chain=None, embedder=None,
                 clock=time.time, debug: bool = False):
        self.debug = debug
        self._logger = logger or (lambda m: print(f"[Cognition] {m}"))
        self._clock = clock
        self.store = Store(db_path)
        self.memory = Memory(self.store)
        self.goals = Goals(self.store)
        self.autonomy = Autonomy(self)
        self.constitution = (load_constitution(constitution_path) if constitution_path
                             else load_constitution())
        try:
            if register(self.store, self.constitution, rationale="chargée au démarrage"):
                self._log(f"constitution v{self.constitution.version} enregistrée")
        except ConstitutionDrift as e:
            # Signalé, pas bloquant : le selftest, lui, échoue.
            self._log(f"⚠️ {e}")
        self._chain = chain          # None = construite à la demande ; False = aucune
        # Même règle pour les vecteurs ; sans chaîne (tests, évaluations), aucun.
        # Une chaîne injectée (tests, évaluations) n'emmène pas de vecteurs avec elle.
        self._embedder = embedder if embedder is not None else (None if chain is None else False)
        self.tool_names = lambda: []
        self.session_id = self._new_session()
        self._turns_this_session = 0
        # save_memory arrive PENDANT le tour, la transcription de l'utilisateur
        # seulement à turn_complete : la preuve est évaluée au tour suivant.
        self._pending_saves: list[tuple[str, str, str]] = []
        self._pending_lock = threading.Lock()
        # L'objectif en cours : celui dont le modèle vient de noter un progrès.
        # Les outils réellement appelés dans la fenêtre comptent dans SON budget.
        self._goal_focus: tuple[str, float] | None = None
        self._auto_since_progress = 0
        self._affect_lock = threading.Lock()
        self.affect = self._load_affect()
        self._announced: set[str] = set(A.directives(self.affect))
        self._writer = _Lane("cognition-writer", background, self._log)
        self._llm = _Lane("cognition-llm", background, self._log)
        if legacy_path:
            self._writer.submit(self._import_legacy, legacy_path)
        self._writer.submit(self.memory.purge_raw_turns)

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
        self._writer.submit(fn, *a, **kw)

    def flush(self, timeout: float = 10.0) -> None:
        """Attend que les deux files soient vides (tests, arrêt propre)."""
        self._writer.flush(timeout)
        self._llm.flush(timeout)
        self._writer.flush(timeout)

    def close(self) -> None:
        self._flush_saves()
        self.flush()
        if getattr(self, "sync", None) is not None:
            self.sync.stop()
        self._writer.stop()
        self._llm.stop()
        self.store.close()

    def _meta_get(self, key: str) -> str | None:
        rows = self.store.query("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def _meta_set(self, key: str, value: str) -> None:
        self.store.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))

    @property
    def embedder(self):
        if self._embedder is None:
            from .embeddings import default
            self._embedder = default()
        return self._embedder or None

    @property
    def chain(self):
        if self._chain is None:
            from .providers import ProviderChain
            self._chain = ProviderChain.from_config()
        return self._chain or None

    def _import_legacy(self, path: Path) -> None:
        n = self.memory.import_legacy(path)
        if n:
            self._log(f"{n} fait(s) importé(s) de long_term.json (lecture seule)")

    # ── Affect ───────────────────────────────────────────────────────────────

    def _load_affect(self) -> A.AffectState:
        """Relu au démarrage et décru du temps écoulé : un redémarrage n'efface
        pas l'état, il le laisse retomber comme l'aurait fait le temps."""
        st = A.AffectState(at=self._clock())
        rows = self.store.query("SELECT var, value, updated_at FROM affect_state")
        if rows:
            st.at = min(r["updated_at"] for r in rows)
            for r in rows:
                if r["var"] in A.VARS:
                    st.values[r["var"]] = A.clamp(r["var"], r["value"])
        return A.decayed(st, self._clock())

    def _appraise(self, events: list[str], tool: str | None = None) -> list[str]:
        """Applique les règles ; renvoie les directives NOUVELLEMENT actives."""
        if not events:
            return []
        with self._affect_lock:
            self.affect, log = A.apply(self.affect, events, self._clock(), tool)
            snapshot = self.affect.copy()
            active = set(A.directives(snapshot))
            fresh = sorted(active - self._announced, key=list(A.THRESHOLDS).index)
            self._announced = active
        if log:
            self._submit(self._persist_affect, snapshot, log)
        return fresh

    def _persist_affect(self, st: A.AffectState, log) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store.transaction() as c:
            c.executemany("INSERT OR REPLACE INTO affect_state(var, value, updated_at) "
                          "VALUES (?,?,?)", [(k, v, st.at) for k, v in st.values.items()])
            c.executemany("INSERT INTO affect_log(ts, var, before, after, rule) VALUES (?,?,?,?,?)",
                          [(ts, var, b, a, rule) for var, b, a, rule in log])

    def current_affect(self) -> A.AffectState:
        with self._affect_lock:
            self.affect = A.decayed(self.affect, self._clock())
            return self.affect.copy()

    def _state_text(self, st: A.AffectState | None = None) -> str:
        st = st or self.current_affect()
        lines = ["[INTERNAL STATE — computed variables, not feelings. If asked how you "
                 "feel, call self_state and report these values honestly.]",
                 A.describe(st)]
        for key in A.directives(st):
            lines.append("Guidance: " + A.ALLOWED_DIRECTIVES[key])
        return "\n".join(lines)

    # ── Entrées (hooks main.py) ──────────────────────────────────────────────

    @_safe()
    def observe_turn(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        kind = "user_turn" if role == "user" else "assistant_turn"
        self._submit(self.memory.log_event, kind, session_id=self.session_id,
                     actor=role, summary=text)
        if role == "user":
            events = A.classify_user_turn(text)
            if self._turns_this_session == 0:
                events.insert(0, "session_start")
            self._turns_this_session += 1
            self._appraise(events)
        self._flush_saves()

    def _flush_saves(self) -> None:
        with self._pending_lock:
            pending, self._pending_saves = self._pending_saves, []
        for cat, key, value in pending:
            self._submit(self._record_model_save, cat, key, value, self.session_id)

    @_safe()
    def observe_tool(self, name: str, args: dict, result, response: dict | None = None) -> None:
        text = str(result or "")
        events = A.events_for_tool(name, text, self.affect)
        failed = "tool_error" in events
        # Arguments tronqués, jamais le contenu des fichiers : le journal dit ce
        # qui a été fait, pas ce qui a été lu.
        safe_args = {k: (str(v)[:120]) for k, v in (args or {}).items()}
        self._submit(self.memory.log_event, "tool_result", session_id=self.session_id,
                     actor=name, summary=text[:300], payload={"args": safe_args},
                     outcome="error" if failed else "ok")
        if events:          # les outils neutres (close_camera, self_state) ne comptent pas
            self._submit(selfmodel.record_outcome, self.store, name, not failed)
            if self._goal_focus:
                # Synchrone, et seulement quand un objectif est en cours : la note
                # de progrès qui suit doit voir ce compte, sinon l'action compte
                # deux fois (l'autonomie note juste après chaque outil).
                self._count_for_goal(name)
        fresh = self._appraise(events, tool=name)
        # Le prompt système est figé à la connexion. Le seul canal qui atteint
        # le modèle pendant la session sans déclencher de prise de parole est la
        # réponse d'outil : on n'y ajoute l'état QUE quand un seuil vient d'être
        # franchi, pour ne pas payer des tokens à chaque appel.
        if fresh and isinstance(response, dict):
            response["internal_state"] = " ".join(A.ALLOWED_DIRECTIVES[k] for k in fresh)

    @_safe()
    def on_save_memory(self, category: str, key: str, value: str) -> None:
        """Le modèle a appelé save_memory. La V1 a déjà écrit long_term.json ;
        ici le même fait est enregistré AVEC sa provenance, vérifiée contre ce
        que l'utilisateur a réellement dit."""
        if not key or not value:
            return
        with self._pending_lock:
            self._pending_saves.append((category or "notes", str(key), str(value)))

    def _record_model_save(self, category: str, key: str, value: str, sid: str) -> None:
        turns = self.memory.recent_user_turns(sid, limit=8)
        verdict, ratio = policy.lexical_verdict(value, turns)
        if verdict == "unclear" and (self.embedder or self.chain):
            # Probablement une traduction (« pastries » pour « pâtisseries ») :
            # le sens d'abord (local), un AUTRE modèle seulement si le sens hésite.
            self._llm.submit(self._weigh_unclear, category, key, value, sid, turns, ratio)
            return
        source = "user_stated" if verdict == "supported" else "model_inferred"
        self._store_fact(category, key, value, sid, source, ratio, f"lexical:{verdict}")

    def _weigh_unclear(self, category, key, value, sid, turns, ratio) -> None:
        """Les éléments précis ont été dits, pas les mots ordinaires. Le vecteur
        tranche les cas nets sur ce PC ; seul l'entre-deux coûte un appel."""
        from . import embeddings as E
        sim = self.embedder.best_match(value, turns) if self.embedder else None
        if sim is not None and sim >= E.SAME_MEANING:
            self._submit(self._store_fact, category, key, value, sid, "user_stated", ratio,
                         f"semantic:{sim:.2f}", policy.JUDGED_CONFIDENCE)
        elif sim is not None and sim < E.UNRELATED:
            self._submit(self._store_fact, category, key, value, sid, "model_inferred", ratio,
                         f"semantic:{sim:.2f}")
        elif self.chain:
            self._judge_then_store(category, key, value, sid, turns, ratio)
        else:
            self._submit(self._store_fact, category, key, value, sid, "model_inferred", ratio,
                         "lexical:unclear")

    def index_embeddings(self, limit: int = 64) -> int:
        """Vectorise les souvenirs rappelables qui n'en ont pas encore."""
        emb = self.embedder
        if not emb:
            return 0
        statuses = ",".join(f"'{s}'" for s in policy.RECALLABLE)
        rows = self.store.query(
            f"SELECT m.id, m.content FROM memories m LEFT JOIN memory_embeddings e ON e.memory_id = m.id "
            f"WHERE e.memory_id IS NULL AND m.status IN ({statuses}) ORDER BY m.created_at DESC LIMIT ?",
            (limit,))
        if not rows:
            return 0
        got = emb.embed([r["content"] for r in rows])
        if not got:
            return 0
        from .embeddings import pack
        model, vecs = got
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store.transaction() as c:
            for r, v in zip(rows, vecs):
                c.execute("INSERT OR REPLACE INTO memory_embeddings(memory_id, model, dim, vec, ts) "
                          "VALUES (?,?,?,?,?)", (r["id"], model, len(v), pack(v), ts))
        return len(rows)

    def _judge_then_store(self, category, key, value, sid, turns, ratio) -> None:
        method, ok = "judge:unavailable", None
        try:
            prompt = policy.JUDGE_PROMPT.format(
                today=datetime.now().strftime("%Y-%m-%d"),
                said="\n".join(f"- {t}" for t in turns) or "(nothing)",
                fact=f"{key.replace('_', ' ')}: {value}")
            text, used = self.chain.complete(prompt, max_tokens=300, temperature=0,
                                             exclude=("local",))
            ok = policy.parse_judgement(text)
            method = f"judge:{used}"
        except Exception as e:
            self._log(f"second avis indisponible ({type(e).__name__})")
        source = "user_stated" if ok else "model_inferred"
        conf = policy.JUDGED_CONFIDENCE if ok else None
        self._submit(self._store_fact, category, key, value, sid, source, ratio, method, conf)

    def _store_fact(self, category, key, value, sid, source, ratio, method, conf=None) -> None:
        ev = self.memory.log_event("memory_write", session_id=sid, actor="model",
                                   summary=f"{category}/{key}",
                                   payload={"source": source, "evidence": round(ratio, 2),
                                            "method": method})
        self.memory.add(f"{key.replace('_', ' ')}: {value}", kind="semantic", source=source,
                        slot=slot_for(category, key), confidence=conf,
                        importance=0.8 if category == "identity" else 0.6, event_id=ev)
        if source == "model_inferred":
            self._log(f"souvenir non vérifié : {category}/{key} ({method})")
        if self._embedder is not False:
            self._llm.submit(self.index_embeddings)

    @_safe()
    def archive_session(self, summary: str) -> None:
        """Le résumé V1 est consommé au briefing suivant puis effacé. Ici il est
        gardé : la première brique de l'autobiographie."""
        summary = (summary or "").strip()
        self._flush_saves()
        if not summary:
            return
        sid = self.session_id
        self._submit(self._archive, sid, summary)
        self.session_id = self._new_session()
        self._turns_this_session = 0

    def _archive(self, sid: str, summary: str) -> None:
        n_turns = self.store.query(
            "SELECT count(*) n FROM events WHERE session_id=? AND kind IN ('user_turn','assistant_turn')",
            (sid,))[0]["n"]
        ev = self.memory.log_event("system", session_id=sid, actor="cognition",
                                   summary="session archivée", payload={"turns": n_turns})
        st = self.current_affect()
        day = datetime.now().strftime("%Y-%m-%d")
        self.memory.add(f"[{day}] {summary}", kind="episodic", source="model_summary",
                        importance=min(1.0, 0.3 + n_turns / 40), event_id=ev,
                        valence=st.values["valence"], arousal=st.values["arousal"])
        if self.chain and reflection.due(self.memory, self._meta_get):
            self._llm.submit(self._reflect)

    def _reflect(self) -> None:
        def gen(prompt: str) -> str:
            return self.generate(prompt, quality=True, max_tokens=700, with_state=False)

        report = reflection.run(self.memory, gen, lambda k, v: self._submit(self._meta_set, k, v))
        self._log(f"réflexion : {report}")

    # ── Sorties ──────────────────────────────────────────────────────────────

    @_safe(default="")
    def prompt_block(self) -> str:
        """Ajouté au prompt système à chaque connexion, sous PROMPT_BUDGET.
        Les faits non vérifiés et les réflexions n'y entrent jamais."""
        parts = [self.constitution.prompt_core(), self._state_text()]
        goal_lines = self.goals.prompt_lines()
        if goal_lines:
            parts.append("\n".join(goal_lines))
        weak = selfmodel.weak_spots(self.store)[:2]
        if weak:
            parts.append("[SELF-KNOWLEDGE — measured, not guessed] Recently unreliable: " + "; ".join(
                f"{w['tool']} (expected success {w['expected']:.0%} over {w['n']} tries)"
                for w in weak) + ". Say so before relying on them; prefer another way if one exists.")
        auto_lines = self.autonomy.prompt_lines()
        if auto_lines:
            parts.append("\n".join(auto_lines))
        # Les épisodes « external » (pages web lues par l'autonomie) ne sont pas
        # l'histoire de la conversation : ils restent dans recall_episodes.
        episodes = [e for e in self.memory.recent("episodic", limit=8)
                    if e["source"] != "external"][:3]
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

    # ── La mémoire du prompt : une seule source ──────────────────────────────
    #
    # main.py mettait long_term.json (V1) dans le prompt. Deux défauts : un
    # souvenir rejeté dans ESPRIT y restait, et un fait non vérifié y entrait
    # par la V1. La cognition est maintenant la source ; long_term.json reste
    # écrit (et réimporté) pour la compatibilité, mais n'est plus lu ici.

    _PROMPT_STATUSES = ("active", "validated")
    _CAT_LABELS = {"preferences": "Preferences", "projects": "Projects",
                   "relationships": "Relationships", "wishes": "Wishes", "notes": "Notes"}

    @_safe(default="")
    def memory_prompt_block(self, core_chars: int = 900, index_chars: int = 420,
                            per_category: int = 4) -> str:
        rows = [dict(r) for r in self.store.query(
            "SELECT slot, content, updated_at, created_at FROM memories WHERE kind='semantic' "
            "AND slot IS NOT NULL AND status IN ('active','validated') "
            "ORDER BY COALESCE(updated_at, created_at) DESC, rowid DESC")]
        if not rows:
            return ""

        def value(r):
            text = r["content"]
            return text.split(":", 1)[1].strip() if ":" in text else text

        def label(r):
            return r["slot"].split("/", 1)[-1].replace("_", " ").title()

        seen, core, shown, overflow, used = set(), [], {}, [], 0
        for r in rows:
            if r["slot"] in seen:
                continue
            seen.add(r["slot"])
            cat = r["slot"].split("/", 1)[0]
            if cat == "identity":
                if r["slot"] == "identity/language":
                    # comme la V1 : une observation, pas un ordre de langue
                    core.append(f"Has spoken to you in: {value(r)[:40]} (an observation about the "
                                "past — always answer in the language of their CURRENT message)")
                else:
                    core.append(f"{label(r)}: {value(r)[:200]}")
                continue
            line = f"  - {label(r)}: {value(r)}"
            if len(shown.get(cat, [])) < per_category and used + len(line) + 1 <= core_chars:
                shown.setdefault(cat, []).append(line)
                used += len(line) + 1
            else:
                overflow.append(label(r))
        out = ["[WHAT YOU KNOW ABOUT THIS PERSON — verified memories only; use naturally, "
               "never recite like a list]", *core]
        for cat, lab in self._CAT_LABELS.items():
            if shown.get(cat):
                out += ["", f"{lab}:", *shown[cat]]
        for cat in sorted(set(shown) - set(self._CAT_LABELS)):
            out += ["", f"{cat.title()}:", *shown[cat]]
        if overflow:
            names, budget = [], index_chars
            for n in overflow:
                if budget - len(n) - 2 < 0:
                    break
                names.append(n)
                budget -= len(n) + 2
            more = len(overflow) - len(names)
            out += ["", "[ALSO REMEMBERED — values not shown here. Call recall_memory with a keyword "
                        "to read any of these before saying you do not know]",
                    ", ".join(names) + (f" (+{more} more)" if more else "")]
        return "\n".join(out) + "\n"

    @_safe(default="")
    def recall_facts(self, query: str, limit: int = 8) -> str:
        """recall_memory sur la mémoire cognitive : les faits, vérifiés d'abord."""
        rows = [r for r in self.memory.search(query or "", limit=limit * 2, kinds=("semantic",),
                                               embedder=self.embedder)][:limit]
        if not rows:
            return (f"Nothing stored about '{query}'." if query else "No stored facts yet.")
        lines = []
        for r in rows:
            tag = "" if r["verified"] else f" [{r['status'].upper()} — not verified, say so]"
            lines.append(f"- {r['content']}{tag}")
        return "\n".join(lines)

    @_safe(default="Mémoire épisodique indisponible.")
    def recall(self, query: str, limit: int = 5) -> str:
        rows = self.memory.search(query or "", limit=limit, embedder=self.embedder)
        if not rows:
            return f"Aucun souvenir trouvé pour « {query} »." if query else "Aucun souvenir archivé."
        lines = []
        for r in rows:
            tag = ""
            if r["kind"] == "reflective":
                tag = " [REFLECTION — your own synthesis, never confirmed]"
            elif r["source"] == "external":
                tag = " [FROM THE WEB — third-party content read during autonomous work, not verified]"
            elif r["status"] == "contested":
                tag = " [CONTESTED — contradicted by a later statement]"
            elif not r["verified"]:
                tag = " [UNVERIFIED — inferred, never confirmed by the user]"
            lines.append(f"- ({r['kind']}, {r['created_at'][:10]}) {r['content'][:240]}{tag}")
        return "Memories found:\n" + "\n".join(lines)

    @_safe(default="Self-state unavailable.")
    def self_report(self, about: str = "") -> str:
        """Ce que JARVIS peut dire de lui-même, lu dans les données, pas imaginé.
        `about` : un outil dont on veut la fiabilité mesurée."""
        st = self.current_affect()
        causes = self.store.query(
            "SELECT ts, var, before, after, rule FROM affect_log ORDER BY id DESC LIMIT 6")
        counts = self.memory.counts()
        unverified = sum(v.get("proposed", 0) for v in counts.values())
        contested = sum(v.get("contested", 0) for v in counts.values())
        tools = sorted(self.tool_names() or [])
        rel = selfmodel.reliability(self.store)
        cal = selfmodel.tool_calibration(self.store)
        mcal = selfmodel.memory_calibration(self.store)
        out = [
            "These are recorded variables computed by rules from events — not felt emotions.",
            "State: " + "; ".join(f"{n} {st.values[n]:.2f} ({A._word(n, st.values[n])})" for n in A.VARS),
            "Active guidance: " + (", ".join(A.directives(st)) or "none"),
            "Recent causes: " + ("; ".join(
                f"{r['var']} {r['before']:.2f}->{r['after']:.2f} after {r['rule']}" for r in causes)
                or "none recorded"),
            f"Memory: {sum(sum(v.values()) for v in counts.values())} items, {unverified} unverified, "
            f"{contested} contested.",
            "Goals: " + ("; ".join(self.goals.describe(g) for g in self.goals.open_goals())
                         or "none open"),
            f"Constitution v{self.constitution.version}. Tools loaded: {len(tools)}.",
        ]
        if about:
            hit = next((r for r in rel if r["tool"] == about.strip()), None)
            out.append(f"Tool {about}: " + (
                f"expected success {hit['expected']:.0%} over {hit['n']} recorded tries"
                if hit else "no recorded tries yet"))
        weak = [r for r in rel if r["weak"]]
        if weak:
            out.append("Measured weak spots: " + ", ".join(
                f"{r['tool']} {r['expected']:.0%}" for r in weak))
        if cal["n"]:
            out.append(f"Self-prediction calibration on {cal['n']} tool calls: Brier {cal['brier']}, "
                       f"ECE {cal['ece']} (0 = perfect).")
        if mcal["n"]:
            out.append(f"Memory confidence vs the user's verdicts ({mcal['n']} reviews): "
                       f"Brier {mcal['brier']}.")
        out.append("Hard limits (enforced by code): " + " ".join(selfmodel.HARD_LIMITS))
        out.append("This describes the architecture's records; it is not evidence of what the "
                   "language model itself experiences or can introspect.")
        return "\n".join(out)

    def hud(self, op: str, data: dict | None = None) -> dict:
        """Le panneau ESPRIT (clics de l'utilisateur uniquement, cf. hud.py)."""
        from . import hud
        return hud.call(self, op, data)

    # ── Objectifs (appelés par plugins/cognition_goals.py) ───────────────────
    # Ce que le modèle peut faire seul : proposer, noter un progrès, retirer sa
    # proposition. approve/complete/cancel ne sont appelés que par la fonction
    # que core/confirm.py exécute quand l'utilisateur appuie sur CONFIRMER.

    def goal_propose(self, title: str, justification: str, success_criteria: str,
                     priority: int = 3, deadline: str | None = None,
                     budget_actions: int | None = None) -> dict:
        turns = self.memory.recent_user_turns(self.session_id, limit=6)
        said = policy.lexical_verdict(title, turns)[0] == "supported"
        g = self.goals.propose(title, justification=justification,
                               success_criteria=success_criteria, priority=priority,
                               deadline=deadline, budget_actions=budget_actions,
                               origin="user" if said else "jarvis")
        self._submit(self.memory.log_event, "system", session_id=self.session_id,
                     actor="goals", summary=f"objectif proposé : {g['title']}")
        return g

    def goal_progress(self, ref: str, note: str) -> dict:
        g = self.goal_find(ref)
        # Déjà compté par les vrais appels d'outils ? La note ne recompte pas.
        counted = bool(self._goal_focus and self._goal_focus[0] == g["id"] and self._auto_since_progress)
        after = self.goals.progress(g["id"], note, count=not counted)
        self._goal_focus = (g["id"], time.monotonic()) if after["status"] == "active" else None
        self._auto_since_progress = 0
        if after["status"] == "blocked":
            self._appraise(["goal_blocked"])
        return after

    def _count_for_goal(self, tool: str) -> None:
        """Un vrai appel d'outil pendant qu'un objectif est en cours : il compte
        dans son budget (limite de README : le budget ne comptait que ce que le
        modèle déclarait)."""
        if not self._goal_focus or tool in GOAL_NEUTRAL_TOOLS:
            return
        gid, since = self._goal_focus
        if time.monotonic() - since > GOAL_FOCUS_S:
            self._goal_focus, self._auto_since_progress = None, 0
            return
        after = self.goals.count_action(gid, tool)
        if after is None or after["status"] != "active":
            self._goal_focus, self._auto_since_progress = None, 0
            if after is not None and after["status"] == "blocked":
                self._appraise(["goal_blocked"])
            return
        self._auto_since_progress += 1

    def goal_withdraw(self, ref: str) -> dict:
        return self.goals.withdraw(self.goal_find(ref)["id"])

    def goal_approve(self, gid: str) -> str:
        return self.goals.approve(gid)

    def goal_complete(self, gid: str, evidence: str = "") -> str:
        out = self.goals.complete(gid, evidence)
        if out.startswith("Objectif atteint"):
            self._appraise(["goal_done"])
        return out

    def goal_cancel(self, gid: str, reason: str = "") -> str:
        return self.goals.cancel(gid, reason)

    def forget_preview(self, subject: str) -> list[dict]:
        return self.memory.about(subject)

    def forget_about(self, subject: str) -> str:
        """Appelé UNIQUEMENT après CONFIRMER (plugins/cognition_forget.py) ou
        depuis le panneau ESPRIT : c'est irréversible."""
        n = self.memory.forget_about(subject, session_id=self.session_id)
        return f"{n} souvenir(s) effacé(s) au sujet de « {subject[:60]} »."

    def goal_find(self, ref: str) -> dict:
        g = self.goals.find(ref)
        if not g:
            raise GoalError(f"Aucun objectif ne correspond à « {ref} ».")
        return g

    def generate(self, prompt: str, *, system: str = "", quality: bool = False,
                 max_tokens: int = 400, with_state: bool = True) -> str:
        """Générateurs de secours, IA locale comprise. Le prompt système reçoit
        la constitution et, par défaut, l'état affectif courant : le même JARVIS,
        quel que soit le modèle qui répond. `quality=True` écarte le petit
        modèle local (fiabilité mesurée insuffisante pour juger)."""
        if not self.chain:
            raise RuntimeError("aucun générateur configuré")
        sys_parts = [self.constitution.prompt_core()]
        if with_state:
            sys_parts.append(self._state_text())
        if system:
            sys_parts.append(system)
        text, _ = self.chain.complete(prompt, system="\n\n".join(sys_parts),
                                      max_tokens=max_tokens,
                                      exclude=("local",) if quality else ())
        return text

    @_safe(default=dict)
    def stats(self) -> dict:
        ev = self.store.query("SELECT kind, count(*) n FROM events GROUP BY kind")
        return {"session": self.session_id, "constitution": self.constitution.version,
                "events": {r["kind"]: r["n"] for r in ev}, "memories": self.memory.counts(),
                "affect": {k: round(v, 3) for k, v in self.current_affect().values.items()}}


def attach(jarvis, db_path: Path | str | None = None,
           legacy_path: Path | None = None, chain=None) -> Cognition | None:
    """Appelé une fois par JarvisLive.__init__. Mode local uniquement (pour l'instant).
    Les paramètres ne servent qu'au selftest, qui ne touche jamais la vraie base."""
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
                            legacy_path=legacy_path or LEGACY_PATH, chain=chain)
    except Exception as e:
        _log(f"désactivée : {type(e).__name__}: {e}")
        _active = None
        return None

    def _tools() -> list[str]:
        names = []
        for reg in ("_action_registry", "_plugin_registry"):
            try:
                names += [d["name"] for d in getattr(jarvis, reg).get_tool_declarations()]
            except Exception:
                pass
        return names

    _active.tool_names = _tools

    def _run_tool(name: str, args: dict) -> str:
        # Seul autonomy.SAFE_TOOLS arrive ici (vérifié avant l'appel) ; même
        # chemin que main._execute_tool pour une action, sans voix.
        return jarvis._action_registry.run(name, dict(args), {
            "player": ui, "speak": None, "response": None, "session_memory": None})

    _active.autonomy.tool_runner = _run_tool
    _active.autonomy.start()

    # Les souvenirs déjà en base reçoivent leur vecteur, en fond, par lots.
    def _backfill(cog=_active) -> None:
        total = 0
        for _ in range(50):
            n = cog.index_embeddings()
            total += n
            if not n:
                break
        if total:
            cog._log(f"{total} souvenir(s) vectorisé(s) pour le rappel par le sens")
    _active._llm.submit(_backfill)

    # Une seule mémoire sur tous les PC (cognition/sync.py), OneDrive par défaut.
    _active.sync = None
    if db_path is None:                   # jamais dans le selftest
        try:
            import json as _json
            from config import api_key_path
            from .sync import MemorySync, default_folder
            cfg = _json.loads(Path(api_key_path(Path(__file__).resolve().parent.parent))
                              .read_text(encoding="utf-8-sig"))
            folder = default_folder(cfg if isinstance(cfg, dict) else {})
            if folder:
                _active.sync = MemorySync(_active.store, _active.memory, folder, log=_log)
                _active.sync.start(on_change=lambda n: _active._llm.submit(_active.index_embeddings))
                _log(f"mémoire partagée avec vos autres PC : {folder} (ce PC : {_active.sync.device})")
        except Exception as e:
            _log(f"mémoire partagée indisponible : {type(e).__name__}: {e}")
    return _active
