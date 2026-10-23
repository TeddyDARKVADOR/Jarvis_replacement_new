"""
Starting with Windows, without asking for anything the client does not need.

```
Windows démarre
      ↓
   réseau
      ↓
Matthew Desktop démarre
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
import os
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MatthewAssistantDesktop"
LINUX_AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "matthew-assistant-desktop.desktop"
# Before the rename (2026-10-23) the entry was "JarvisDesktop": still seen, replaced on enable.
LEGACY_VALUE_NAME = "JarvisDesktop"
LEGACY_LINUX_AUTOSTART = LINUX_AUTOSTART.with_name("jarvis-desktop.desktop")


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
    if sys.platform.startswith("linux"):
        if os.environ.get("MATTHEW_ASSISTANT_LOCAL_MODE") == "1":
            script = Path(__file__).resolve().parent.parent / "main.py"
            return f'"{sys.executable}" "{script}" --local'
        return f'"{sys.executable}" -m client_desktop'
    return f'"{pythonw()}" "{launcher_path()}"'


def _windows_registry():
    import winreg
    return winreg


def _linux_entry() -> str:
    repo = Path(__file__).resolve().parent.parent
    return "\n".join((
        "[Desktop Entry]", "Type=Application", "Name=Matthew Desktop",
        "Comment=HUD Matthew", f"Exec={command()}",
        f"Path={repo}", "Terminal=false", "X-GNOME-Autostart-enabled=true", "",
    ))


def is_enabled() -> bool:
    if sys.platform.startswith("linux"):
        return LINUX_AUTOSTART.is_file() or LEGACY_LINUX_AUTOSTART.is_file()
    try:
        winreg = _windows_registry()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            for name in (VALUE_NAME, LEGACY_VALUE_NAME):
                try:
                    if winreg.QueryValueEx(key, name)[0]:
                        return True
                except FileNotFoundError:
                    pass
            return False
    except OSError:
        return False


def _drop_legacy() -> None:
    if sys.platform.startswith("linux"):
        LEGACY_LINUX_AUTOSTART.unlink(missing_ok=True)
        return
    winreg = _windows_registry()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
        except FileNotFoundError:
            pass


def matches_current_install() -> bool:
    """True if the registered command still points at *this* checkout.

    Worth checking separately from `is_enabled`: moving the repository leaves a
    Run entry that fails silently at every login, and the symptom is "Matthew
    stopped starting" with nothing anywhere to explain it.
    """
    if sys.platform.startswith("linux"):
        try:
            return LINUX_AUTOSTART.read_text(encoding="utf-8") == _linux_entry()
        except OSError:
            return False
    try:
        winreg = _windows_registry()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value) == command()
    except OSError:
        return False


def enable() -> tuple[bool, str]:
    if sys.platform.startswith("linux"):
        try:
            LINUX_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            LINUX_AUTOSTART.write_text(_linux_entry(), encoding="utf-8")
            _drop_legacy()
            return True, str(LINUX_AUTOSTART)
        except OSError as exc:
            return False, str(exc)
    try:
        winreg = _windows_registry()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        _drop_legacy()
        return True, command()
    except OSError as exc:
        return False, str(exc)


def disable() -> tuple[bool, str]:
    if sys.platform.startswith("linux"):
        try:
            LINUX_AUTOSTART.unlink(missing_ok=True)
            _drop_legacy()
            return True, "removed"
        except OSError as exc:
            return False, str(exc)
    try:
        _drop_legacy()
        winreg = _windows_registry()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True, "removed"
    except FileNotFoundError:
        return True, "was not set"
    except OSError as exc:
        return False, str(exc)
