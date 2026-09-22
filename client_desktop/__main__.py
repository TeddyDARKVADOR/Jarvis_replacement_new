"""
Entry point.

```
python -m client_desktop              the panel
python -m client_desktop --debug      the panel, developer window open
python -m client_desktop --selftest   no window, no network, no microphone
python -m client_desktop --autostart on|off
```
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="client_desktop",
        description="JARVIS Desktop — a MARK LIII terminal for this workstation.",
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="run the offline checks and exit",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="open the Developer / Debug window at startup",
    )
    parser.add_argument(
        "--autostart", choices=("on", "off"),
        help="register or remove the Windows startup entry, then exit",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        from .selftest import run

        return 0 if run() else 1

    if args.autostart:
        from . import autostart

        ok, detail = autostart.enable() if args.autostart == "on" else autostart.disable()
        print(f"{'OK' if ok else 'FAILED'}: {detail}")
        return 0 if ok else 1

    return _run_app(open_debug=args.debug)


def _run_app(open_debug: bool) -> int:
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from .app import JarvisDesktop
    from .config import load
    from .single_instance import SingleInstance

    # The guard comes before the QApplication: building a Qt app only to throw
    # it away shows a window frame for a moment on a slow machine, which is
    # exactly the "did it start twice?" flicker this is meant to prevent.
    guard = SingleInstance()
    if not guard.acquire():
        guard.signal_existing()
        print("JARVIS est déjà en cours d'exécution — instance existante affichée.")
        return 0

    # Before the QApplication, and it has to be: Chromium builds its scheme
    # registry once at startup, so a scheme declared afterwards is accepted by
    # the API and then ignored. This is what lets the 3D body be served from a
    # real origin instead of file://, and it costs nothing when the body is off
    # or WebEngine is absent — see `ui/avatar_scheme.py`.
    from .ui import avatar_scheme

    avatar_scheme.register()

    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS Desktop")
    app.setOrganizationName("MARK LIII")
    # The panel can be closed; the client stays in the tray. Without this,
    # hiding the panel would quit — and an assistant that stops existing when
    # you tidy your screen is not a permanent assistant.
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("Warning: no system tray on this session — notifications will be lost.")

    settings = load()
    if open_debug:
        settings.open_debug_at_start = True

    desktop = JarvisDesktop(settings)
    guard.listen(desktop.tray.show_panel.emit)
    desktop.start()
    if settings.open_debug_at_start:
        desktop._show_debug()

    try:
        return app.exec()
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
