"""
Entry point for the HUD.

```
python -m client_desktop.hud              the HUD
python -m client_desktop.hud --debug      the HUD, developer window open
python -m client_desktop.hud --mini       start as the small corner orb
python -m client_desktop.hud --selftest   offline checks, no window
```

It runs the *same* controller as `python -m client_desktop` — the network
worker, the wake word, the gate, the tray, the debug window — and swaps one
class: the controller's `JarvisPanel` reference is pointed at `HudWindow`
before the controller is built. No file of the existing client is edited, and
`python -m client_desktop` still starts the panel exactly as before.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="client_desktop.hud",
                                     description="JARVIS — HUD holographique.")
    parser.add_argument("--selftest", action="store_true", help="offline checks, then exit")
    parser.add_argument("--debug", action="store_true", help="open the debug window too")
    parser.add_argument("--mini", action="store_true", help="start as the corner orb")
    args = parser.parse_args(argv)

    if args.selftest:
        from .selftest import run
        return 0 if run() else 1
    return _run(open_debug=args.debug, mini=args.mini)


def _run(open_debug: bool, mini: bool) -> int:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from client_desktop.single_instance import SingleInstance

    guard = SingleInstance()
    if not guard.acquire():
        guard.signal_existing()
        print("JARVIS est déjà en cours d'exécution — instance existante affichée.")
        return 0

    # Both schemes before the QApplication: Chromium reads its scheme registry
    # once. `jarvis://` is the face's, `jarvis-hud://` is ours.
    from client_desktop.ui import avatar_scheme
    from . import scheme

    avatar_scheme.register()
    if not scheme.register():
        print("PyQt6-WebEngine est requis pour le HUD : pip install PyQt6-WebEngine")
        guard.release()
        return 1

    _gpu_flags()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS Desktop")
    app.setOrganizationName("MARK LIII")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("Warning: no system tray on this session — notifications will be lost.")

    from client_desktop import app as app_module
    from client_desktop.config import load
    from .window import HudWindow

    # The one substitution. `JarvisDesktop.__init__` builds `JarvisPanel(...)`
    # by name from its own module; pointing that name at the HUD is the whole
    # integration, and the controller's code runs untouched.
    app_module.JarvisPanel = HudWindow  # type: ignore[misc]

    settings = load()
    if open_debug:
        settings.open_debug_at_start = True
    if mini:
        settings.start_collapsed = True

    desktop = app_module.JarvisDesktop(settings)
    hud = desktop.panel
    hud.attach_store(desktop.store)
    _wire_settings(desktop, hud)
    # First run, never paired: `start()` opens the pairing screen by calling
    # `_show_debug`. In the HUD that screen is the settings page. Rebinding the
    # attribute only changes that call — the tray's "Developer / Debug" entry
    # was connected to the original method and still opens the debug window.
    desktop._show_debug = lambda: hud.open_settings("connexion")
    guard.listen(desktop.tray.show_panel.emit)
    desktop.start()
    if settings.open_debug_at_start:
        desktop.debug.show()

    try:
        return app.exec()
    finally:
        guard.release()


def _wire_settings(desktop, hud) -> None:  # noqa: ANN001
    """Apply what the settings page changed, through the controller's own parts.

    The controller was written before the page and has no slot for most of
    these; everything it needs already exists on its objects, so this applies
    each change where it lives instead of editing `app.py`.
    """
    import threading

    from client_desktop.config import save
    from client_desktop.hud.settings import RECONNECT_KEYS
    from client_desktop.wake import engine_available

    s = desktop.settings

    def on_saved(changed: list) -> None:
        save(s)
        changed = set(changed)
        if changed:
            desktop.store.log("Paramètres modifiés : " + ", ".join(sorted(changed)))

        if "gate_seconds" in changed:
            desktop.gate._seconds = s.gate_seconds

        if "input_device" in changed:
            mic = desktop.microphone
            was_running = mic.running
            mic._device_name = s.input_device
            if was_running:
                mic.stop()
                mic.start()

        if changed & {"wake_word_enabled", "wake_word_threshold"}:
            desktop.wake._threshold = s.wake_word_threshold
            desktop.wake.stop()
            if s.wake_word_enabled and engine_available():
                if desktop.wake.start():
                    desktop.microphone.start()
            elif not desktop.gate.is_open:
                desktop.microphone.stop()

        if not s.is_configured:
            desktop.store.log("Non appairé — renseignez l'hôte et le jeton.")
        elif changed & RECONNECT_KEYS or not desktop.store.snapshot().connected:
            desktop.worker.connect()

    def on_install() -> None:
        # pip + a model download: never on the GUI thread.
        threading.Thread(target=desktop._install_wake_word, name="wake-install", daemon=True).start()
        desktop.store.log("Installation du mot d'éveil en cours…")

    hud.settings_saved.connect(on_saved)
    hud.wake_install_requested.connect(on_install)


def _gpu_flags() -> None:
    """Let Chromium use the GPU it has, before it starts.

    Chromium refuses hardware acceleration on GPUs/drivers in its blocklist and
    falls back to SwiftShader — WebGL computed on the CPU. On such a PC the HUD
    is slow whatever the governor does, because every frame is software. Most
    blocklist entries are old driver bugs that do not affect three.js, which is
    why Electron apps routinely pass the same flag.

    `JARVIS_HUD_SAFE_GPU=1` turns this off, for the rare driver that really
    misbehaves. Flags the user already set are kept.
    """
    import os

    if os.environ.get("JARVIS_HUD_SAFE_GPU", "").strip() not in ("", "0", "false"):
        return
    current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    wanted = ["--ignore-gpu-blocklist", "--enable-gpu-rasterization"]
    extra = [f for f in wanted if f not in current]
    if extra:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join([current, *extra]).strip()


if __name__ == "__main__":
    raise SystemExit(main())
