"""
pilot/screen.py — la capture que le modèle regarde.

mss (Windows, macOS, X11) ; sous Wayland, où une application ne lit pas l'écran
des autres, l'outil du bureau : grim (Sway, Hyprland…), gnome-screenshot,
spectacle (KDE). La capture est réduite (MAX_WIDTH) et compressée en JPEG :
c'est l'image envoyée au modèle à chaque étape.

La zone capturée est l'écran qui contient la fenêtre active ; les coordonnées
données au modèle (0..1000) sont relatives à cette zone (model.to_norm).
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

MAX_WIDTH = 1280


def wayland() -> bool:
    return sys.platform.startswith("linux") and (os.environ.get("XDG_SESSION_TYPE") == "wayland"
                                                 or bool(os.environ.get("WAYLAND_DISPLAY")))


def monitor_for(rect: tuple[int, int, int, int], monitors: list[dict]) -> tuple[int, int, int, int]:
    """L'écran qui contient le centre de `rect` (format mss : left, top, width, height). Pur."""
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    for m in monitors:
        if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
            return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]
    m = monitors[0]
    return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]


def _jpeg(img) -> bytes:
    from PIL import Image
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=70)
    return buf.getvalue()


def capture(window_rect: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int], bytes, str]:
    """(zone capturée, JPEG, note). Ne lève pas : une capture impossible rend b"" et dit pourquoi."""
    try:
        from PIL import Image
    except ImportError:
        return (0, 0, 1920, 1080), b"", "Pillow absent : pas de capture"
    if wayland():
        for cmd in (["grim", "{out}"], ["gnome-screenshot", "-f", "{out}"], ["spectacle", "-b", "-n", "-o", "{out}"]):
            if not shutil.which(cmd[0]):
                continue
            out = os.path.join(tempfile.mkdtemp(), "screen.png")
            try:
                subprocess.run([c.replace("{out}", out) for c in cmd], timeout=10, capture_output=True)
                img = Image.open(out)
                return (0, 0, img.width, img.height), _jpeg(img), ""
            except Exception:
                continue
        return (0, 0, 1920, 1080), b"", "Wayland : installez grim (wlroots), gnome-screenshot ou spectacle"
    try:
        import mss
        with mss.mss() as sct:
            zone = monitor_for(window_rect, sct.monitors[1:] or sct.monitors)
            shot = sct.grab({"left": zone[0], "top": zone[1], "width": zone[2] - zone[0], "height": zone[3] - zone[1]})
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        return zone, _jpeg(img), ""
    except Exception as e:
        return (0, 0, 1920, 1080), b"", f"capture impossible : {type(e).__name__}"
