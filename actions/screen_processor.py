"""
Screen & webcam capture for Matthew vision.

Provides the two capture entry points main.py uses — `_capture_screen()` and
`_capture_camera()` — plus their helpers (compression, camera auto-detection,
config access). main.py grabs a frame here on demand, then injects it into the
main Gemini Live session; there is no separate vision session here.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from config import api_key_path

import numpy as np

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = api_key_path(_BASE)


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _capture_screen(display: str = "primary") -> tuple[bytes, str]:

    # MSS uses X11 APIs on Linux. On a Wayland desktop those APIs can be
    # unavailable or return black pixels; KDE's Spectacle talks to KWin and
    # captures the actual compositor output. Its full-screen mode includes
    # every connected monitor, so the vision model can inspect either one.
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        spectacle = shutil.which("spectacle")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if spectacle and ("kde" in desktop or "plasma" in desktop):
            path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    path = Path(tmp.name)
                completed = subprocess.run(
                    [spectacle, "--background", "--nonotify", "--fullscreen",
                     "--output", str(path)],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                if completed.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
                    detail = (completed.stderr or completed.stdout or "aucune image produite").strip()
                    raise RuntimeError(f"Spectacle n'a pas pu capturer le bureau KDE Wayland : {detail}")
                print(f"[Vision] KDE Wayland: captured all monitors via Spectacle (requested {display}).")
                return _compress(path.read_bytes(), "PNG")
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("La capture Spectacle du bureau Wayland a dépassé 20 secondes.") from exc
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)
        raise RuntimeError(
            "La capture directe MSS n'est pas fiable sous Wayland. "
            "Sur KDE, installez Spectacle ; sur les autres bureaux, il faut utiliser "
            "la capture d'écran via XDG Desktop Portal/PipeWire."
        )

    if not _MSS:
        raise RuntimeError("mss is not installed. Run: pip install mss")

    with mss.mss() as sct:
        monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
        display = (display or "primary").strip().lower()
        if display in ("all", "combined", "both"):
            target = monitors[0]
        elif display in ("secondary", "second", "2"):
            if len(monitors) < 3:
                raise RuntimeError("Aucun écran secondaire détecté par le système.")
            target = monitors[2]
        else:
            target = monitors[1] if len(monitors) > 1 else monitors[0]
        shot     = sct.grab(target)

        # Some Windows display-driver configurations expose a secondary
        # monitor through MSS but return a black frame for it (common with
        # duplicated displays). In that mode the primary contains the same
        # image, so fall back only when the secondary is effectively all black
        # and the primary is not.
        if display in ("secondary", "second", "2"):
            secondary_pixels = np.frombuffer(shot.rgb, dtype=np.uint8)
            if secondary_pixels.size and float(secondary_pixels.mean()) < 2.0:
                primary = sct.grab(monitors[1])
                primary_pixels = np.frombuffer(primary.rgb, dtype=np.uint8)
                if primary_pixels.size and float(primary_pixels.mean()) >= 2.0:
                    print("[Vision] Secondary display returned a black frame; using primary capture (mirrored display fallback).")
                    shot = primary
        png      = mss.tools.to_png(shot.rgb, shot.size)

    return _compress(png, "PNG")


def _cv2_backend() -> int:
    """Return the best OpenCV camera backend for the current OS."""
    if not _CV2:
        return 0
    os_name = _get_os()
    if os_name == "windows":
        return cv2.CAP_DSHOW
    if os_name == "mac":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:

    backend = _cv2_backend()
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] ✅ Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    index   = _get_camera_index()
    backend = _cv2_backend()
    cap     = cv2.VideoCapture(index, backend)

    if not cap.isOpened():
        raise RuntimeError(f"Camera index {index} could not be opened.")

    for _ in range(10):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera returned no frame.")

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"
