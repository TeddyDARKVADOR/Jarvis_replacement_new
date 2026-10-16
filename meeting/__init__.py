"""
meeting/ — le copilote de réunion : il entend la visio, quelle que soit
l'application, et rend un compte rendu, des rappels et des objectifs.

Micro ou caméra pris par une autre application (detect.py) → JARVIS capte le
son du poste et le micro (capture.py), les transcrit sur ce poste au fil de la
réunion (transcribe.py), puis, la réunion finie (watcher.py), demande un
compte rendu à un modèle (summarize.py, brain.py). Le compte rendu et la
transcription sont écrits dans Documents/JARVIS/Réunions ; le son n'est jamais
enregistré. Les actions datées de l'utilisateur deviennent des rappels (outil
reminder), les autres des objectifs PROPOSÉS (outil goals : l'utilisateur
approuve sur le HUD).

DEUX POINTS D'ENTRÉE (paquet racine, contrat V2)
  - main.py (mode local) : `attach(jarvis)`. Tout se passe dans ce processus.
  - client_desktop (HUD relié à Oracle) : `Copilot` créé par app.py. Le poste
    capte et transcrit ; Oracle résume et livre (server/meeting_api.py,
    POST /api/meeting/summary) — la clé ne quitte jamais le serveur.
  Supprimer le dossier : plus de copilote, rien d'autre ne change.

RÉGLAGES (config/api_keys.json, ou settings.json du client)
  meeting_mode          auto (défaut) · ask · off
  meeting_dir           dossier des comptes rendus (défaut Documents/JARVIS/Réunions)
  meeting_whisper_model small (défaut) · base · medium…
  meeting_language      fr, en… (défaut : détection automatique)

L'outil vocal est plugins/meeting.py : « je suis en réunion », « arrête les
notes », « où en est la réunion ? », « résume la dernière réunion ».
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from meeting import detect
from meeting.summarize import mine, to_markdown
from meeting.transcribe import Line
from meeting.watcher import Watcher

POLL_S = 3.0
_copilot: "Copilot | None" = None


def default_dir() -> Path:
    return Path.home() / "Documents" / "JARVIS" / "Réunions"


def _safe(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_'" else " " for c in name).strip()
    return " ".join(keep.split())[:60] or "Réunion"


class Session:
    def __init__(self, manual: bool, reason: str, capture, transcriber):
        self.started = datetime.now()
        self.t0 = time.monotonic()
        self.manual, self.reason = manual, reason
        self.capture, self.transcriber = capture, transcriber
        self.ended: float | None = None

    def duration(self) -> float:
        return (self.ended or time.monotonic()) - self.t0     # figée à l'arrêt : le résumé prend du temps

    def silent_for(self) -> float:
        lines = self.transcriber.lines
        last = max((x.t for x in lines), default=0.0)
        return self.duration() - last


class Copilot:
    """Le veilleur, la session en cours et le dernier compte rendu."""

    def __init__(self, settings: Callable[[], dict], summarize: Callable[[str, datetime, float], dict],
                 deliver: Callable[[dict, Path | None, str], None], notify: Callable[[str, str], None],
                 log: Callable[[str], None] = print, detect_fn=detect.in_use, capture_factory=None,
                 transcriber_factory=None, poll_s: float = POLL_S, clock=time.monotonic,
                 save_setting: Callable[[str, str], None] | None = None):
        self.settings, self._summarize, self._deliver, self._notify, self.log = settings, summarize, deliver, notify, log
        self.save_setting = save_setting
        self.mode_override = ""
        self._detect = detect_fn
        self._capture_factory = capture_factory
        self._transcriber_factory = transcriber_factory
        self.poll_s, self.clock = poll_s, clock
        cfg = self._cfg()
        self.watcher = Watcher(mode=str(cfg.get("meeting_mode") or "auto"))
        self.session: Session | None = None
        self.last: dict = {}
        self.usage = detect.Usage()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.finalizing = False

    def _cfg(self) -> dict:
        try:
            return dict(self.settings() or {})
        except Exception:
            return {}

    # ── veille ──
    def watch(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="meeting-watch", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:                       # la veille ne meurt pas
                self.log(f"SYS: réunion — veille : {type(e).__name__}: {e}")
            self._stop.wait(self.poll_s)

    def tick(self) -> str | None:
        self.watcher.mode = self.mode_override or str(self._cfg().get("meeting_mode") or "auto")
        if self.watcher.mode not in ("auto", "ask", "off"):
            self.watcher.mode = "auto"
        self.usage = self._detect()
        recording = self.session is not None
        in_use = self.usage.any
        if recording and self.usage.source == "macos":    # notre capture occupe le micro : la caméra seule
            in_use = bool(self.usage.camera)
        action = self.watcher.step(self.clock(), in_use, recording,
                                   self.session.silent_for() if self.session else 0.0)
        if action == "start":
            self.start(manual=False, reason=self.usage.describe())
        elif action == "ask":
            self._notify("ask", f"Réunion détectée ({self.usage.describe()}). Dis « prends des notes » "
                                "et je fais le compte rendu.")
        elif action == "stop":
            self.stop("fin détectée")
        return action

    # ── session ──
    def start(self, manual: bool = True, reason: str = "à la demande") -> str:
        with self._lock:
            if self.session is not None:
                return "Je prends déjà des notes."
            cfg = self._cfg()
            from meeting.capture import Capture
            from meeting.transcribe import Transcriber
            tr = (self._transcriber_factory or Transcriber)(
                model=str(cfg.get("meeting_whisper_model") or "small"),
                language=(cfg.get("meeting_language") or None), log=self.log)
            cap = (self._capture_factory or Capture)(on_chunk=tr.submit, log=self.log)
            tr.start()
            cap.start()
            self.session = Session(manual, reason, cap, tr)
            self.watcher.begin(self.clock(), manual)
        self._notify("start", f"Je prends des notes ({reason}) : son du poste et micro, transcrits ici. "
                              "Le son n'est pas enregistré. Dis « arrête les notes » pour arrêter.")
        return "C'est parti : je prends des notes de la réunion."

    def stop(self, reason: str = "à la demande") -> str:
        with self._lock:
            s = self.session
            if s is None:
                return "Je ne prenais pas de notes."
            self.session = None
            self.watcher.end()
            self.finalizing = True
        s.capture.stop()
        s.ended = time.monotonic()
        self._notify("stop", f"Réunion terminée ({reason}, {round(s.duration() / 60)} min) : je prépare le compte rendu.")
        threading.Thread(target=self._finalize, args=(s,), name="meeting-summary", daemon=True).start()
        return "Je m'arrête et je prépare le compte rendu."

    def _finalize(self, s: Session) -> None:
        try:
            lines = s.transcriber.finish()
            transcript = "\n".join(x.render() for x in lines)
            sources = ", ".join(f"{k} {v}" for k, v in s.capture.status.items())
            try:
                result = self._summarize(transcript, s.started, s.duration())
            except Exception as e:
                result = {"title": "Réunion", "summary": f"Compte rendu impossible : {e}. La transcription est gardée.",
                          "decisions": [], "actions": [], "questions": [], "parsed": False, "model": ""}
            md = to_markdown(result, transcript, s.started, s.duration(), sources)
            path = self._save(md, s.started, result.get("title") or "Réunion")
            self.last = {"result": result, "path": str(path) if path else "", "lines": len(lines),
                         "started": s.started.isoformat(timespec="minutes"), "minutes": round(s.duration() / 60)}
            self._deliver(result, path, md)
        except Exception as e:
            self.log(f"SYS: réunion — compte rendu : {type(e).__name__}: {e}")
            self._notify("error", f"Le compte rendu a échoué : {e}")
        finally:
            self.finalizing = False

    def _save(self, md: str, started: datetime, title: str) -> Path | None:
        folder = Path(self._cfg().get("meeting_dir") or default_dir()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{started:%Y-%m-%d %Hh%M} - {_safe(title)}.md"
            path.write_text(md, encoding="utf-8")
            return path
        except OSError as e:
            self.log(f"SYS: réunion — compte rendu non écrit ({e})")
            return None

    def status(self) -> str:
        s = self.session
        if s is None:
            if self.finalizing:
                return "La réunion est finie ; le compte rendu est en préparation."
            mode = {"auto": "je prends des notes dès qu'une visio commence",
                    "ask": "je propose de prendre des notes quand une visio commence",
                    "off": "copilote de réunion désactivé"}[self.watcher.mode]
            return f"Pas de réunion en cours ({self.usage.describe()}) ; {mode}."
        tr = s.transcriber
        return (f"Réunion en cours depuis {round(s.duration() / 60)} min ({s.reason}) : {len(tr.lines)} répliques "
                f"transcrites, {round(tr.backlog_s())} s en attente. "
                + " ; ".join(f"{k} : {v}" for k, v in s.capture.status.items()))

    def recent(self, n: int = 12) -> list[Line]:
        s = self.session
        return sorted(s.transcriber.lines, key=lambda x: x.t)[-n:] if s else []


def get() -> "Copilot | None":
    return _copilot


def install(copilot: "Copilot") -> "Copilot":
    """Le client du PC (client_desktop) installe le sien : l'outil meeting le trouve par get()."""
    global _copilot
    _copilot = copilot
    return copilot


def set_mode(value: str) -> str:
    cop = get()
    if cop is None:
        return "Le copilote de réunion n'est pas actif ici."
    cop.mode_override = cop.watcher.mode = value
    if cop.save_setting:
        try:
            cop.save_setting("meeting_mode", value)
        except Exception as e:
            cop.log(f"SYS: réunion — réglage non enregistré ({e})")
    return {"auto": "D'accord : je prendrai des notes dès qu'une visio commence.",
            "ask": "D'accord : je te proposerai de prendre des notes quand une visio commence.",
            "off": "D'accord : je ne prends plus de notes de réunion tout seul."}[value]


# ── livraison (mode local, et Oracle pour un poste client) ──────────────────

def summary_text(result: dict) -> str:
    md = [result.get("summary") or ""]
    if result.get("decisions"):
        md += ["", "Décisions :"] + [f"• {d}" for d in result["decisions"]]
    if result.get("actions"):
        md += ["", "Actions :"] + [f"• {a['who']} — {a['task']}" + (f" ({a['due']})" if a["due"] else "")
                                   for a in result["actions"]]
    if result.get("questions"):
        md += ["", "Questions ouvertes :"] + [f"• {q}" for q in result["questions"]]
    return "\n".join(md).strip()


def deliver(jarvis, result: dict, where: str = "", names: tuple[str, ...] = ("moi",)) -> dict:
    """Le compte rendu au HUD, les rappels, les objectifs proposés, une phrase à la voix. Rend ce qui a été fait."""
    done = {"reminders": [], "goals": [], "voice": False}
    title = result.get("title") or "Réunion"
    try:
        jarvis.ui.show_content(f"RÉUNION — {title}"[:60], summary_text(result) + (f"\n\n{where}" if where else ""))
    except Exception:
        pass
    ctx = {"player": getattr(jarvis, "ui", None), "speak": getattr(jarvis, "speak", None),
           "response": None, "session_memory": None}
    for a in mine(result, names):
        try:
            if a["due"]:
                date, _, hour = a["due"].partition(" ")
                r = jarvis._action_registry.run("reminder", {"date": date, "time": hour or "09:00",
                                                             "message": a["task"][:200]}, ctx)
                done["reminders"].append(f"{a['task'][:60]} → {r}")
            elif len(done["goals"]) < 3:
                r = jarvis._plugin_registry.run(
                    "goals", {"action": "propose", "title": a["task"][:80],
                              "justification": f"Engagement pris pendant la réunion « {title} ».",
                              "success_criteria": a["task"][:200]}, player=getattr(jarvis, "ui", None))
                done["goals"].append(f"{a['task'][:60]} → {r}")
        except Exception as e:
            done.setdefault("errors", []).append(f"{a['task'][:40]} : {type(e).__name__}")
    if getattr(jarvis, "session", None):
        try:
            mine_n = len(mine(result, names))
            jarvis.plugin_say(
                f"[MEETING] The meeting « {title} » just ended and its minutes are ready"
                + (f" (saved to {where})" if where else "") + f". Summary: {result.get('summary', '')[:600]} "
                f"The user has {mine_n} action(s); {len(done['reminders'])} became reminders and "
                f"{len(done['goals'])} goal proposals awaiting approval on the HUD. Tell the user in two or three "
                "short sentences, in their language. Do not call any tool.")
            done["voice"] = True
        except Exception:
            pass
    try:
        jarvis.ui.write_log(f"SYS: Réunion « {title} » : {len(done['reminders'])} rappel(s), "
                            f"{len(done['goals'])} objectif(s) proposé(s).")
    except Exception:
        pass
    return done


def user_names(cfg: dict) -> tuple[str, ...]:
    name = str(cfg.get("user_name") or "").strip()
    return ("moi",) + ((name, name.split()[0]) if name else ())


def attach(jarvis) -> None:
    """Le hook de main.py, mode local seulement (sur Oracle, c'est le PC relié qui écoute)."""
    global _copilot
    if _copilot is not None or not getattr(getattr(jarvis, "ui", None), "local_mode", False):
        return

    def settings() -> dict:
        try:
            from memory.config_manager import load_api_keys
            return load_api_keys() or {}
        except Exception:
            return {}

    def summarize_local(transcript: str, started: datetime, duration: float) -> dict:
        from meeting.brain import Brain
        from meeting.summarize import summarize
        cfg = settings()
        return summarize(transcript, Brain(cfg), started, duration, user_names(cfg))

    def notify(kind: str, text: str) -> None:
        try:
            jarvis.ui.write_log(f"SYS: {text}")
            if kind in ("start", "ask", "error"):
                jarvis.ui.show_content("RÉUNION", text)
        except Exception:
            pass

    def save_setting(key: str, value: str) -> None:
        from memory.config_manager import _patch_config
        _patch_config(**{key: value})

    _copilot = Copilot(settings, summarize_local,
                       lambda result, path, md: deliver(jarvis, result, str(path or ""), user_names(settings())),
                       notify, log=lambda m: _log(jarvis, m), save_setting=save_setting)
    _copilot.watch()


def _log(jarvis, message: str) -> None:
    try:
        jarvis.ui.write_log(message)
    except Exception:
        print(message)
