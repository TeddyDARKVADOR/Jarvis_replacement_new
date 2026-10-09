"""
Double-click (or Run-key) launcher for the HUD — `pythonw`, no console.

Same trick as `client_desktop/launch.pyw`: put the repository root on
`sys.path` so the package is found from any working directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from client_desktop.hud.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
