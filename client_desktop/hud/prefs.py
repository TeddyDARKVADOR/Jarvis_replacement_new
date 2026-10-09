"""
The HUD's own preferences — accent colour, face or reactor, sounds, geometry.

Kept apart from `config.Settings` on purpose: that file is the pairing and the
panel's placement, written only by the debug window's Save button, and the HUD
must not be the thing that rewrites it. This one is cosmetic; losing it costs a
colour.

Stored next to it, in `%LOCALAPPDATA%\\JarvisDesktop\\hud.json`, and not in
the page's localStorage — QtWebEngine's default profile is off-the-record in
Qt 6, so localStorage would be wiped at every launch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "accent": "#7DD3FC",
    "face": False,
    "sound": True,
    "volume": 0.35,
    "geometry": None,       # [x, y, w, h] in logical pixels
    "maximized": True,
    "quality": "auto",      # "auto", or a forced tier 0 (ULTRA) .. 4 (MINIMAL)
    "quality_tier": None,   # the tier the governor settled on last time
}


def _path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".config")
    return Path(base) / "JarvisDesktop" / "hud.json"


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        stored = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: data.get(k, v) for k, v in DEFAULTS.items()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
