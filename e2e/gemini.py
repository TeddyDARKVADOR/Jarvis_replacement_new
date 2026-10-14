"""
e2e/gemini.py — le faux Gemini : l'API Live, et assez de generateContent.

    fake = FakeGemini(workdir).start()        # https://127.0.0.1:<port>, TLS de test
    s = fake.wait_session(1)                  # la connexion que JARVIS a ouverte
    s.setup                                   # son vrai `setup` : modèle, prompt, outils, reprise
    s.say("Bonjour.")                         # de la voix, sa transcription, fin de tour
    r = s.tool_call("capabilities", {...})    # JARVIS exécute l'outil, renvoie sa réponse
    s.go_away() · s.resumable("h1") · s.close(1011)

LE PROTOCOLE, TEL QUE LE SDK LE PARLE (google-genai, live.py)
    JARVIS ouvre wss://<base>/ws/google.ai.generativelanguage.<v1alpha|v1beta>.
    GenerativeService.BidiGenerateContent, envoie {"setup": …} et attend
    {"setupComplete": {}}. Ensuite, dans un sens : clientContent (un tour tapé,
    le briefing), realtimeInput (le micro), toolResponse ; dans l'autre :
    serverContent (modelTurn audio, outputTranscription, inputTranscription,
    turnComplete), toolCall, goAway, sessionResumptionUpdate. Du JSON en
    camelCase, comme l'API réelle.

POURQUOI UN FIL À PART
    Le serveur (uvicorn + starlette, déjà des dépendances de JARVIS) tourne dans
    son propre fil et sa propre boucle : hors du fil principal, uvicorn
    n'intercepte aucun signal, et les scénarios restent du code séquentiel, qui
    se lit comme une conversation. Toutes les méthodes de LiveSession sont
    synchrones et sûres depuis n'importe quel fil.
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import socket
import threading
import time
from pathlib import Path

PCM_RATE = 24000


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pcm(seconds: float) -> bytes:
    """De la « voix » : une onde carrée à 500 Hz, jamais du silence — un test
    qui compte des octets ne doit pas confondre une voix et rien."""
    period = PCM_RATE // 500
    half = period // 2
    block = (int(3000).to_bytes(2, "little", signed=True) * half
             + int(-3000).to_bytes(2, "little", signed=True) * (period - half))
    return block * max(1, int(PCM_RATE * seconds) // period)


def user_texts(msg: dict) -> list[str]:
    """Les textes d'un clientContent : ce que JARVIS a « tapé » à Gemini."""
    cc = msg.get("clientContent") or msg.get("client_content") or {}
    out = []
    for turn in cc.get("turns") or []:
        for part in (turn or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                out.append(str(part["text"]))
    return out


def tool_responses(msg: dict) -> list[dict]:
    tr = msg.get("toolResponse") or msg.get("tool_response") or {}
    return tr.get("functionResponses") or tr.get("function_responses") or []


def declared_tools(setup: dict) -> list[str]:
    names = []
    for tool in setup.get("tools") or []:
        for fd in tool.get("functionDeclarations") or tool.get("function_declarations") or []:
            names.append(fd.get("name"))
    return names


def system_text(setup: dict) -> str:
    si = setup.get("systemInstruction") or setup.get("system_instruction") or {}
    if isinstance(si, str):
        return si
    return "\n".join(str(p.get("text", "")) for p in si.get("parts") or [] if isinstance(p, dict))


def resumption_handle(setup: dict) -> str | None:
    sr = setup.get("sessionResumption") or setup.get("session_resumption") or {}
    return sr.get("handle")


class LiveSession:
    """Une connexion Live ouverte par JARVIS."""

    def __init__(self, fake: "FakeGemini", ws, index: int, path: str, query: dict, headers: dict) -> None:
        self._fake = fake
        self._ws = ws
        self.index = index
        self.path = path
        self.api_key = query.get("key", "") or headers.get("x-goog-api-key", "")
        self.setup: dict = {}
        self.inbox: list[dict] = []
        self.error = ""
        self.close_code: int | None = None
        self.closed = threading.Event()
        self._cond = threading.Condition()
        self._ids = itertools.count(1)

    # ── ce que JARVIS a envoyé ───────────────────────────────────────────────

    def _push(self, msg: dict) -> None:
        with self._cond:
            self.inbox.append(msg)
            self._cond.notify_all()

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def mark(self) -> int:
        """Où en est la boîte : wait_for(…, since=mark) ne regarde que la suite."""
        with self._cond:
            return len(self.inbox)

    def wait_for(self, pred, timeout: float = 20.0, since: int = 0, what: str = "") -> dict:
        deadline = time.monotonic() + timeout
        i = since
        with self._cond:
            while True:
                while i < len(self.inbox):
                    msg = self.inbox[i]
                    i += 1
                    try:
                        if pred(msg):
                            return msg
                    except Exception:
                        pass
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"{what or 'message de JARVIS'} : rien en {timeout:.0f} s")
                if self.closed.is_set():
                    raise TimeoutError(f"{what or 'message de JARVIS'} : session {self.index} fermée "
                                       f"(code {self.close_code})")
                self._cond.wait(min(left, 0.5))

    def wait_text(self, needle: str, timeout: float = 20.0, since: int = 0) -> str:
        """Le premier texte envoyé par JARVIS qui contient `needle`."""
        msg = self.wait_for(lambda m: any(needle in t for t in user_texts(m)), timeout, since,
                            f"le texte « {needle} »")
        return next(t for t in user_texts(msg) if needle in t)

    # ── ce que « Gemini » envoie ─────────────────────────────────────────────

    def send(self, msg: dict) -> None:
        self._fake._call(self._ws.send_text(json.dumps(msg)))

    def say(self, text: str, seconds: float = 0.4) -> int:
        """Un tour de parole complet. Renvoie le nombre d'octets de voix envoyés."""
        audio = pcm(seconds)
        self.send({"serverContent": {"modelTurn": {"parts": [{"inlineData": {
            "mimeType": f"audio/pcm;rate={PCM_RATE}", "data": base64.b64encode(audio).decode("ascii")}}]}}})
        self.send({"serverContent": {"outputTranscription": {"text": text}}})
        self.send({"serverContent": {"turnComplete": True}})
        return len(audio)

    def voice(self, seconds: float, chunk_s: float = 0.5) -> int:
        """De la voix seule, en morceaux, sans fin de tour (pour l'interruption)."""
        sent = 0
        for _ in range(max(1, int(seconds / chunk_s))):
            audio = pcm(chunk_s)
            self.send({"serverContent": {"modelTurn": {"parts": [{"inlineData": {
                "mimeType": f"audio/pcm;rate={PCM_RATE}", "data": base64.b64encode(audio).decode("ascii")}}]}}})
            sent += len(audio)
        return sent

    def end_turn(self) -> None:
        self.send({"serverContent": {"turnComplete": True}})

    def heard(self, text: str) -> None:
        """Ce que Gemini aurait entendu l'utilisateur dire (inputTranscription)."""
        self.send({"serverContent": {"inputTranscription": {"text": text}}})

    def tool_call(self, name: str, args: dict | None = None, timeout: float = 30.0) -> dict:
        """Appelle un outil comme le modèle le ferait ; renvoie la réponse de JARVIS."""
        call_id = f"e2e-{self.index}-{next(self._ids)}"
        since = self.mark()
        self.send({"toolCall": {"functionCalls": [{"id": call_id, "name": name, "args": args or {}}]}})
        msg = self.wait_for(lambda m: any(r.get("id") == call_id for r in tool_responses(m)),
                            timeout, since, f"la réponse de l'outil {name}")
        reply = next(r for r in tool_responses(msg) if r.get("id") == call_id)
        return reply.get("response") or {}

    def go_away(self, time_left: str = "1s") -> None:
        self.send({"goAway": {"timeLeft": time_left}})

    def resumable(self, handle: str) -> None:
        self.send({"sessionResumptionUpdate": {"newHandle": handle, "resumable": True}})

    def close(self, code: int = 1011, reason: str = "e2e") -> None:
        self._fake._call(self._ws.close(code=code, reason=reason))


class FakeGemini:
    """Le serveur. `start()` rend la main quand il écoute."""

    def __init__(self, workdir: Path, text_reply: str = "D'accord.") -> None:
        self.workdir = Path(workdir)
        self.text_reply = text_reply
        self.port = free_port()
        self.sessions: list[LiveSession] = []
        self.http_calls: list[dict] = []
        self.ca: Path | None = None
        self._cond = threading.Condition()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None

    @property
    def base_url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    # ── vie du serveur ───────────────────────────────────────────────────────

    def start(self, timeout: float = 20.0) -> "FakeGemini":
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Route, WebSocketRoute

        from e2e import certs

        self.ca, cert, key = certs.make(self.workdir / "tls")
        app = Starlette(routes=[WebSocketRoute("/{path:path}", self._live),
                                Route("/{path:path}", self._http, methods=["GET", "POST"])])
        self._server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=self.port, ssl_certfile=str(cert), ssl_keyfile=str(key),
            log_level="warning", lifespan="off"))

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._server.serve())
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, name="e2e-faux-gemini", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not getattr(self._server, "started", False):
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("le faux Gemini n'a pas démarré")
            time.sleep(0.05)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(10)

    def _call(self, coro, timeout: float = 15.0):
        if self._loop is None:
            raise RuntimeError("le faux Gemini ne tourne pas")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def wait_session(self, count: int, timeout: float = 60.0) -> LiveSession:
        """La `count`-ième connexion de JARVIS (1 = la première)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while len(self.sessions) < count:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"JARVIS n'a pas ouvert sa session Live n°{count} en {timeout:.0f} s "
                                       f"({len(self.sessions)} ouverte(s))")
                self._cond.wait(min(left, 0.5))
            return self.sessions[count - 1]

    # ── les deux points d'entrée ─────────────────────────────────────────────

    async def _live(self, ws) -> None:
        await ws.accept()
        session = LiveSession(self, ws, 0, ws.url.path, dict(ws.query_params),
                              {k.lower(): v for k, v in ws.headers.items()})
        try:
            first = await ws.receive()
            raw = first.get("text") or first.get("bytes")
            if not raw:
                return
            msg = json.loads(raw)
            session.setup = msg.get("setup") or msg
            with self._cond:
                self.sessions.append(session)
                session.index = len(self.sessions)
                self._cond.notify_all()
            await ws.send_text(json.dumps({"setupComplete": {}}))
            while True:
                m = await ws.receive()
                if m.get("type") == "websocket.disconnect":
                    session.close_code = m.get("code")
                    break
                raw = m.get("text") or m.get("bytes")
                if raw:
                    try:
                        session._push(json.loads(raw))
                    except ValueError:
                        session._push({"_illisible": str(raw)[:200]})
        except Exception as exc:          # la connexion fermée par l'un ou l'autre
            session.error = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            session.closed.set()
            session._wake()
            with self._cond:
                self._cond.notify_all()

    async def _http(self, request):
        from starlette.responses import JSONResponse

        body = await request.body()
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {"_brut": body[:200].decode("utf-8", "replace")}
        path = request.url.path
        with self._cond:
            self.http_calls.append({"method": request.method, "path": path, "body": data})
        if path.endswith(":generateContent"):
            return JSONResponse({
                "candidates": [{"content": {"role": "model", "parts": [{"text": self.text_reply}]},
                                "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3, "totalTokenCount": 15}})
        if path.endswith(":countTokens"):
            return JSONResponse({"totalTokens": 12})
        return JSONResponse({"error": {"code": 404, "status": "NOT_FOUND",
                                       "message": f"e2e : {path} n'est pas simulé"}}, status_code=404)
