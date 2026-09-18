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
    #: Fraction of the available width. 0.20 is the brief; it is clamped to a
    #: sane pixel range at layout time for very small or very wide screens.
    width_fraction: float = 0.20
    dock: str = "right"          # "right" | "left"
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
