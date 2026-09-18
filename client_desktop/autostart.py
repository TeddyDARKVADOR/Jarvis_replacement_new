"""
Starting with Windows, without asking for anything the client does not need.

```
Windows démarre
      ↓
   réseau
      ↓
JARVIS Desktop démarre
      ↓
connexion Oracle
      ↓
   READY
```

**HKCU\\...\\Run, not a service, and not Task Scheduler.**

A *service* runs in session 0. It has no desktop, so it cannot show a panel, and
it has no audio session, so it cannot open the user's microphone or speakers.
Everything this client exists to do is per-user and interactive; making it a
service would mean a service plus a user-mode agent plus the IPC between them —
three things to keep working instead of one.

*Task Scheduler* would add an "at logon, delay 30 s, restart on failure" policy
that sounds attractive, and the restart-on-failure part is the trap: combined
with the Run key and a user double-clicking the shortcut, it is precisely the
loop of duplicate instances the brief warns against. The single-instance mutex
is the safety net, but the right answer is not to build the hazard.

The Run key needs no administrator rights, is visible to the user in Task
Manager → Startup where they can turn it off without asking anyone, and is the
mechanism Windows itself expects for this. `pythonw.exe` rather than
`python.exe`: the latter opens a console window at every login.

**Why a launcher file and not `-m client_desktop`.** Run entries start in an
arbitrary working directory, so `-m` would not find the package. `launch.pyw`
puts the repository root on `sys.path` itself and is the one thing that works
from anywhere.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "JarvisDesktop"


def launcher_path() -> Path:
    return Path(__file__).resolve().parent / "launch.pyw"


def pythonw() -> Path:
    """The windowed interpreter beside the one running us.

    `sys.executable` is `python.exe` when started from a terminal, and using it
    in the Run key means a console window at every single login.
    """
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    return candidate if candidate.is_file() else current


def command() -> str:
    return f'"{pythonw()}" "{launcher_path()}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def matches_current_install() -> bool:
    """True if the registered command still points at *this* checkout.

    Worth checking separately from `is_enabled`: moving the repository leaves a
    Run entry that fails silently at every login, and the symptom is "JARVIS
    stopped starting" with nothing anywhere to explain it.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value) == command()
    except OSError:
        return False


def enable() -> tuple[bool, str]:
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        return True, command()
    except OSError as exc:
        return False, str(exc)


def disable() -> tuple[bool, str]:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True, "removed"
    except FileNotFoundError:
        return True, "was not set"
    except OSError as exc:
        return False, str(exc)
