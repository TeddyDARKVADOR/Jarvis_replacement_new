r"""
The bridge between Windows' pixels and Qt's.

One job: turn what `win_windows` reports — physical pixels, on a named monitor —
into the logical rectangles `placement` does arithmetic with. Nothing else in
the client is allowed to mix the two, and this is the only file that sees both.

**The conversion, and why it is not just a division.**

```
   logical = screen.geometry().topLeft()
           + (physical - monitor.physicalTopLeft()) / scale
```

Dividing the raw physical coordinate by the scale factor is the version that
works on one monitor and fails on two. Qt lays logical screens out in its own
coordinate space, whose origins do not match the physical ones as soon as any
monitor is scaled: a 1920-wide primary at 125 % is 1536 logical, so a second
monitor physically starting at x=1920 starts at x=1536 for Qt. Subtracting the
monitor's own origin first, and adding Qt's back, is what makes both agree.

The monitors are matched by device name — `\\.\DISPLAY1` — which
`GetMonitorInfoExW` and `QScreen.name()` spell identically on Windows.
"""

from __future__ import annotations

from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication

from ..placement import Rect
from ..win_windows import ForegroundWindow


def _to_rect(geometry) -> Rect:  # noqa: ANN001
    return Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())


def work_areas() -> list[Rect]:
    """Every screen's usable area, logical, primary first.

    `availableGeometry`, not `geometry`: it already excludes the taskbar
    wherever the user keeps it. Primary first so that callers with nothing
    better to go on get the screen the user is most likely looking at.
    """
    screens = QApplication.screens()
    if not screens:
        return []
    primary = QApplication.primaryScreen()
    ordered = ([primary] if primary in screens else []) + [
        screen for screen in screens if screen is not primary
    ]
    return [_to_rect(screen.availableGeometry()) for screen in ordered]


def screen_names() -> list[str]:
    """Matches `work_areas()` index for index, for remembering where FREE was."""
    screens = QApplication.screens()
    if not screens:
        return []
    primary = QApplication.primaryScreen()
    ordered = ([primary] if primary in screens else []) + [
        screen for screen in screens if screen is not primary
    ]
    return [screen.name() for screen in ordered]


def _screen_named(name: str) -> QScreen | None:
    for screen in QApplication.screens():
        if screen.name() == name:
            return screen
    return None


def to_logical(window: ForegroundWindow) -> Rect | None:
    """A foreground window's physical rectangle, in Qt's coordinates."""
    screen = _screen_named(window.monitor_device)
    if screen is None:
        # The monitor could not be matched by name. Falling back to the primary
        # screen's ratio is wrong on a mixed-DPI setup, but it is right on the
        # overwhelmingly common single-monitor one, and returning nothing would
        # disable window alignment entirely for a name mismatch.
        primary = QApplication.primaryScreen()
        if primary is None:
            return None
        scale = window.scale or primary.devicePixelRatio() or 1.0
        x, y, w, h = window.rect
        return Rect(int(x / scale), int(y / scale), int(w / scale), int(h / scale))

    scale = window.scale or 1.0
    logical_origin = screen.geometry()
    px, py, pw, ph = window.rect
    mx, my, _, _ = window.monitor_rect

    return Rect(
        int(logical_origin.x() + (px - mx) / scale),
        int(logical_origin.y() + (py - my) / scale),
        max(1, int(pw / scale)),
        max(1, int(ph / scale)),
    )
