"""
The file the Windows Run key points at.

`.pyw` so it is opened by `pythonw.exe` even if someone runs it by hand, and so
no console flashes at login.

It exists because a Run entry starts in an arbitrary working directory:
`pythonw -m client_desktop` cannot find the package from `C:\\Windows\\system32`.
This puts the repository root on `sys.path` itself and then defers to the
ordinary entry point, so there is only ever one startup path to keep working.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client_desktop.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main([]))
