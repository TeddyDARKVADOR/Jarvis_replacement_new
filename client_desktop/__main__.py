"""
Entry point — the HUD, connected to MARK LIII on Oracle.

```
python -m client_desktop              the HUD
python -m client_desktop --mini       start as the corner orb
python -m client_desktop --debug      the HUD, developer window open
python -m client_desktop --selftest   no window, no network, no microphone
python -m client_desktop --autostart on|off
```

`python main.py` on a desktop lands here too (see the top of main.py), and
`python main.py --local` runs the same HUD with the brain on this PC
(`local_hud.py`).
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
        description="JARVIS — le HUD de ce poste, relié à MARK LIII.",
    )
    parser.add_argument("--selftest", action="store_true", help="run the offline checks and exit")
    parser.add_argument("--debug", action="store_true", help="open the developer window at startup")
    parser.add_argument("--mini", action="store_true", help="start as the corner orb")
    parser.add_argument("--autostart", choices=("on", "off"),
                        help="register or remove the Windows startup entry, then exit")
    args = parser.parse_args(argv)

    if args.selftest:
        from .selftest import run

        return 0 if run() else 1

    if args.autostart:
        from . import autostart

        ok, detail = autostart.enable() if args.autostart == "on" else autostart.disable()
        print(f"{'OK' if ok else 'FAILED'}: {detail}")
        return 0 if ok else 1

    return _run_app(open_debug=args.debug, mini=args.mini)


def _run_app(open_debug: bool, mini: bool) -> int:
    from .hud import boot
    from .single_instance import SingleInstance

    # The guard comes before the QApplication: building a Qt app only to throw
    # it away shows a window frame for a moment on a slow machine, which is
    # exactly the "did it start twice?" flicker this is meant to prevent.
    guard = SingleInstance()
    if not guard.acquire():
        guard.signal_existing()
        print("JARVIS est déjà en cours d'exécution — instance existante affichée.")
        return 0

    if not boot.prepare():
        print(boot.MISSING_WEBENGINE)
        guard.release()
        return 1

    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from .app import JarvisDesktop
    from .config import load

    app = QApplication(sys.argv[:1])
    app.setApplicationName("JARVIS Desktop")
    app.setOrganizationName("MARK LIII")
    # The HUD can be hidden; the client stays in the tray. Without this, hiding
    # it would quit — and an assistant that stops existing when you tidy your
    # screen is not a permanent assistant.
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("Warning: no system tray on this session — notifications will be lost.")

    desktop = JarvisDesktop(load())
    desktop.start_mini = mini        # this launch only — never written to settings
    guard.listen(desktop.tray.show_hud.emit)
    desktop.start()
    if open_debug or desktop.settings.open_debug_at_start:
        desktop.debug.show()

    try:
        return app.exec()
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
