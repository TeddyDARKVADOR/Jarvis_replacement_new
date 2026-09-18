"""
Where this client keeps its settings, and why not in the repository.

`%LOCALAPPDATA%\\JarvisDesktop\\settings.json`.

Not `config/` next to `api_keys.json`, for two reasons. The device token is a
password — it is full control of JARVIS — and a secret inside a git working
tree is one `git add -A` away from being published, whatever `.gitignore` says
today. And this file is *per-installation* state, not part of the program: a
`git clean -xdf` should not log the workstation out.

`%LOCALAPPDATA%` is ACL'd to the user by Windows, so the token is no more
exposed than the user's own browser cookies. It is not encrypted: a process
already running as this user can read it, and so can anything that could read a
DPAPI blob the same process would have to be able to decrypt.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .placement import Anchor, PlacementConfig, PlacementMode, Rect
from .protocol import ServerEndpoint

APP_DIR_NAME = "JarvisDesktop"
SETTINGS_FILE = "settings.json"


def settings_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_DIR_NAME


def settings_path() -> Path:
    return settings_dir() / SETTINGS_FILE


@dataclass
class Settings:
    # ── where MARK LIII is ───────────────────────────────────────────────────
    #
    # The MagicDNS name, not a 100.x address: it survives a node being
    # re-registered, and it is what the deployment runbook tells you to use.
    host: str = ""
    port: int = 8000
    use_tls: bool = False
    #: From `python -m server.run_headless --pairing` on the VPS.
    device_token: str = ""

    # ── identity ─────────────────────────────────────────────────────────────
    #: This machine's logical id, generated once on first run and then kept.
    #: It is what Oracle routes on — not the hostname, which two machines can
    #: share and which changes when the user renames their laptop.
    device_id: str = ""
    #: What a question to the user calls this machine ("sur le PC de travail ?").
    #: For humans only: routing never looks at it.
    device_name: str = ""

    # ── microphone ───────────────────────────────────────────────────────────
    #
    # None means "whatever Windows calls the default", which is the right answer
    # on a laptop whose microphone changes when a headset is plugged in.
    input_device: str | None = None
    output_device: str | None = None

    # ── the gate ─────────────────────────────────────────────────────────────
    wake_word_enabled: bool = True
    wake_word_threshold: float = 0.5
    #: How long the microphone stays open after a wake, refreshed by activity.
    #: Long enough to finish a sentence and be answered, short enough that a
    #: forgotten gate is not an open microphone on a work machine.
    gate_seconds: float = 25.0
    #: Start with the gate shut. A client that came up streaming would be a
    #: microphone that opened itself at every login.
    mic_open_at_start: bool = False

    # ── the panel ────────────────────────────────────────────────────────────
    #: Fraction of the reference width, for a LEFT/RIGHT anchor. 0.20 is the
    #: brief; `placement` clamps it to a readable pixel range, because 20 % of a
    #: 1366-wide laptop is not the same object as 20 % of an ultrawide.
    width_fraction: float = 0.20
    #: Fraction of the reference height, for a TOP/BOTTOM anchor — where the
    #: panel becomes a wide strip rather than a tall column.
    height_fraction: float = 0.28

    # ── where it goes ────────────────────────────────────────────────────────
    #: "screen" | "window" | "follow" | "free". See `placement.PlacementMode`.
    placement_mode: str = "screen"
    #: "left" | "right" | "top" | "bottom".
    anchor: str = "right"
    margin: int = 8
    snap_enabled: bool = True
    snap_distance: int = 16
    #: Topmost is a *visibility* setting and nothing else — it never implies
    #: activation. See the note in `ui/panel.py`.
    always_on_top: bool = True

    #: The FREE position, remembered. `free_screen` is `QScreen.name()`, kept so
    #: a position can be recognised as belonging to a monitor that is no longer
    #: attached rather than silently restored into empty space.
    free_x: int | None = None
    free_y: int | None = None
    free_w: int | None = None
    free_h: int | None = None
    free_screen: str = ""

    start_collapsed: bool = False
    notifications: bool = True
    #: Only ever shown when explicitly asked for. The debug window is a separate
    #: surface on purpose — see the brief's "mode technique séparé".
    open_debug_at_start: bool = False

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def endpoint(self) -> ServerEndpoint:
        return ServerEndpoint(host=self.host, port=self.port, use_tls=self.use_tls)

    @property
    def placement(self) -> PlacementConfig:
        """The stored strings, turned into the enums the arithmetic uses.

        Unknown values fall back rather than raise: a settings file hand-edited
        to `"anchor": "diagonal"` must still produce a client that starts.
        """
        try:
            mode = PlacementMode(self.placement_mode)
        except ValueError:
            mode = PlacementMode.SCREEN
        try:
            anchor = Anchor(self.anchor)
        except ValueError:
            anchor = Anchor.RIGHT
        return PlacementConfig(
            mode=mode,
            anchor=anchor,
            margin=max(0, int(self.margin)),
            width_fraction=float(self.width_fraction),
            height_fraction=float(self.height_fraction),
            snap_enabled=bool(self.snap_enabled),
            snap_distance=max(0, int(self.snap_distance)),
        )

    @property
    def free_rect(self) -> Rect | None:
        if None in (self.free_x, self.free_y, self.free_w, self.free_h):
            return None
        return Rect(int(self.free_x), int(self.free_y), int(self.free_w), int(self.free_h))  # type: ignore[arg-type]

    def remember_free(self, rect: Rect, screen_name: str = "") -> None:
        self.free_x, self.free_y = rect.x, rect.y
        self.free_w, self.free_h = rect.w, rect.h
        self.free_screen = screen_name

    @property
    def is_configured(self) -> bool:
        return bool(self.host.strip()) and bool(self.device_token.strip())

    def ensure_identity(self) -> bool:
        """Give this machine an id and a name if it has none. True if changed.

        Called once at startup rather than generated on the fly every time: an
        id that changed between runs would register a new device on every login
        and leave the registry full of ghosts that are all 'connected'.
        """
        changed = False
        if not self.device_id.strip():
            from .device import new_device_id

            self.device_id = new_device_id()
            changed = True
        if not self.device_name.strip():
            from .device import default_display_name

            self.device_name = default_display_name()
            changed = True
        return changed


def load() -> Settings:
    """Read the settings, falling back to defaults for anything absent.

    Never raises. A corrupt or half-written file yields defaults rather than a
    client that will not start — this thing is meant to come up at every login
    without a human present.
    """
    path = settings_path()
    data: dict[str, object] = {}
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    # `dock` was the only placement setting before there were four modes.
    # Migrated rather than dropped: the filter below discards unknown keys, so
    # without this an existing user's left-hand panel would silently jump to
    # the right on the first run of the new version.
    if "dock" in data and "anchor" not in data:
        data["anchor"] = data["dock"]

    known = {f.name for f in fields(Settings)}
    filtered = {k: v for k, v in data.items() if k in known}
    try:
        return Settings(**filtered)  # type: ignore[arg-type]
    except Exception:
        return Settings()


def save(settings: Settings) -> bool:
    """Write the settings atomically. Returns False rather than raising.

    Atomically because this file holds the device token and is written while the
    client is running: a crash midway through a plain write leaves a truncated
    file, and the next login would come up unpaired for no reason the user could
    see.
    """
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(asdict(settings), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp, path)
        return True
    except Exception:
        return False
