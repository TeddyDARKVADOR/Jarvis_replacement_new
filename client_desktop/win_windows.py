"""
The foreground window, as Windows sees it.

Win32 only — no Qt, no placement arithmetic. Everything here returns **physical**
pixels and the monitor they belong to; converting to Qt's logical space is
`ui/screens.py`'s job, and keeping the two apart is what stops a physical
rectangle ever being added to a logical one.

**Why polling and not `SetWinEventHook`.** The hook is the "correct" API and it
delivers `EVENT_SYSTEM_FOREGROUND` the instant focus moves. It also requires a
C callback invoked on an arbitrary thread inside someone else's message pump; a
Python callback there is a way to crash another process's UI thread if anything
in it raises, and ctypes callbacks that outlive their owning object crash
harder. `GetForegroundWindow()` costs a single syscall, and at 4 Hz the whole
feature is cheaper than one frame of the core animation.

**What is deliberately not here: anything that changes a window.** No
`SetForegroundWindow`, no `SetWindowPos` on a window we do not own, no
`AttachThreadInput`. This module reads. A panel that rearranged other people's
windows would be a window manager, and a buggy one.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

try:
    import win32con
    import win32gui

    AVAILABLE = True
except Exception:  # pragma: no cover - non-Windows or pywin32 absent
    AVAILABLE = False

#: DwmGetWindowAttribute — a UWP window that has been "closed" stays alive and
#: visible-according-to-IsWindowVisible, but cloaked. Without this test the
#: panel happily aligns itself against a Mail window nobody can see.
_DWMWA_CLOAKED = 14

#: GetDpiForMonitor
_MDT_EFFECTIVE_DPI = 0

#: Windows whose class is never a sensible alignment target.
_SHELL_CLASSES = frozenset(
    {
        "Progman",                 # the desktop
        "WorkerW",                 # the desktop's wallpaper host
        "Shell_TrayWnd",           # the taskbar
        "Shell_SecondaryTrayWnd",  # the taskbar on other monitors
        "Windows.UI.Core.CoreWindow",   # Start, search, the action centre
        "XamlExplorerHostIslandWindow", # Task view, Alt-Tab, widgets
        "ForegroundStaging",
        "MultitaskingViewFrame",
    }
)


@dataclass(frozen=True)
class ForegroundWindow:
    """A window worth aligning against, in physical pixels."""

    hwnd: int
    #: (x, y, w, h), physical.
    rect: tuple[int, int, int, int]
    #: `\\.\DISPLAY1` — matches `QScreen.name()` on Windows, which is how the
    #: physical rectangle finds its way to the right logical screen.
    monitor_device: str
    #: The monitor's own rectangle, physical. The origin for the conversion.
    monitor_rect: tuple[int, int, int, int]
    #: Effective DPI / 96.
    scale: float
    title: str
    class_name: str
    maximised: bool


def _cloaked(hwnd: int) -> bool:
    try:
        value = ctypes.c_int(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(_DWMWA_CLOAKED),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0 and value.value != 0
    except Exception:
        return False


def _monitor_for(hwnd: int) -> tuple[str, tuple[int, int, int, int], float]:
    """(device name, physical monitor rect, scale) for the monitor holding `hwnd`."""
    monitor = ctypes.windll.user32.MonitorFromWindow(
        wintypes.HWND(hwnd), 2  # MONITOR_DEFAULTTONEAREST
    )

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
    ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info))

    rect = (
        info.rcMonitor.left,
        info.rcMonitor.top,
        info.rcMonitor.right - info.rcMonitor.left,
        info.rcMonitor.bottom - info.rcMonitor.top,
    )

    scale = 1.0
    try:
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(
            monitor, _MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        ) == 0:
            scale = dpi_x.value / 96.0
    except Exception:
        # Pre-1607, or shcore missing. A wrong scale is better than no window:
        # 1.0 is right on every unscaled display, which is most of them.
        pass

    return str(info.szDevice), rect, (scale or 1.0)


def is_usable_target(hwnd: int) -> bool:
    """Can this window sensibly be aligned against?

    Minimised windows are excluded and that is not an optimisation: a minimised
    window's rectangle is around (-32000, -32000), and honouring it would fling
    the panel off the desktop.
    """
    if not AVAILABLE or not hwnd:
        return False
    try:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.IsIconic(hwnd):
            return False
        if win32gui.GetClassName(hwnd) in _SHELL_CLASSES:
            return False
        if _cloaked(hwnd):
            return False
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        # Something 1 px wide is a tooltip, a drop shadow or an IME candidate
        # list, not an application to sit beside.
        return (right - left) >= 200 and (bottom - top) >= 150
    except Exception:
        return False


def foreground_window(exclude: frozenset[int] = frozenset()) -> ForegroundWindow | None:
    """The window the user is working in, or None if there is nothing sensible.

    `exclude` is our own window handles. Without it, clicking the panel makes
    the panel the foreground window and it would then align itself against
    itself — which converges on a slow drift towards one corner, and looks
    exactly like the panel "wandering off".
    """
    if not AVAILABLE:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or hwnd in exclude or not is_usable_target(hwnd):
            return None

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        device, monitor_rect, scale = _monitor_for(hwnd)

        placement = win32gui.GetWindowPlacement(hwnd)
        maximised = placement[1] == win32con.SW_SHOWMAXIMIZED

        return ForegroundWindow(
            hwnd=hwnd,
            rect=(left, top, right - left, bottom - top),
            monitor_device=device,
            monitor_rect=monitor_rect,
            scale=scale,
            title=win32gui.GetWindowText(hwnd) or "",
            class_name=win32gui.GetClassName(hwnd) or "",
            maximised=maximised,
        )
    except Exception:
        return None
