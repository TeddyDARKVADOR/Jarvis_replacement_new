"""
Where the panel goes — the arithmetic, with nothing else in it.

No Qt, no Win32, no window handles: rectangles in, a rectangle out. That is the
whole design, and it is what makes this testable. Placement bugs are the kind
that only show up on the third monitor at 150 % scaling with a maximised editor,
which is exactly the situation nobody can reproduce on demand. Pure functions
over synthetic rectangles can reproduce all of it, deterministically, in
milliseconds.

```
   work area  ─┐
   target     ─┼──►  compute_*()  ──►  snap()  ──►  clamp_visible()  ──►  Rect
   config     ─┘
```

**Everything here is in logical pixels** — Qt's coordinate space, the one where
a 1920×1080 screen at 125 % scaling is 1536×864. Win32 hands out *physical*
pixels, and converting at the boundary rather than halfway through is what stops
the two from ever being added together. That mistake is the single most common
multi-monitor defect: it looks perfect on the primary screen and puts the window
off the edge of the second one.

Four modes:

| Mode | The rectangle it is placed against |
|---|---|
| `SCREEN` | the screen's work area |
| `WINDOW` | the target window, once |
| `FOLLOW` | the target window, again whenever the foreground changes |
| `FREE` | none — the user's own position, remembered |
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Bounds for the panel, in logical pixels. The fraction from the settings is
# clamped into these: 20 % of a 1366-wide laptop is not the same object as 20 %
# of an ultrawide, and neither should be allowed to become unreadable or a
# billboard.
MIN_WIDTH = 260
MAX_WIDTH = 520
MIN_HEIGHT = 170
MAX_HEIGHT = 520

#: Below this the panel is a horizontal strip and its contents must reflow.
#: Read by the UI; defined here so the two cannot drift apart.
SHORT_HEIGHT = 260
#: Below this the panel hides everything but the core and the state line.
NARROW_WIDTH = 300


class PlacementMode(Enum):
    SCREEN = "screen"
    WINDOW = "window"
    FOLLOW = "follow"
    FREE = "free"


class Anchor(Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"

    @property
    def is_vertical_edge(self) -> bool:
        """True for LEFT/RIGHT — the panel is a tall column beside something."""
        return self in (Anchor.LEFT, Anchor.RIGHT)

    @property
    def opposite(self) -> "Anchor":
        return {
            Anchor.LEFT: Anchor.RIGHT,
            Anchor.RIGHT: Anchor.LEFT,
            Anchor.TOP: Anchor.BOTTOM,
            Anchor.BOTTOM: Anchor.TOP,
        }[self]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def moved(self, x: int, y: int) -> "Rect":
        return Rect(x, y, self.w, self.h)

    def resized(self, w: int, h: int) -> "Rect":
        return Rect(self.x, self.y, w, h)

    def intersection(self, other: "Rect") -> "Rect":
        x = max(self.x, other.x)
        y = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        return Rect(x, y, max(0, right - x), max(0, bottom - y))

    def overlaps(self, other: "Rect") -> bool:
        return self.intersection(other).area > 0

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


@dataclass(frozen=True)
class PlacementConfig:
    """Everything the arithmetic needs, and nothing about how it is stored."""

    mode: PlacementMode = PlacementMode.SCREEN
    anchor: Anchor = Anchor.RIGHT
    margin: int = 8
    #: Fraction of the *reference* rectangle's width, for LEFT/RIGHT anchors.
    width_fraction: float = 0.20
    #: Fraction of its height, for TOP/BOTTOM anchors.
    height_fraction: float = 0.28
    snap_enabled: bool = True
    snap_distance: int = 16


# ── sizing ───────────────────────────────────────────────────────────────────


def panel_size(reference: Rect, config: PlacementConfig) -> tuple[int, int]:
    """The panel's size against a reference rectangle (a work area or a window).

    A LEFT/RIGHT anchor makes a tall column: the fraction drives the width and
    the height fills what is available. TOP/BOTTOM inverts that, and the result
    is a wide strip whose contents have to reflow — which is why `SHORT_HEIGHT`
    lives in this module rather than in the widget that reacts to it.
    """
    if config.anchor.is_vertical_edge:
        width = _clamp(
            int(reference.w * config.width_fraction), MIN_WIDTH, MAX_WIDTH
        )
        height = _clamp(reference.h - config.margin * 2, MIN_HEIGHT, MAX_HEIGHT * 4)
    else:
        width = _clamp(reference.w - config.margin * 2, MIN_WIDTH, MAX_WIDTH * 4)
        height = _clamp(
            int(reference.h * config.height_fraction), MIN_HEIGHT, MAX_HEIGHT
        )
    return width, height


# ── the four modes ───────────────────────────────────────────────────────────


def compute_screen_anchored(work_area: Rect, config: PlacementConfig) -> Rect:
    """Flat against one edge of the screen's work area.

    The *work area*, never the full screen: it already excludes the taskbar,
    wherever the user has put it and whether or not it auto-hides. Subtracting
    a taskbar by hand is how a panel ends up underneath one on the machine where
    it lives on the left.
    """
    width, height = panel_size(work_area, config)
    margin = config.margin

    if config.anchor is Anchor.RIGHT:
        x, y = work_area.right - width - margin, work_area.y + margin
    elif config.anchor is Anchor.LEFT:
        x, y = work_area.x + margin, work_area.y + margin
    elif config.anchor is Anchor.TOP:
        x, y = work_area.x + margin, work_area.y + margin
    else:  # BOTTOM
        x, y = work_area.x + margin, work_area.bottom - height - margin

    return clamp_to(Rect(x, y, width, height), work_area)


def compute_window_aligned(
    target: Rect, work_area: Rect, config: PlacementConfig
) -> Rect:
    """Beside a window, preferring not to cover it.

    Three attempts, in order, and the order is the whole behaviour:

    1. the requested side, if the panel fits there without leaving the screen;
    2. the opposite side, same test — a panel on the right of a window pinned
       to the right edge belongs on its left, not half off the desktop;
    3. the screen edge on the requested side, accepting an overlap.

    Step 3 is a deliberate concession. With a maximised window there is no
    non-overlapping answer, and refusing to place the panel at all would be
    worse than covering 300 px of an editor the user can scroll.
    """
    width, height = panel_size(target, config)
    margin = config.margin

    for anchor in (config.anchor, config.anchor.opposite):
        candidate = _beside(target, anchor, width, height, margin)
        if _fits_within(candidate, work_area):
            return candidate

    # Nothing fits beside it: fall back to the screen edge on the requested
    # side, which at least keeps the panel where the user asked for it.
    fallback = compute_screen_anchored(work_area, config)
    if config.anchor.is_vertical_edge:
        fallback = fallback.resized(width, min(height, work_area.h - margin * 2))
    else:
        fallback = fallback.resized(min(width, work_area.w - margin * 2), height)
    return clamp_to(fallback, work_area)


def _beside(target: Rect, anchor: Anchor, width: int, height: int, margin: int) -> Rect:
    if anchor is Anchor.RIGHT:
        return Rect(target.right + margin, target.y, width, height)
    if anchor is Anchor.LEFT:
        return Rect(target.x - width - margin, target.y, width, height)
    if anchor is Anchor.TOP:
        return Rect(target.x, target.y - height - margin, width, height)
    return Rect(target.x, target.bottom + margin, width, height)


def _fits_within(rect: Rect, work_area: Rect) -> bool:
    return (
        rect.x >= work_area.x
        and rect.y >= work_area.y
        and rect.right <= work_area.right
        and rect.bottom <= work_area.bottom
    )


# ── snapping ─────────────────────────────────────────────────────────────────


def snap_lines(work_area: Rect, target: Rect | None, margin: int) -> tuple[
    list[int], list[int]
]:
    """The x and y lines a dragged panel may latch onto.

    Both the bare edges and the edges inset by the margin, because those are the
    two positions the user is actually aiming at: flush with the screen, or at
    the same offset the anchored modes would have used.
    """
    xs = [
        work_area.x, work_area.right,
        work_area.x + margin, work_area.right - margin,
    ]
    ys = [
        work_area.y, work_area.bottom,
        work_area.y + margin, work_area.bottom - margin,
    ]
    if target is not None:
        xs += [target.x, target.right, target.x - margin, target.right + margin]
        ys += [target.y, target.bottom, target.y - margin, target.bottom + margin]
    return xs, ys


def apply_snap(
    rect: Rect,
    work_area: Rect,
    target: Rect | None,
    config: PlacementConfig,
) -> Rect:
    """Pull a nearly-aligned rectangle onto the line it is nearly aligned with.

    Moves the panel; never resizes it. Snapping by stretching would mean a
    window that changes size when the user drags it near an edge, which reads
    as a bug however it is explained.

    Both of the panel's own edges are candidates on each axis: dragging its
    right edge towards the screen's right edge should latch, and so should its
    left edge towards the screen's left.
    """
    if not config.snap_enabled or config.snap_distance <= 0:
        return rect

    xs, ys = snap_lines(work_area, target, config.margin)
    x = _snap_axis(rect.x, rect.right, xs, config.snap_distance)
    y = _snap_axis(rect.y, rect.bottom, ys, config.snap_distance)
    return rect.moved(x if x is not None else rect.x, y if y is not None else rect.y)


def _snap_axis(
    near_edge: int, far_edge: int, lines: list[int], threshold: int
) -> int | None:
    """Returns the new position of `near_edge`, or None to leave it alone."""
    best: tuple[int, int] | None = None  # (distance, new near_edge)
    size = far_edge - near_edge
    for line in lines:
        for edge, offset in ((near_edge, 0), (far_edge, size)):
            distance = abs(edge - line)
            if distance <= threshold and (best is None or distance < best[0]):
                best = (distance, line - offset)
    return None if best is None else best[1]


# ── staying on a real screen ─────────────────────────────────────────────────


def clamp_to(rect: Rect, work_area: Rect) -> Rect:
    """Push a rectangle back inside a work area, shrinking only if it must."""
    width = min(rect.w, work_area.w)
    height = min(rect.h, work_area.h)
    x = _clamp(rect.x, work_area.x, work_area.right - width)
    y = _clamp(rect.y, work_area.y, work_area.bottom - height)
    return Rect(x, y, width, height)


def screen_for(rect: Rect, screens: list[Rect]) -> int:
    """Index of the screen this rectangle mostly lives on.

    By overlapping area, not by top-left corner. A panel dragged so that only
    its first ten pixels are on the old screen belongs to the new one, and
    corner-based tests put it back on the wrong monitor every time.

    Falls back to the screen whose centre is nearest, so a rectangle that is
    entirely off every screen — a monitor unplugged since it was saved — still
    resolves to something real.
    """
    if not screens:
        return 0
    best_index, best_area = 0, 0
    for index, screen in enumerate(screens):
        area = rect.intersection(screen).area
        if area > best_area:
            best_index, best_area = index, area
    if best_area > 0:
        return best_index

    rx, ry = rect.centre
    return min(
        range(len(screens)),
        key=lambda i: (screens[i].centre[0] - rx) ** 2
        + (screens[i].centre[1] - ry) ** 2,
    )


def ensure_visible(rect: Rect, work_areas: list[Rect], min_visible: int = 80) -> Rect:
    """Guarantee the panel can still be reached with a mouse.

    A saved FREE position outlives the monitor it was saved on: undock a laptop,
    change a resolution, unplug a screen, and the stored rectangle points at
    empty space. Windows would happily place a window there and the user would
    have no way to get it back — so a position that is no longer usable is
    corrected rather than honoured.

    `min_visible` is how much of the panel must remain on a real work area for
    it to count as reachable. Requiring the whole rectangle would fight the
    perfectly reasonable habit of parking it half off the edge.
    """
    if not work_areas:
        return rect
    for work_area in work_areas:
        if rect.intersection(work_area).area >= min_visible * min_visible:
            return rect
    return clamp_to(rect, work_areas[screen_for(rect, work_areas)])


# ── the one entry point the UI calls ─────────────────────────────────────────


def compute(
    config: PlacementConfig,
    work_areas: list[Rect],
    *,
    target: Rect | None = None,
    free: Rect | None = None,
    current: Rect | None = None,
) -> Rect:
    """The panel's rectangle for `config`, whatever mode it is in.

    `work_areas` must be non-empty and in logical pixels. `target` is the window
    to align against in WINDOW/FOLLOW; when it is missing — nothing focused, or
    the foreground window is one of ours — the mode degrades to SCREEN rather
    than refusing, because a panel that stops moving is easier to mistake for a
    crash than one that falls back to an edge.
    """
    if not work_areas:
        return free or current or Rect(0, 0, MIN_WIDTH, MIN_HEIGHT)

    if config.mode is PlacementMode.FREE:
        rect = free or current or compute_screen_anchored(work_areas[0], config)
        return ensure_visible(rect, work_areas)

    if config.mode in (PlacementMode.WINDOW, PlacementMode.FOLLOW) and target:
        work_area = work_areas[screen_for(target, work_areas)]
        return compute_window_aligned(target, work_area, config)

    reference = current or target
    work_area = (
        work_areas[screen_for(reference, work_areas)] if reference else work_areas[0]
    )
    return compute_screen_anchored(work_area, config)


def _clamp(value: int, low: int, high: int) -> int:
    if high < low:
        return low
    return max(low, min(high, value))
