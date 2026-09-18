"""
server/devices.py — who is connected, and what each one can actually do.

THE GAP THIS FILLS
    `dashboard/server.py` authenticates perfectly well and has no idea who it is
    talking to. `_tokens` is a `set[str]`; `_clients` is a `set[WebSocket]`.
    When `/ws` receives a command it does `_command_queue.put(text)` — a bare
    string. There is no field anywhere that says which machine spoke, so
    "ouvre le navigateur" from the phone and from the PC are the same nine
    bytes.

    Worse, both clients currently hold the *same* device_token, so even the
    credential cannot tell them apart.

WHAT THIS ADDS, WITHOUT CHANGING ANY OF IT
    A registry of `DeviceInfo`, keyed by a logical `device_id` that the client
    chooses once and keeps. Nothing here replaces the token check: a device is
    only ever registered by a caller that has already passed
    `dashboard._tokens`. Identity is layered *on top of* authentication, not
    substituted for it.

WHY NOT KEY ON IP, OR ON THE TOKEN
    Tailscale reassigns addresses, a laptop moves between Wi-Fi and Ethernet,
    and the phone's 100.x address is not stable across a re-registration. The
    token is worse: it is shared today and it rotates on every server restart.
    The logical id is the only thing that survives all of that.

WHY `device_type` AND NOT THE NAME
    Routing on a display name is routing on something the user can change in a
    settings field. `device_type` is a closed set the code can reason about;
    `display_name` is for humans and never for a decision.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum


class DeviceType(str, Enum):
    """The closed set routing is allowed to reason about.

    `UNKNOWN` is deliberately a member rather than an error. A client that has
    not declared itself — today, every unmodified Android build — must still be
    able to connect and talk; what it must *not* get is a silent guess about
    where its commands should run. `targeting.py` refuses to default an unknown
    origin and asks instead.
    """

    PC = "pc"
    ANDROID = "android"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: object) -> "DeviceType":
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return cls.UNKNOWN


#: How long after its last sign of life a device is considered gone, when no
#: explicit disconnect arrived. Deliberately generous: a phone on a train drops
#: its socket without a FIN, and declaring it offline too eagerly is what makes
#: "your phone is not connected" appear while the user is holding it.
STALE_AFTER_SECONDS = 90.0


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    device_type: DeviceType = DeviceType.UNKNOWN
    display_name: str = ""
    #: Capability names the client says it can execute. Never inferred here —
    #: see `capabilities.py` for why a declared capability is a promise.
    capabilities: frozenset[str] = field(default_factory=frozenset)
    connected: bool = False
    last_seen: float = 0.0
    protocol_version: int = 1

    @property
    def label(self) -> str:
        """What a log line or a question to the user should call this device."""
        return self.display_name or self.device_id

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def is_stale(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return (now - self.last_seen) > STALE_AFTER_SECONDS

    def public(self) -> dict:
        """The shape `/status` and the tests read. No secrets pass through here:
        a device never holds a token, by construction."""
        return {
            "device_id": self.device_id,
            "device_type": self.device_type.value,
            "display_name": self.display_name,
            "capabilities": sorted(self.capabilities),
            "connected": self.connected,
            "protocol_version": self.protocol_version,
        }


class DeviceRegistry:
    """Every device Oracle knows about. One instance, owned by the server.

    Thread-safe because registration arrives on the FastAPI event loop while
    routing decisions are taken from a worker thread running an action.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, DeviceInfo] = {}
        #: bearer token -> device_id. This is the only place the two are
        #: associated, and it is how a command arriving on an authenticated
        #: socket acquires an origin. Tokens are never logged or exported.
        self._token_devices: dict[str, str] = {}

    # ── registration ─────────────────────────────────────────────────────────

    def register(
        self,
        device_id: str,
        device_type: object = DeviceType.UNKNOWN,
        display_name: str = "",
        capabilities: object = (),
        protocol_version: int = 1,
        token: str | None = None,
    ) -> DeviceInfo:
        """Declare (or re-declare) a device. Idempotent.

        Re-registration replaces the capabilities wholesale rather than merging
        them: a client that has *lost* an ability — a desktop whose pyautogui
        import started failing — must be able to say so, and a merge would make
        that impossible to express.
        """
        device_id = (device_id or "").strip()
        if not device_id:
            raise ValueError("device_id is required")

        info = DeviceInfo(
            device_id=device_id,
            device_type=DeviceType.parse(
                device_type.value if isinstance(device_type, DeviceType) else device_type
            ),
            display_name=(display_name or "").strip(),
            capabilities=frozenset(str(c).strip() for c in (capabilities or ()) if str(c).strip()),
            connected=True,
            last_seen=time.monotonic(),
            protocol_version=int(protocol_version or 1),
        )
        with self._lock:
            self._devices[device_id] = info
            if token:
                self._token_devices[token] = device_id
        return info

    def bind_token(self, token: str, device_id: str) -> None:
        with self._lock:
            if token and device_id in self._devices:
                self._token_devices[token] = device_id

    def device_for_token(self, token: str) -> DeviceInfo | None:
        with self._lock:
            device_id = self._token_devices.get(token or "")
            return self._devices.get(device_id) if device_id else None

    def forget_token(self, token: str) -> None:
        with self._lock:
            self._token_devices.pop(token or "", None)

    # ── liveness ─────────────────────────────────────────────────────────────

    def touch(self, device_id: str) -> None:
        with self._lock:
            info = self._devices.get(device_id)
            if info is not None:
                self._devices[device_id] = replace(
                    info, last_seen=time.monotonic(), connected=True
                )

    def set_connected(self, device_id: str, connected: bool) -> None:
        with self._lock:
            info = self._devices.get(device_id)
            if info is not None:
                self._devices[device_id] = replace(
                    info, connected=connected, last_seen=time.monotonic()
                )

    # ── reading ──────────────────────────────────────────────────────────────

    def get(self, device_id: str) -> DeviceInfo | None:
        with self._lock:
            return self._devices.get(device_id)

    def all(self) -> list[DeviceInfo]:
        with self._lock:
            return list(self._devices.values())

    def online(self) -> list[DeviceInfo]:
        now = time.monotonic()
        with self._lock:
            return [
                info for info in self._devices.values()
                if info.connected and not info.is_stale(now)
            ]

    def online_of_type(self, device_type: DeviceType) -> list[DeviceInfo]:
        return [d for d in self.online() if d.device_type is device_type]

    def with_capability(self, capability: str, online_only: bool = True) -> list[DeviceInfo]:
        source = self.online() if online_only else self.all()
        return [d for d in source if d.can(capability)]

    def public(self) -> list[dict]:
        return [d.public() for d in self.all()]

    def summary(self) -> str:
        """One short line for a log or for Gemini's context. Never a token."""
        parts = [
            f"{d.device_type.value}:{d.label}{'' if d.connected else ' (offline)'}"
            for d in sorted(self.all(), key=lambda x: x.device_id)
        ]
        return ", ".join(parts) if parts else "aucun appareil"
