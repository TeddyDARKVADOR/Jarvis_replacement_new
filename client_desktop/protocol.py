"""
The wire contract with MARK LIII — read off the server, not invented here.

A direct port of `client-android/.../net/Protocol.kt`. Every endpoint below
already existed before either client did: three ship with the desktop assistant
(`dashboard/server.py`), two were added by the headless layer (`server/api.py`).

```
 POST /api/device-login   {"device_token"} -> {"ok","token","key"}
 GET  /health             liveness, no auth
 GET  /status             Bearer <token>
 WS   /ws                 ?token=   JSON events, both directions
 WS   /ws/phone-audio     ?token=   binary PCM 16 kHz  ->  Gemini
 WS   /ws/phone-out       ?token=   binary PCM 24 kHz  <-  Gemini
```

The endpoints are named for a phone because a phone asked for them first. They
are not phone-specific: `/ws/phone-audio` is "a microphone that is not the
server's own", which is exactly what this workstation is.

Source of truth, in order: `dashboard/server.py`, `server/api.py`, `main.py`,
then `client-android/PROTOCOL.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

# ── Uplink: this microphone -> MARK LIII -> Gemini ───────────────────────────
#
# 16 kHz, mono, signed 16-bit little-endian, raw. No header, no framing, no
# container: `dashboard/server.py` does receive_bytes() and hands the buffer
# straight to Gemini as {"mime_type": "audio/pcm"}.
UPLINK_SAMPLE_RATE = 16_000

# 1024 samples = 2048 bytes ~= 64 ms, which is main.py's CHUNK_SIZE exactly.
# Matching it means the server sees one format whichever microphone is talking.
UPLINK_FRAME_SAMPLES = 1024
UPLINK_FRAME_BYTES = UPLINK_FRAME_SAMPLES * 2

# ── Downlink: Gemini -> MARK LIII -> these speakers ──────────────────────────
#
# Announced by the server in the first (text) frame of /ws/phone-out:
#   {"type":"audio_format","sample_rate":24000,"channels":1,"encoding":"pcm_s16le"}
#
# This constant is only what to assume until that frame arrives. Building the
# player from the announced value rather than from this number is the whole
# point of the announcement — hardcoding 24000 means wrong-pitch audio the day
# RECEIVE_SAMPLE_RATE changes in main.py.
DOWNLINK_SAMPLE_RATE_DEFAULT = 24_000

CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2

# ── Heartbeat ────────────────────────────────────────────────────────────────
#
# There is no application-level heartbeat in the protocol and none is added:
# uvicorn[standard] pings on its own and `websockets` answers automatically.
# What matters is the OTHER direction. A workstation that loses Wi-Fi, sleeps,
# or has its Tailscale tunnel drop gets no TCP reset — the socket stays open and
# looks healthy for ever. Our own ping is the whole mechanism.
PING_SECONDS = 20.0

# How long to wait for a pong before declaring the socket dead. Deliberately
# shorter than the ping interval is wrong (a slow VPS would flap); one interval
# plus margin is the usable value.
PING_TIMEOUT_SECONDS = 20.0

# ── Server -> client event types seen on /ws ─────────────────────────────────
EV_LOG = "log"                      # {"speaker":"user"|"jarvis","text","ts"}
EV_STATUS = "status"                # {"state":"active"|"sleeping"}
EV_SYS = "sys"                      # {"text"} - system line
EV_FILE = "file_received"           # {"name","size","saved_to"}
EV_JARVIS_STATE = "jarvis_state"    # {"state":"LISTENING"|"THINKING"|...}
EV_CONTENT = "content"              # {"title","text"}
EV_CONFIRM = "confirm"              # {"id","title","detail","timeout_s"}
EV_CONFIRM_HIDE = "confirm_hide"    # no payload - take the banner down
EV_AUDIO_FORMAT = "audio_format"    # first frame of /ws/phone-out
EV_AVATAR = "avatar"                # {"ts", "directive": {...}} - see below

#: How long an `avatar` directive is worth obeying, counted from the `ts` the
#: server stamped on it.
#:
#: The guard is not optional. `/ws` replays the last 50 events to every client
#: that connects, which is right for a transcript and wrong for a face: a laptop
#: that reconnects at noon would otherwise put on the expression JARVIS chose at
#: nine and wear it for twenty-five seconds, as a reaction to nothing.
#:
#: This is a deliberate copy of `plugins.presence.FRESH_S`, which is itself
#: a copy of `presence.director.INTENT_TTL_S`. The client does not import from
#: the server — it has to work against a host whose filesystem it has never seen
#: — so the number is mirrored and a check in `selftest.py` fails if the copies
#: ever drift apart.
AVATAR_FRESH_SECONDS = 25.0

# ── Client -> server ─────────────────────────────────────────────────────────
CMD_COMMAND = "command"
CMD_INTERRUPT = "interrupt"
CMD_CONFIRMATION_RESPONSE = "confirmation_response"

# ── /ws/device ───────────────────────────────────────────────────────────────
#
# Server -> client, and the answer back. Unknown types are ignored by both ends
# (PROTOCOL.md section 4), so a client or server that predates these carries on
# without noticing them.
EV_DEVICE_COMMAND = "device_command"    # {"id","target_device_id","action","parameters"}
CMD_DEVICE_RESULT = "device_result"     # {"id","result"}
CMD_MIC_STATE = "mic"                   # {"open": bool} - who is speaking
CMD_PING = "ping"

# WebSocket close code the server uses to refuse a stale bearer. Not fatal: the
# correct response is to call /api/device-login again. Only a 401 on that call
# means the credential itself is wrong.
CLOSE_UNAUTHORISED = 4001


class AuthRejected(Exception):
    """The device token itself was refused — a `401` on `/api/device-login`.

    This lives here, with the contract it comes from, because it is the one
    failure the reconnect loop must treat differently from every other: the
    bearer expiring (close 4001) is routine and means log in again, but a
    refused *device token* will be refused identically for ever. Retrying it is
    a loop that can only ever fail, so it is the single condition that stops
    the client and says so.
    """


@dataclass(frozen=True)
class ServerEndpoint:
    """Where MARK LIII is, and how to address each of its five doors."""

    host: str
    port: int = 8000
    # MARK LIII serves TLS only when `config/certs/` is populated. Over
    # Tailscale the tunnel already encrypts everything; see the transport note
    # in PROTOCOL.md section 6.
    use_tls: bool = False

    @property
    def _http_scheme(self) -> str:
        return "https" if self.use_tls else "http"

    @property
    def _ws_scheme(self) -> str:
        return "wss" if self.use_tls else "ws"

    @property
    def http_base(self) -> str:
        return f"{self._http_scheme}://{self.host}:{self.port}"

    @property
    def ws_base(self) -> str:
        return f"{self._ws_scheme}://{self.host}:{self.port}"

    @property
    def device_login(self) -> str:
        return f"{self.http_base}/api/device-login"

    @property
    def health(self) -> str:
        return f"{self.http_base}/health"

    @property
    def status(self) -> str:
        return f"{self.http_base}/status"

    def events(self, bearer: str) -> str:
        return f"{self.ws_base}/ws?token={quote(bearer, safe='')}"

    def mic_uplink(self, bearer: str) -> str:
        return f"{self.ws_base}/ws/phone-audio?token={quote(bearer, safe='')}"

    def audio_downlink(self, bearer: str) -> str:
        return f"{self.ws_base}/ws/phone-out?token={quote(bearer, safe='')}"

    # ── device identity and routing (server/device_api.py) ───────────────────
    #
    # Two routes added beside the five above, never replacing them. A server
    # that does not have them answers 404 and the client carries on exactly as
    # it did before — which is how this stays compatible with an Oracle that
    # has not been updated yet.

    @property
    def device_register(self) -> str:
        return f"{self.http_base}/api/device-register"

    def device_channel(self, bearer: str, device_id: str) -> str:
        return (
            f"{self.ws_base}/ws/device?token={quote(bearer, safe='')}"
            f"&device_id={quote(device_id, safe='')}"
        )

    @property
    def is_usable(self) -> bool:
        return bool(self.host.strip()) and 1 <= self.port <= 65535
