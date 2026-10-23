"""
What the HUD asks of "the assistant", and who answers — Oracle or this PC.

The page asks for the same things in both modes: list the memory, change the
voice, toggle a plugin, attach a file. A *backend* answers:

    RemoteBackend   (here)            `POST /api/assistant` and `POST /api/upload`
                                      on Oracle, with the session's bearer
    LocalBackend    (local_hud.py)    `server.assistant_control` in-process, and
                                      the local brain's own file slot

Same interface, so `HudWindow` holds "a backend" and never learns which:

    call(op, data) -> dict            {"error": …} on failure, never raises
    attach_file(path) -> dict         {"ok", "name"} or {"error"}
    clear_file() -> None
    capabilities() -> dict            what this mode can do at all
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

#: Plain HTTP over Tailscale, to a server one hop away. Anything slower than
#: this is a link problem, and the page says so instead of hanging.
TIMEOUT_S = 15
#: The dashboard's own upload limit (dashboard/server.py MAX_UPLOAD_MB).
MAX_UPLOAD_MB = 50


class RemoteBackend:
    def __init__(self, session: Callable[[], "tuple[str, str] | None"]) -> None:
        #: `NetworkWorker.http_session` — read at each call, because the bearer
        #: changes at every reconnection.
        self._session = session

    def capabilities(self) -> dict:
        # Oracle has no webcam to show and pairs phones with --pairing.
        return {"local": False, "memory": True, "profile": True, "plugins": True,
                "phone": False, "files": True}

    def call(self, op: str, data: dict | None = None) -> dict:
        import requests

        session = self._session()
        if session is None:
            return {"error": "Matthew n'est pas connecté — réessayez une fois la liaison établie."}
        base, bearer = session
        try:
            r = requests.post(f"{base}/api/assistant", json={"op": op, "data": data or {}},
                              headers={"Authorization": f"Bearer {bearer}"}, timeout=TIMEOUT_S)
        except Exception as exc:
            return {"error": f"serveur injoignable ({type(exc).__name__})"}
        if r.status_code == 404:
            return {"error": "le serveur Oracle est à mettre à jour (route /api/assistant absente)"}
        try:
            return r.json()
        except ValueError:
            return {"error": f"réponse illisible (HTTP {r.status_code})"}

    def attach_file(self, path: str) -> dict:
        """Upload to Oracle; the server's newest upload is the file Matthew reads
        when asked to analyse "this file" (server/headless_ui.current_file)."""
        import requests

        session = self._session()
        if session is None:
            return {"error": "Matthew n'est pas connecté."}
        p = Path(path)
        if not p.is_file():
            return {"error": "fichier introuvable"}
        if p.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            return {"error": f"fichier trop gros (max {MAX_UPLOAD_MB} Mo)"}
        base, bearer = session
        try:
            with p.open("rb") as fh:
                r = requests.post(f"{base}/api/upload", files={"file": (p.name, fh)},
                                  headers={"Authorization": f"Bearer {bearer}"}, timeout=120)
            body = r.json()
        except Exception as exc:
            return {"error": f"envoi impossible ({type(exc).__name__})"}
        if not r.ok or not body.get("ok"):
            return {"error": body.get("error") or f"HTTP {r.status_code}"}
        return {"ok": True, "name": body.get("name", p.name)}

    def clear_file(self) -> None:
        # The server keeps its uploads; the page simply stops pointing at one.
        pass
