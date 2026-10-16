"""
pilot/client.py — le pilote sur un PC relié à Oracle (client_desktop).

Le PC regarde son écran et agit ; il n'a pas de clé. Chaque étape part à Oracle
(POST /api/pilot/step), qui appelle le modèle ; une action sensible attend la
confirmation affichée par Oracle sur le HUD et le téléphone (POST
/api/pilot/confirm) ; le résultat est annoncé par la voix d'Oracle (POST
/api/pilot/report). Appelé par client_desktop/app.py :

    pilot.client.start(log, worker.http_session, config_dir)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import pilot as P


def _post(http_session, path: str, payload: dict, timeout: float, opener=urllib.request.urlopen) -> dict:
    sess = http_session()
    if not sess:
        raise ConnectionError("Oracle n'est pas joignable")
    base, bearer = sess
    req = urllib.request.Request(f"{base.rstrip('/')}{path}", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": f"Bearer {bearer}"})
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def start(log: Callable[[str], None], http_session: Callable[[], tuple[str, str] | None],
          config_dir: Path, opener=urllib.request.urlopen) -> "P.Pilot":
    from pilot.brain import RemoteBrain

    def confirm(title: str) -> bool:
        try:
            return bool(_post(http_session, "/api/pilot/confirm", {"title": title}, 110, opener).get("confirmed"))
        except Exception as e:
            log(f"SYS: pilote — confirmation impossible ({e}) : refusée par prudence")
            return False

    def report(outcome: str, message: str) -> None:
        try:
            _post(http_session, "/api/pilot/report", {"outcome": outcome, "message": message}, 30, opener)
        except Exception as e:
            log(f"SYS: pilote — {outcome} : {message[:200]} (Oracle non prévenu : {e})")

    return P.install(P.Pilot(lambda: RemoteBrain(http_session, opener=opener), confirm, report, log,
                             recipes_path=Path(config_dir) / "pilot_recipes.json"))
