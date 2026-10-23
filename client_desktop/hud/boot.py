"""
Everything that must happen before the `QApplication` exists — once, the same
way, whichever brain the HUD is showing (Oracle or this PC).

Chromium reads its scheme registry and its command-line flags once, when the
first web view's process starts. Anything declared after that is accepted by
the API and then ignored, silently. So both entry points (`client_desktop/
__main__.py` and `local_hud.py`) call `prepare()` on the line above
`QApplication(...)`, and nowhere else.
"""

from __future__ import annotations

import os


def gpu_flags() -> None:
    """Let Chromium use the GPU it has.

    Chromium refuses hardware acceleration on GPUs/drivers in its blocklist and
    falls back to SwiftShader — WebGL computed on the CPU. On such a PC the HUD
    is slow whatever the governor does, because every frame is software. Most
    blocklist entries are old driver bugs that do not affect three.js, which is
    why Electron apps routinely pass the same flag.

    `MATTHEW_ASSISTANT_HUD_SAFE_GPU=1` turns this off, for the rare driver that really
    misbehaves. Flags the user already set are kept.
    """
    if (os.environ.get("MATTHEW_ASSISTANT_HUD_SAFE_GPU") or os.environ.get("JARVIS_HUD_SAFE_GPU", "")).strip() not in ("", "0", "false"):
        return
    current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    wanted = ["--ignore-gpu-blocklist", "--enable-gpu-rasterization"]
    extra = [f for f in wanted if f not in current]
    if extra:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join([current, *extra]).strip()


def prepare() -> bool:
    """Schemes, GPU flags, DPI policy. False when PyQt6-WebEngine is missing."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    # QtWebEngineWidgets must be loaded before QApplication is constructed.
    # Importing HudWindow afterwards is too late: QtWebEngine then rejects the
    # view unless shared OpenGL contexts were enabled before the app existed.
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    from . import scheme

    if not scheme.register():         # matthew-assistant-hud:// — the HUD page
        return False
    gpu_flags()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    return True


MISSING_WEBENGINE = "PyQt6-WebEngine est requis pour le HUD : pip install PyQt6-WebEngine"
