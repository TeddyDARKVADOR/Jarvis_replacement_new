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
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .protocol import ServerEndpoint

APP_DIR_NAME = "JarvisDesktop"
SETTINGS_FILE = "settings.json"


def settings_dir() -> Path:
    if sys.platform.startswith("linux"):
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    elif sys.platform == "darwin":
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / "Library" / "Application Support")
    else:
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

    # ── the HUD ──────────────────────────────────────────────────────────────
    #: The user's colour. Everything decorative is drawn in it; the colours that
    #: say what JARVIS is doing (listening, thinking, error) stay fixed.
    accent: str = "#7DD3FC"
    sound: bool = True
    sound_volume: float = 0.35
    #: Open maximised; otherwise at `window_geometry`, [x, y, w, h] logical px.
    maximized: bool = True
    window_geometry: list | None = None
    #: "auto", or a tier forced by hand, 0 (ULTRA) .. 4 (MINIMAL).
    quality: object = "auto"
    #: The tier the performance governor settled on last time on this PC.
    quality_tier: int | None = None
    #: Copying text offers to translate, summarise, explain or correct it.
    clipboard_assist: bool = True

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

    # The HUD's look used to live in a second file, hud.json. Folded in once:
    # one machine, one settings file, whichever mode JARVIS runs in.
    legacy = path.with_name("hud.json")
    if legacy.is_file():
        try:
            old = json.loads(legacy.read_text(encoding="utf-8"))
            for new, key in (("accent", "accent"), ("sound", "sound"),
                             ("sound_volume", "volume"),
                             ("maximized", "maximized"), ("window_geometry", "geometry"),
                             ("quality", "quality"), ("quality_tier", "quality_tier")):
                if key in old and new not in data:
                    data[new] = old[key]
        except Exception:
            pass

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
