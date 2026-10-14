"""
e2e/clients.py — parler à JARVIS comme le téléphone et le HUD lui parlent.

    status, body = http("GET", url, token=bearer)
    bearer = login(jarvis)                       # /api/device-login, avec la vraie clé d'appareil
    ws = Listener(jarvis.url("/ws"), bearer)     # le flux d'événements du HUD / téléphone
    ws.wait_for(lambda m: m.get("type") == "confirm") · ws.send({"type": "command", ...})

Aucun proxy, quel que soit l'environnement de celui qui lance les tests : tout
se passe sur 127.0.0.1.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, url: str, body: dict | None = None, token: str | None = None,
         timeout: float = 10.0) -> tuple[int, object]:
    """(code HTTP, JSON ou texte). (0, raison) si rien ne répond. Ne lève jamais."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw.decode("utf-8", "replace")


def login(jarvis) -> str:
    """Le jeton de session qu'un téléphone appairé obtient avec sa clé d'appareil."""
    code, body = http("POST", jarvis.url("/api/device-login"), {"device_token": jarvis.credential()})
    if code != 200 or not isinstance(body, dict) or not body.get("token"):
        raise AssertionError(f"/api/device-login → {code} {body!r:.120}")
    return body["token"]


def _ws_connect(url: str):
    from websockets.sync.client import connect

    try:
        return connect(url, open_timeout=10, max_size=None, proxy=None)
    except TypeError:                     # websockets < 15 : pas de proxy du tout
        return connect(url, open_timeout=10, max_size=None)


class Listener:
    """Un websocket lu en continu dans son fil ; les messages s'accumulent."""

    def __init__(self, url: str, token: str, query: str = "") -> None:
        sep = "&" if "?" in url else "?"
        self.url = f"{url.replace('http://', 'ws://', 1)}{sep}token={token}{query}"
        self.messages: list = []
        self.closed = threading.Event()
        self.close_code: int | None = None
        self._cond = threading.Condition()
        self._ws = _ws_connect(self.url)
        self._thread = threading.Thread(target=self._read, name="e2e-ws", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            for raw in self._ws:
                msg = raw
                if isinstance(raw, str):
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        msg = {"_texte": raw}
                with self._cond:
                    self.messages.append(msg)
                    self._cond.notify_all()
        except Exception:
            pass
        finally:
            try:
                self.close_code = self._ws.close_code
            except Exception:
                pass
            self.closed.set()
            with self._cond:
                self._cond.notify_all()

    def mark(self) -> int:
        with self._cond:
            return len(self.messages)

    def json_since(self, since: int = 0) -> list[dict]:
        with self._cond:
            return [m for m in self.messages[since:] if isinstance(m, dict)]

    def bytes_since(self, since: int = 0) -> int:
        with self._cond:
            return sum(len(m) for m in self.messages[since:] if isinstance(m, (bytes, bytearray)))

    def wait_for(self, pred, timeout: float = 20.0, since: int = 0, what: str = ""):
        deadline = time.monotonic() + timeout
        i = since
        with self._cond:
            while True:
                while i < len(self.messages):
                    m = self.messages[i]
                    i += 1
                    try:
                        if pred(m):
                            return m
                    except Exception:
                        pass
                left = deadline - time.monotonic()
                if left <= 0 or self.closed.is_set():
                    raise TimeoutError(f"{what or 'message'} : rien sur {self.url.split('?')[0]} "
                                       f"en {timeout:.0f} s" + (" (socket fermée)" if self.closed.is_set() else ""))
                self._cond.wait(min(left, 0.5))

    def send(self, msg: dict) -> None:
        self._ws.send(json.dumps(msg))

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def sys_lines(messages: list[dict]) -> list[str]:
    """Les lignes de journal que JARVIS a publiées sur /ws ({"type": "sys", "text": …})."""
    return [str(m.get("text", "")) for m in messages if m.get("type") == "sys"]
