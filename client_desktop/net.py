"""
The three sockets, and the thread they live on.

```
   POST /api/device-login          once per session, and again after a 4001
        │
        ├── WS /ws              ── JSON events, both ways      (always open)
        ├── WS /ws/phone-out    ── JARVIS's voice, binary      (always open)
        └── WS /ws/phone-audio  ── this microphone, binary     (only while the
                                                                gate is open)
```

**Why the uplink is not always open.** Connecting it *means something* to the
server: it broadcasts that a microphone is live and silences its own. Holding it
open with the gate shut would leave MARK LIII believing a microphone is present
that is deliberately sending nothing, and after one second of no frames it marks
it inactive anyway. So the socket's lifetime is the gate's lifetime, which is
also the honest thing to show a user: the socket is open exactly when audio is
leaving the machine.

**Why a dedicated thread.** PyQt owns the main thread and its event loop is not
asyncio's. `qasync` would marry them; it is one more dependency to keep working
across Qt releases for a client whose networking is three sockets. A worker
thread with its own loop, and Qt signals for the handful of things the UI must
know about, is the smaller correct thing.

Everything public on `NetworkWorker` is safe to call from the GUI thread; it
schedules onto the loop and returns immediately.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Callable

import requests
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import protocol as P
from .audio import Microphone, Speaker
from .config import Settings
from .protocol import AuthRejected, ServerEndpoint
from .reconnect import ReconnectManager
from .state import AssistantState, JarvisStore, LinkState, Message

#: How many uplink frames may queue before the oldest are dropped. At 64 ms a
#: frame this is ~3 s. The server's own inbound queue drops when full too, so a
#: bounded queue here is the same policy applied one hop earlier — and a client
#: that buffered without limit would answer a question asked a minute ago.
MIC_QUEUE_FRAMES = 48

LOGIN_TIMEOUT = 10.0


def _close_code(exc: BaseException) -> int | None:
    """The close code, wherever this version of `websockets` decided to put it."""
    received = getattr(exc, "rcvd", None)
    if received is not None and getattr(received, "code", None) is not None:
        return int(received.code)
    code = getattr(exc, "code", None)
    return int(code) if isinstance(code, int) else None


class JarvisClient:
    """One session with MARK LIII. Owns the sockets, owns nothing else."""

    def __init__(
        self,
        settings: Settings,
        store: JarvisStore,
        speaker: Speaker,
        on_connected: Callable[[], None],
        on_disconnected: Callable[[str, bool], None],
    ) -> None:
        self._settings = settings
        self._store = store
        self._speaker = speaker
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        self._bearer: str = ""
        self._session_key: str = ""

        self._events_ws = None
        self._uplink_ws = None
        self._tasks: set[asyncio.Task] = set()

        self._mic_queue: asyncio.Queue[bytes] = asyncio.Queue(MIC_QUEUE_FRAMES)
        self._want_uplink = False

        self._sockets_open: set[str] = set()
        self._announced_connected = False
        self._closing = False

    @property
    def endpoint(self) -> ServerEndpoint:
        return self._settings.endpoint

    # ── connecting ───────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Log in and request the two permanent sockets.

        Raises `AuthRejected` if the device token itself is refused — the only
        failure the reconnect loop must not retry. Every other failure raises
        something ordinary and gets a backoff.
        """
        await self._teardown()
        self._closing = False
        self._sockets_open.clear()
        self._announced_connected = False

        endpoint = self.endpoint
        if not endpoint.is_usable:
            raise RuntimeError("no server configured")
        if not self._settings.device_token.strip():
            raise AuthRejected("no device token — pair this machine first")

        self._bearer, self._session_key = await self._login(endpoint)
        self._store.log(f"Logged in to {endpoint.host}:{endpoint.port}")

        self._spawn(self._events_loop(endpoint), "events")
        self._spawn(self._downlink_loop(endpoint), "downlink")

    async def _login(self, endpoint: ServerEndpoint) -> tuple[str, str]:
        """`/api/device-login`, off the event loop.

        `requests` rather than an async client on purpose: this is one short
        POST per session, and the alternative is a second HTTP stack to keep
        working for no measurable gain.
        """

        def post() -> tuple[str, str]:
            response = requests.post(
                endpoint.device_login,
                json={"device_token": self._settings.device_token.strip()},
                timeout=LOGIN_TIMEOUT,
            )
            if response.status_code == 401:
                raise AuthRejected("device token refused")
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise AuthRejected("device token refused")
            return str(payload.get("token", "")), str(payload.get("key", ""))

        return await asyncio.get_running_loop().run_in_executor(None, post)

    def _spawn(self, coro, name: str) -> None:
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _socket_up(self, name: str) -> None:
        self._sockets_open.add(name)
        # CONNECTED is declared only when both permanent sockets are genuinely
        # open. Declaring it on the first would show a connected core while
        # JARVIS's voice had nowhere to arrive.
        if not self._announced_connected and {"events", "downlink"} <= self._sockets_open:
            self._announced_connected = True
            self._on_connected()
            # The gate may already be open — the user can press the microphone
            # button while the client is OFFLINE, and a reconnect does not shut
            # it. Without this the uplink would never start for that session:
            # `set_mic_streaming(True)` was called when there was nothing to
            # attach it to, and nothing calls it again.
            if self._want_uplink:
                self._spawn(self._uplink_loop(self.endpoint), "uplink")

    def _socket_down(self, name: str, reason: str, fatal: bool = False) -> None:
        self._sockets_open.discard(name)
        if self._closing:
            return
        self._closing = True
        self._on_disconnected(reason, fatal)

    # ── /ws — events ─────────────────────────────────────────────────────────

    async def _events_loop(self, endpoint: ServerEndpoint) -> None:
        try:
            async with ws_connect(
                endpoint.events(self._bearer),
                ping_interval=P.PING_SECONDS,
                ping_timeout=P.PING_TIMEOUT_SECONDS,
                max_size=None,
            ) as socket:
                self._events_ws = socket
                self._socket_up("events")
                async for raw in socket:
                    if isinstance(raw, bytes):
                        continue
                    self._handle_event(raw)
        except asyncio.CancelledError:
            raise
        except InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self._socket_down("events", f"events refused ({status})", fatal=status == 401)
        except ConnectionClosed as exc:
            code = _close_code(exc)
            # 4001 is the bearer going stale, not the credential being wrong.
            # Reconnecting re-runs device-login, which is the documented cure.
            self._socket_down("events", f"events closed ({code or 'no code'})")
        except Exception as exc:
            self._socket_down("events", f"events: {exc}")
        finally:
            self._events_ws = None
            self._sockets_open.discard("events")

    def _handle_event(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except Exception:
            return
        kind = event.get("type")

        if kind == P.EV_JARVIS_STATE:
            name = str(event.get("state", "")).upper()
            try:
                self._store.set_assistant(AssistantState[name])
            except KeyError:
                self._store.set_assistant(AssistantState.UNKNOWN)

        elif kind == P.EV_STATUS:
            # Coarser than jarvis_state and kept for servers that only send
            # this one. Never allowed to overwrite a finer state with UNKNOWN.
            if str(event.get("state", "")) == "sleeping":
                self._store.set_assistant(AssistantState.SLEEPING)

        elif kind == P.EV_LOG:
            speaker = str(event.get("speaker", ""))
            text = str(event.get("text", "")).strip()
            if text:
                self._store.add_message(
                    Message(
                        from_jarvis=speaker == "jarvis",
                        text=text,
                        at=_parse_iso(event.get("ts")),
                    )
                )

        elif kind == P.EV_CONTENT:
            text = str(event.get("text", "")).strip()
            if text:
                self._store.add_message(
                    Message(
                        from_jarvis=True,
                        text=text,
                        title=str(event.get("title", "")) or None,
                        at=time.time(),
                    )
                )

        elif kind == P.EV_SYS:
            text = str(event.get("text", "")).strip()
            if text:
                self._store.log(f"SYS: {text}")

        elif kind == P.EV_FILE:
            self._store.log(
                f"file received: {event.get('name')} ({event.get('size')} B)"
            )

        elif kind == P.EV_CONFIRM:
            cid = str(event.get("id", ""))
            if cid:
                self._store.set_confirmation(
                    cid,
                    str(event.get("title", "")),
                    str(event.get("detail", "")),
                    int(event.get("timeout_s") or 0),
                )

        elif kind == P.EV_CONFIRM_HIDE:
            self._store.clear_confirmation()

    # ── /ws/phone-out — JARVIS's voice ───────────────────────────────────────

    async def _downlink_loop(self, endpoint: ServerEndpoint) -> None:
        try:
            async with ws_connect(
                endpoint.audio_downlink(self._bearer),
                ping_interval=P.PING_SECONDS,
                ping_timeout=P.PING_TIMEOUT_SECONDS,
                max_size=None,
            ) as socket:
                self._socket_up("downlink")
                async for raw in socket:
                    if isinstance(raw, str):
                        # The announcement. Build the player from *this*, never
                        # from a constant — see protocol.py.
                        try:
                            message = json.loads(raw)
                        except Exception:
                            continue
                        if message.get("type") == P.EV_AUDIO_FORMAT:
                            rate = int(
                                message.get("sample_rate")
                                or P.DOWNLINK_SAMPLE_RATE_DEFAULT
                            )
                            self._store.set_downlink_rate(rate)
                            self._store.log(f"downlink announced {rate} Hz")
                            self._speaker.open(rate)
                        continue

                    self._store.count_received(len(raw))
                    if not self._speaker.running:
                        # Audio before the announcement, or after a device
                        # error. Opening at the documented default is better
                        # than dropping the first sentence of the session.
                        self._speaker.open(
                            self._store.snapshot().downlink_rate
                            or P.DOWNLINK_SAMPLE_RATE_DEFAULT
                        )
                    self._speaker.write(raw)
        except asyncio.CancelledError:
            raise
        except InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self._socket_down("downlink", f"voice refused ({status})", fatal=status == 401)
        except ConnectionClosed as exc:
            self._socket_down("downlink", f"voice closed ({_close_code(exc) or 'no code'})")
        except Exception as exc:
            self._socket_down("downlink", f"voice: {exc}")
        finally:
            self._sockets_open.discard("downlink")

    # ── /ws/phone-audio — this microphone ────────────────────────────────────

    async def _uplink_loop(self, endpoint: ServerEndpoint) -> None:
        """Open for exactly as long as the gate is. See the module note."""
        try:
            async with ws_connect(
                endpoint.mic_uplink(self._bearer),
                ping_interval=P.PING_SECONDS,
                ping_timeout=P.PING_TIMEOUT_SECONDS,
                max_size=None,
            ) as socket:
                self._uplink_ws = socket
                self._store.set_mic_open(True)
                self._store.log("microphone open — audio is leaving this machine")
                while self._want_uplink:
                    frame = await self._mic_queue.get()
                    await socket.send(frame)
                    self._store.count_sent(len(frame))
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as exc:
            self._store.log(f"microphone closed ({_close_code(exc) or 'no code'})")
        except Exception as exc:
            self._store.log(f"microphone error: {exc}")
        finally:
            self._uplink_ws = None
            self._store.set_mic_open(False)
            self._drain_mic_queue()

    def _drain_mic_queue(self) -> None:
        while True:
            try:
                self._mic_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def set_mic_streaming(self, streaming: bool) -> None:
        if streaming == self._want_uplink:
            return
        self._want_uplink = streaming
        if streaming:
            if self._announced_connected:
                self._spawn(self._uplink_loop(self.endpoint), "uplink")
        else:
            socket = self._uplink_ws
            self._drain_mic_queue()
            if socket is not None:
                try:
                    await socket.close()
                except Exception:
                    pass

    def offer_frame(self, pcm: bytes) -> None:
        """Called from the audio thread, via the loop. Drops when behind."""
        if not self._want_uplink:
            return
        try:
            self._mic_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            self._store.count_dropped()

    # ── client -> server ─────────────────────────────────────────────────────

    async def send_command(self, text: str) -> None:
        await self._send({"type": P.CMD_COMMAND, "text": text})

    async def send_interrupt(self) -> None:
        # The client must clear its own playback buffer too: the server drops
        # what it has queued for us, but it cannot reach into this process.
        self._speaker.flush()
        await self._send({"type": P.CMD_INTERRUPT})

    async def send_confirmation(self, cid: str, confirmed: bool) -> None:
        # A decision, never an action. What runs is decided on the server
        # against its own pending request; an id that is stale does nothing.
        await self._send(
            {
                "type": P.CMD_CONFIRMATION_RESPONSE,
                "id": cid,
                "confirmed": bool(confirmed),
            }
        )

    async def _send(self, payload: dict) -> None:
        socket = self._events_ws
        if socket is None:
            self._store.log("not sent — no link")
            return
        try:
            await socket.send(json.dumps(payload))
        except Exception as exc:
            self._store.log(f"send failed: {exc}")

    # ── teardown ─────────────────────────────────────────────────────────────

    async def disconnect(self) -> None:
        self._closing = True
        self._want_uplink = False
        await self._teardown()

    async def _teardown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._events_ws = None
        self._uplink_ws = None
        self._sockets_open.clear()
        self._announced_connected = False
        self._store.set_mic_open(False)
        self._drain_mic_queue()


def _parse_iso(value: object) -> float:
    """ISO-8601 from `datetime.now().isoformat()` -> epoch seconds, once.

    Parsed on arrival rather than on display: the history is repainted far more
    often than it grows.
    """
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


class NetworkWorker:
    """The asyncio loop, its thread, and the reconnect policy driving it.

    Every method below is safe to call from the Qt thread.
    """

    def __init__(self, settings: Settings, store: JarvisStore) -> None:
        self._settings = settings
        self._store = store

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

        self.speaker = Speaker(
            device_name=settings.output_device,
            on_level=store.set_speaker_level,
            on_played=store.count_played,
            on_error=store.log,
        )
        self.microphone: Microphone | None = None

        self._client = JarvisClient(
            settings=settings,
            store=store,
            speaker=self.speaker,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )
        self._reconnect = ReconnectManager(
            client=self._client,
            on_link=self._on_link,
            on_retry=store.set_retry,
            on_note=store.log,
            probe_target=lambda: (
                (self._settings.host, self._settings.port)
                if self._settings.endpoint.is_usable
                else None
            ),
        )

    # ── thread lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="JarvisNetwork", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._reconnect.stop(), loop)
        try:
            future.result(timeout=5.0)
        except Exception:
            pass
        self.speaker.close()
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None

    def _submit(self, coro) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            pass

    # ── callbacks from the reconnect loop ────────────────────────────────────

    def _on_link(self, link: LinkState, error: str | None) -> None:
        self._store.set_link(link, error)
        if link is not LinkState.CONNECTED:
            self._store.set_assistant(AssistantState.UNKNOWN)

    def _on_connected(self) -> None:
        self._reconnect.note_connected()
        self._store.log("connected")

    def _on_disconnected(self, reason: str, fatal: bool) -> None:
        self.speaker.flush()
        self._reconnect.note_disconnected(reason, fatal)

    # ── the public surface, all thread-safe ──────────────────────────────────

    def connect(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._reconnect.start)

    def disconnect(self) -> None:
        self._submit(self._reconnect.stop())

    def set_mic_streaming(self, streaming: bool) -> None:
        self._submit(self._client.set_mic_streaming(streaming))

    def send_command(self, text: str) -> None:
        self._submit(self._client.send_command(text))

    def send_interrupt(self) -> None:
        self._submit(self._client.send_interrupt())

    def send_confirmation(self, cid: str, confirmed: bool) -> None:
        self._submit(self._client.send_confirmation(cid, confirmed))

    def offer_frame(self, pcm: bytes) -> None:
        """From the audio thread. `call_soon_threadsafe` is the whole bridge."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._client.offer_frame, pcm)
        except RuntimeError:
            # The loop is shutting down. A dropped frame during teardown is not
            # worth a traceback on the audio thread.
            pass
