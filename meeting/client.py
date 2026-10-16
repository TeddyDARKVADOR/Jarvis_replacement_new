"""
meeting/client.py — le copilote sur un PC relié à Oracle (client_desktop).

Le poste détecte, capte et transcrit ; il n'a pas de clé. La transcription part
à Oracle (POST /api/meeting/summary, server/assist.py), qui résume et livre :
HUD de ce poste par /ws, rappels, objectifs proposés, voix. Oracle injoignable :
le compte rendu est demandé à Ollama sur ce poste s'il tourne, sinon la
transcription seule est gardée — rien n'est perdu.

Réglages : meeting.json à côté de settings.json du client (mode, dossier,
modèle Whisper, langue). Appelé par client_desktop/app.py :

    meeting.client.start(log, worker.http_session, notify, config_dir)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

import meeting as M


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def remote_summary(http_session: Callable[[], tuple[str, str] | None], transcript: str, started: datetime,
                   duration: float, where: str, timeout: float = 300.0, opener=urllib.request.urlopen) -> dict:
    """Le compte rendu fait et livré par Oracle. Lève si Oracle ne répond pas."""
    sess = http_session()
    if not sess:
        raise ConnectionError("Oracle n'est pas joignable")
    base, bearer = sess
    body = json.dumps({"transcript": transcript, "started": started.isoformat(timespec="seconds"),
                       "duration_s": duration, "where": where}).encode("utf-8")
    req = urllib.request.Request(f"{base.rstrip('/')}/api/meeting/summary", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"})
    try:
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"Oracle a répondu HTTP {e.code}") from e
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise ConnectionError("réponse d'Oracle illisible")
    result["delivered_by"] = "oracle"
    return result


def start(log: Callable[[str], None], http_session: Callable[[], tuple[str, str] | None],
          notify: Callable[[str, str], None], config_dir: Path) -> "M.Copilot":
    path = Path(config_dir) / "meeting.json"

    def settings() -> dict:
        return _load(path)

    def save_setting(key: str, value: str) -> None:
        data = _load(path)
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def summarize(transcript: str, started: datetime, duration: float) -> dict:
        where = str(settings().get("meeting_dir") or M.default_dir())
        try:
            return remote_summary(http_session, transcript, started, duration, where)
        except Exception as e:
            log(f"SYS: réunion — Oracle indisponible pour le compte rendu ({e}) : Ollama sur ce poste.")
            from meeting.brain import Brain
            from meeting.summarize import summarize as local
            return local(transcript, Brain({**settings(), "meeting_brain_order": ["local"]}), started, duration)

    def deliver(result: dict, saved: Path | None, md: str) -> None:
        where = f" — {saved}" if saved else ""
        if result.get("delivered_by") == "oracle":
            notify("summary", f"Compte rendu « {result.get('title')} » prêt{where}")
        else:
            notify("summary", f"Compte rendu « {result.get('title')} » (sur ce poste){where}\n\n{M.summary_text(result)}")

    cop = M.Copilot(settings, summarize, deliver, notify, log=log, save_setting=save_setting)
    M.install(cop)
    cop.watch()
    return cop
