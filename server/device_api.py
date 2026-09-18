"""
server/device_api.py — the control channel, grafted on beside the existing ones.

WHY NEW ROUTES AND NOT A CHANGE TO `/ws`
    `dashboard/server.py` is one of the protected files, and FastAPI matches the
    FIRST route registered for a path — so `/ws` and `/api/device-login` cannot
    be redefined from here even if they were fair game. They are therefore left
    exactly as they are, which is also what keeps the current Android build
    working with no change at all.

    Two routes are added instead, following the pattern `server/api.py` already
    established:

        POST /api/device-register    declare who you are and what you can do
        WS   /ws/device              routed commands out, results back

WHAT `/ws` COULD NEVER DO
    Its handler does `_command_queue.put(text)` — a bare string. The token that
    authenticated the socket is in scope there and is discarded, so by the time
    a command reaches Gemini there is nothing left that says which machine sent
    it. That is not a bug in `dashboard/server.py`; it was written when there
    was one client. It is simply why origin cannot be recovered after the fact,
    and why clients that want to be routed send their commands here instead.

    A client that keeps using `/ws` still works. Its commands arrive with no
    origin, and `targeting.py` asks rather than guesses.

THE TURN CONTEXT
    Gemini receives a flat string and answers with a tool call. Nothing in that
    round trip carries the origin, so it is held here for the duration of the
    turn: whoever spoke last, and what they said. It is deliberately tiny and
    deliberately not a session — see `TurnContext`.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

from .devices import DeviceInfo, DeviceRegistry, DeviceType

# Imported at module level, and the annotations on the handlers below are NOT
# strings, for the reason `server/api.py` documents: FastAPI resolves a
# handler's annotations with get_type_hints against its MODULE globals, so a
# name imported inside `attach()` resolves to nothing and every call answers 422
# with the auth check never running.
#
# Guarded, because the hub and the channel are pure asyncio and the routing
# tests must run on a machine with no web framework installed — which is this
# laptop. Only `attach()` genuinely needs FastAPI, and it says so.
try:
    from fastapi import Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on machines without fastapi
    FASTAPI_AVAILABLE = False

    class Request:  # type: ignore[no-redef]
        pass

    class WebSocket:  # type: ignore[no-redef]
        pass

    class WebSocketDisconnect(Exception):  # type: ignore[no-redef]
        pass

    class JSONResponse:  # type: ignore[no-redef]
        def __init__(self, *_a, **_kw) -> None:
            raise RuntimeError("fastapi is not installed")

#: How long a routed command may take before the caller gives up. Generous
#: because "ouvre Photoshop" genuinely takes a while on a cold cache, and a
#: timeout that fires early produces the worst possible outcome: Gemini is told
#: it failed and the application opens anyway.
REMOTE_TIMEOUT_SECONDS = 45.0

#: A turn's origin is only trusted this long. Past it the assistant is answering
#: something else and inheriting a stale origin would route to the wrong device.
TURN_CONTEXT_TTL = 180.0


@dataclass
class TurnContext:
    """Who spoke last, and what they said. Not a session, and not history.

    Two fields and a timestamp, because that is all the resolver needs and
    anything more would be a second conversation state competing with
    `main.py`'s own.
    """

    origin_device_id: str = ""
    text: str = ""
    at: float = 0.0

    def set(self, device_id: str, text: str = "") -> None:
        self.origin_device_id = device_id or ""
        if text:
            self.text = text
        self.at = time.monotonic()

    def fresh(self) -> bool:
        return bool(self.origin_device_id) and (
            time.monotonic() - self.at
        ) < TURN_CONTEXT_TTL

    def origin(self, registry: DeviceRegistry) -> DeviceInfo | None:
        return registry.get(self.origin_device_id) if self.fresh() else None

    def current_text(self) -> str:
        return self.text if self.fresh() else ""


class DeviceChannel:
    """One connected client's control socket, and the replies it owes us."""

    def __init__(self, device_id: str, websocket: WebSocket) -> None:
        self.device_id = device_id
        self.websocket = websocket
        self._pending: dict[str, asyncio.Future] = {}

    async def send(self, payload: dict) -> None:
        await self.websocket.send_text(json.dumps(payload))

    async def request(self, action: str, parameters: dict,
                      timeout: float = REMOTE_TIMEOUT_SECONDS) -> str:
        """Ask this device to run something, and wait for what it says back.

        The id is ours and goes back untouched, so a slow client answering an
        abandoned request cannot be mistaken for the answer to the current one —
        the same reasoning as the confirmation gate's `cid`.
        """
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send({
                "type": "device_command",
                "id": request_id,
                "target_device_id": self.device_id,
                "action": action,
                "parameters": parameters or {},
            })
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return (
                f"L'appareil {self.device_id} n'a pas répondu dans les "
                f"{int(timeout)} secondes."
            )
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, result: str) -> None:
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(result)

    def fail_all(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(reason)
        self._pending.clear()


@dataclass
class DeviceHub:
    """Everything the router needs to reach a device. One per server."""

    registry: DeviceRegistry = field(default_factory=DeviceRegistry)
    turn: TurnContext = field(default_factory=TurnContext)
    channels: dict[str, DeviceChannel] = field(default_factory=dict)
    #: The server's event loop, captured the first time a route runs on it.
    #:
    #: It cannot be captured at wiring time: `run_headless.build()` assembles
    #: everything *before* `asyncio.run()` creates the loop. The router runs on
    #: a worker thread and needs this to marshal a remote call back, so it is
    #: recorded here by the first handler that executes.
    loop: asyncio.AbstractEventLoop | None = None

    def note_loop(self) -> None:
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def channel(self, device_id: str) -> DeviceChannel | None:
        return self.channels.get(device_id)

    def attach_channel(self, channel: DeviceChannel) -> None:
        previous = self.channels.get(channel.device_id)
        if previous is not None:
            # A reconnect before the old socket was noticed as dead. Whatever
            # the old one was waiting on will never arrive.
            previous.fail_all("La connexion à cet appareil a été remplacée.")
        self.channels[channel.device_id] = channel
        self.registry.set_connected(channel.device_id, True)

    def detach_channel(self, channel: DeviceChannel) -> None:
        if self.channels.get(channel.device_id) is channel:
            self.channels.pop(channel.device_id, None)
            self.registry.set_connected(channel.device_id, False)
        channel.fail_all("L'appareil s'est déconnecté avant de répondre.")

    def reachable(self, device_id: str) -> bool:
        return device_id in self.channels


def attach(dashboard, hub: DeviceHub, *, log=print) -> None:
    """Add the device routes to `dashboard.app`. Call once, before serve()."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "server.device_api.attach() needs fastapi, which is not installed."
        )
    app = dashboard.app
    if getattr(app.state, "jarvis_devices_attached", False):
        raise RuntimeError("server.device_api.attach() was already called")
    app.state.jarvis_devices_attached = True
    app.state.jarvis_device_hub = hub

    def _token(req_or_value: object) -> str:
        if isinstance(req_or_value, str):
            return req_or_value.strip()
        header = getattr(req_or_value, "headers", {}).get("authorization", "")
        return header.removeprefix("Bearer ").strip()

    def _authorised(token: str) -> bool:
        # The same question `dashboard/server.py` asks on /api/command, asked
        # of the same set. Not a second auth scheme.
        return bool(token) and token in dashboard._tokens

    # ── POST /api/device-register ────────────────────────────────────────────

    async def device_register(req: Request) -> JSONResponse:
        hub.note_loop()
        token = _token(req)
        if not _authorised(token):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            body = await req.json()
        except Exception:
            body = {}

        device_id = str(body.get("device_id") or "").strip()
        if not device_id:
            return JSONResponse({"error": "device_id required"}, status_code=400)

        info = hub.registry.register(
            device_id=device_id,
            device_type=body.get("device_type", DeviceType.UNKNOWN),
            display_name=str(body.get("display_name") or ""),
            capabilities=body.get("capabilities") or (),
            protocol_version=int(body.get("protocol_version") or 1),
            token=token,
        )
        log(f"[Devices] registered {info.device_type.value}:{info.label} "
            f"({len(info.capabilities)} capabilities)")
        return JSONResponse({
            "ok": True,
            "device": info.public(),
            "devices": hub.registry.public(),
        })

    # ── WS /ws/device ────────────────────────────────────────────────────────

    async def device_ws(websocket: WebSocket, token: str = "",
                        device_id: str = "") -> None:
        hub.note_loop()
        tok = token.strip()
        if not _authorised(tok):
            await websocket.close(code=4001)
            return

        # The id may come from the query or from the token binding made at
        # registration. The query wins, so a client that re-registers under a
        # new id does not keep answering as the old one.
        resolved_id = device_id.strip()
        if not resolved_id:
            known = hub.registry.device_for_token(tok)
            resolved_id = known.device_id if known else ""
        if not resolved_id or hub.registry.get(resolved_id) is None:
            # Registering first is the contract; without it there are no
            # capabilities, and a device with no capabilities can be sent
            # nothing anyway.
            await websocket.close(code=4002)
            return

        await websocket.accept()
        channel = DeviceChannel(resolved_id, websocket)
        hub.attach_channel(channel)
        hub.registry.bind_token(tok, resolved_id)
        info = hub.registry.get(resolved_id)
        log(f"[Devices] {info.device_type.value}:{info.label} online")

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                kind = message.get("type")

                if kind == "device_result":
                    channel.resolve(
                        str(message.get("id") or ""),
                        str(message.get("result") or ""),
                    )

                elif kind == "command":
                    # A command that carries its origin — the whole point of
                    # this channel. It joins the queue `main.py` already drains,
                    # unchanged, so nothing downstream had to learn a new path.
                    text = str(message.get("text") or "").strip()
                    if text:
                        hub.turn.set(resolved_id, text)
                        hub.registry.touch(resolved_id)
                        await dashboard._command_queue.put(text)
                        if dashboard._wake_callback:
                            dashboard._wake_callback()

                elif kind == "mic":
                    # Voice turns have no text to read an origin from, so the
                    # microphone being open is what says who is speaking.
                    if bool(message.get("open")):
                        hub.turn.set(resolved_id, "")
                    hub.registry.touch(resolved_id)

                elif kind == "ping":
                    hub.registry.touch(resolved_id)
                    await channel.send({"type": "pong"})

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log(f"[Devices] {resolved_id}: {type(exc).__name__}: {exc}")
        finally:
            hub.detach_channel(channel)
            log(f"[Devices] {resolved_id} offline")

    app.add_api_route("/api/device-register", device_register, methods=["POST"])
    app.add_api_websocket_route("/ws/device", device_ws)
