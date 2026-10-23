"""
incidents/ — les erreurs réelles de Matthew, comptées, pour que la nuit voie ce que les tests ne voient pas.

Les tests de Night Shift ne couvrent que ce qu'on a pensé à tester. Le
2026-10-16, deux vrais problèmes leur avaient échappé : faster-whisper qui
faisait planter le HUD (Qt), et les quotas gratuits qui se fermaient. Ici, les
erreurs de la vraie vie sont comptées, regroupées par signature :

    signature = genre + type d'exception + fichier:fonction du dépôt + message rendu générique

CE QUI EST CAPTÉ (incidents.attach(matthew_assistant), un hook de main.py)
  - les exceptions non rattrapées (sys.excepthook, threading.excepthook) ;
  - les lignes d'erreur du journal de Matthew (« ERR: », « … failed », « échec »).
Rien d'autre : ni la conversation, ni les fichiers, ni l'écran.

LOCAL D'ABORD — memory/incidents.json (ignoré par Git), 30 jours, 300
signatures au plus. L'outil vocal « incidents » les montre.

PARTAGE — désactivé par défaut (réglage incidents_share). Activé, un résumé
des 7 derniers jours part chaque nuit sur la branche nightshift-data, QUI EST
PUBLIQUE : signatures et compteurs seulement, jamais d'échantillon ; secrets,
adresses web, e-mails, chemins personnels et nombres effacés du message.
Night Shift l'affiche dans son rapport, le plus fréquent d'abord — sans le
confier au modèle : aucun contrôle ne prouverait le correctif (principe n°3).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("MATTHEW_ASSISTANT_INCIDENTS_FILE") or REPO / "memory" / "incidents.json")
SCHEMA = "matthew_assistant.incidents/1"
SCHEMAS = (SCHEMA, "jarvis.incidents/1")   # ancien nom, encore lu
KEEP_DAYS = 30
DIGEST_DAYS = 7
MAX_SIGNATURES = 300
DATA_BRANCH = "nightshift-data"

_lock = threading.Lock()

_SECRETS = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z_\-]{20,}|ghp_[0-9A-Za-z]{20,}|"
                      r"github_pat_[0-9A-Za-z_]{20,}|Bearer\s+[0-9A-Za-z._\-]{10,}|eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z._\-]+)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"(https?|wss?|webcal)://([^/\s:'\"]+)[^\s'\"]*")
_WINPATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s'\"]+\\)*[^\\\s'\"]*")
_POSIXPATH = re.compile(r"(?<![\w.])/(?:home|Users|root|tmp|var|mnt)/[^\s'\"]*")
_QUOTED = re.compile(r"(['\"«])([^'\"»]{1,200})(['\"»])")
_NUM = re.compile(r"0x[0-9a-fA-F]+|\d+(?:[.,]\d+)*")     # « 4.0s » comme « 5.2s » : la même erreur
_FILE = re.compile(r"(?<![/\w.<])[\w\-]+(?:\.[\w\-]+)*\.[A-Za-z][A-Za-z0-9]{1,5}\b(?!/)")
ERROR_LINE = re.compile(r"^(ERR:|\[ERR\])|\b(failed|failure|error|échec|échoué|exception|traceback)\b", re.IGNORECASE)


def generic(message: str) -> str:
    """Le message sans rien de personnel ni de variable : deux fois la même erreur → le même texte. Pur."""
    t = _SECRETS.sub("<secret>", str(message or ""))
    t = _EMAIL.sub("<e-mail>", t)
    t = _URL.sub(lambda m: f"{m.group(1)}://{m.group(2)}/…", t)

    def path(m):
        p = m.group(0).replace("\\", "/")
        rel = p.split("/Jarvis_replacement_new/", 1)
        return rel[1] if len(rel) == 2 else "<chemin>"
    t = _WINPATH.sub(path, t)
    t = _POSIXPATH.sub(path, t)
    # Un nom de fichier isolé (« CV secret.docx » : l'espace a coupé le chemin) n'est pas du dépôt : effacé.
    # Ceux du dépôt gardent leur dossier (« meeting/capture.py ») et passent.
    t = _FILE.sub("<fichier>", t)
    t = _QUOTED.sub(lambda m: f"{m.group(1)}…{m.group(3)}", t)
    t = _NUM.sub("N", t)
    return " ".join(t.split())[:200]


def where(tb) -> str:
    """Le dernier endroit du dépôt dans la pile : « dossier/fichier.py:fonction » (stable d'une version à l'autre)."""
    frames = traceback.extract_tb(tb) if tb else []
    for fr in reversed(frames):
        try:
            rel = Path(fr.filename).resolve().relative_to(REPO)
        except (ValueError, OSError):
            continue
        return f"{rel.as_posix()}:{fr.name}"
    return frames[-1].name if frames else ""


def signature(kind: str, exc: str, place: str, message: str) -> str:
    return hashlib.sha1(f"{kind}|{exc}|{place}|{generic(message)}".encode("utf-8")).hexdigest()[:12]


def _load() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") in SCHEMAS:
            return {**data, "schema": SCHEMA}
    except Exception:
        pass
    return {"schema": SCHEMA, "items": {}}


def _save(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_name(f".incidents-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE)
    except OSError:
        pass


def record(kind: str, exc: str, place: str, message: str, now: datetime | None = None) -> str:
    """Compte une occurrence. Rend la signature. Ne lève jamais."""
    try:
        now = now or datetime.now()
        sig = signature(kind, exc, place, message)
        day = now.date().isoformat()
        with _lock:
            st = _load()
            it = st["items"].setdefault(sig, {"kind": kind, "exc": exc, "where": place, "message": generic(message),
                                              "first": now.isoformat(timespec="seconds"), "days": {}})
            it["last"] = now.isoformat(timespec="seconds")
            it["days"][day] = it["days"].get(day, 0) + 1
            cutoff = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
            for s in list(st["items"]):
                st["items"][s]["days"] = {d: n for d, n in st["items"][s]["days"].items() if d >= cutoff}
                if not st["items"][s]["days"]:
                    del st["items"][s]
            if len(st["items"]) > MAX_SIGNATURES:
                keep = sorted(st["items"], key=lambda s: st["items"][s]["last"], reverse=True)[:MAX_SIGNATURES]
                st["items"] = {s: st["items"][s] for s in keep}
            _save(st)
        return sig
    except Exception:
        return ""


def record_exception(exc_type, exc, tb, kind: str = "exception") -> str:
    return record(kind, getattr(exc_type, "__name__", str(exc_type)), where(tb), str(exc))


def record_log(line: str) -> str | None:
    """Une ligne du journal de Matthew qui ressemble à une erreur : comptée. Les autres : rien."""
    text = str(line or "")
    if not ERROR_LINE.search(text):
        return None
    head = re.sub(r"^(SYS|ERR|INFO|WARN):\s*", "", text.strip())
    return record("log", "", "", head)


def digest(now: datetime | None = None, days: int = DIGEST_DAYS) -> dict:
    """Le résumé partageable : signatures et compteurs des derniers jours, le plus fréquent d'abord."""
    now = now or datetime.now()
    since = (now.date() - timedelta(days=days - 1)).isoformat()
    rows = []
    for sig, it in _load()["items"].items():
        n = sum(c for d, c in it["days"].items() if d >= since)
        if n:
            rows.append({"signature": sig, "kind": it["kind"], "exc": it["exc"], "where": it["where"],
                         "message": generic(it["message"]), "count": n, "days": len([d for d in it["days"] if d >= since]),
                         "last": it["last"][:10]})
    rows.sort(key=lambda r: (r["count"], r["days"]), reverse=True)
    return {"schema": SCHEMA, "since": since, "until": now.date().isoformat(), "host": "pc" if sys.platform == "win32" else sys.platform,
            "incidents": rows[:30]}


def summary(limit: int = 8) -> str:
    rows = digest()["incidents"]
    if not rows:
        return "Aucune erreur comptée ces 7 derniers jours."
    lines = [f"{r['count']}× sur {r['days']} jour(s) — {r['exc'] or r['kind']} {r['where']} : {r['message'][:110]}"
             for r in rows[:limit]]
    return "Erreurs réelles, 7 derniers jours (la plus fréquente d'abord) :\n" + "\n".join(lines)


def push(repo_dir: Path = REPO, now: datetime | None = None) -> str | None:
    """Pousse le résumé sur nightshift-data (incidents/latest.json) avec les identifiants git de ce poste.
    None, ou l'erreur. N'est appelé que si le partage est activé."""
    d = digest(now)
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo_dir), capture_output=True,
                            text=True, encoding="utf-8", errors="replace").stdout.strip()
    if not origin:
        return "pas de remote origin"
    tmp = Path(tempfile.mkdtemp(prefix="matthew-assistant-incidents-"))
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=str(tmp), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=120)
        if subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", DATA_BRANCH, origin, str(tmp)],
                          capture_output=True, timeout=180).returncode != 0:
            return "branche nightshift-data inaccessible"
        out = tmp / "incidents"
        out.mkdir(exist_ok=True)
        (out / "latest.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        git("add", "incidents")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return None                                   # rien de neuf
        git("-c", "user.name=matthew-assistant-incidents", "-c", "user.email=incidents@localhost", "commit", "-q", "-m",
            f"incidents : résumé du {d['until']} ({len(d['incidents'])} signature(s))")
        p = git("push", "-q", "origin", f"HEAD:{DATA_BRANCH}")
        return None if p.returncode == 0 else f"push refusé : {generic(p.stderr)[:160]}"
    except Exception as e:
        return f"{type(e).__name__}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


_installed = False


def install_hooks() -> None:
    """Exceptions non rattrapées, du fil principal et des autres fils. Les gestionnaires d'avant sont gardés."""
    global _installed
    if _installed:
        return
    _installed = True
    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def on_sys(t, e, tb):
        record_exception(t, e, tb)
        prev_sys(t, e, tb)

    def on_thread(args):
        if args.exc_type is not SystemExit:
            record_exception(args.exc_type, args.exc_value, args.exc_traceback, kind="thread")
        prev_thread(args)

    sys.excepthook, threading.excepthook = on_sys, on_thread


def attach(matthew_assistant) -> None:
    """Hook de main.py : les exceptions, et les lignes d'erreur du journal de Matthew. Le partage, la nuit, s'il est activé."""
    install_hooks()
    ui = getattr(matthew_assistant, "ui", None)
    original = getattr(ui, "write_log", None)
    if original is not None and not getattr(original, "__incidents__", False):
        def write_log(text, *a, **k):
            try:
                record_log(text)
            except Exception:
                pass
            return original(text, *a, **k)
        write_log.__incidents__ = True
        try:
            ui.write_log = write_log
        except Exception:
            pass
    threading.Thread(target=_nightly, name="incidents-share", daemon=True).start()


def _share_enabled() -> bool:
    try:
        from memory.config_manager import load_api_keys
        return bool((load_api_keys() or {}).get("incidents_share"))
    except Exception:
        return False


def _nightly() -> None:
    """Une fois par nuit (vers 1 h, avant la première passe de Night Shift), si le partage est activé."""
    import time
    done = ""
    while True:
        now = datetime.now()
        if now.hour == 0 and now.minute >= 45 and done != now.date().isoformat():
            done = now.date().isoformat()
            if _share_enabled():
                err = push()
                if err:
                    record("log", "", "incidents/__init__.py:push", f"partage des incidents impossible : {err}")
        time.sleep(60)
