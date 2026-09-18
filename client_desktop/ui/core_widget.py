"""
JARVIS, drawn — the same core as the phone, on QPainter instead of Canvas.

A port of `client-android/.../ui/JarvisCore.kt`, primitive for primitive: a
filled centre, two arcs, a ring, three orbiting marks and a halo. Everything
else is those five things moving. There is no particle system and no shader,
because this widget is on screen the entire time the workstation is on.

**What drives it.** Two numbers and one state:

```
  level      0..1   the voice being heard — the user's or JARVIS's
  sweep      0..1   a slow rotation, only where a state calls for one
  state             what MARK LIII says it is doing
```

`level` is deliberately the *same* input for both directions. A core that
pulsed differently depending on who was speaking would be showing the plumbing;
what it should show is that something is being said.

**One difference from Compose, and it is not cosmetic.** Compose animations are
declarative and the framework owns the clock. Qt has no such thing, so every
animation here is a pure function of `time.monotonic()`. That is the better
behaviour anyway: the phases stay correct when the paint timer is throttled —
which Windows does the moment this window is fully covered by a maximised
editor, exactly the state it spends most of its life in.
"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

from ..state import AssistantState, LinkState, Snapshot
from .theme import alpha

#: How long the wake acknowledgement ring takes to leave the core.
WAKE_RING_SECONDS = 0.7
#: A wake older than this never animates. Stamped rather than flagged so a wake
#: that happened while the panel was hidden does not fire a stale ring when the
#: user looks at it later.
WAKE_RING_MAX_AGE = 1.5


class _Mood:
    """What one state looks like. Values carried over from `moodOf`."""

    __slots__ = (
        "colour", "dim", "ring_scale", "breath_ms", "sweep_ms", "arcs", "reactivity",
    )

    def __init__(
        self,
        colour: tuple[int, int, int],
        dim: tuple[int, int, int],
        ring_scale: float,
        breath_ms: int,
        sweep_ms: int,
        arcs: float,
        reactivity: float,
    ) -> None:
        self.colour = QColor(*colour)
        self.dim = QColor(*dim)
        self.ring_scale = ring_scale
        self.breath_ms = breath_ms
        self.sweep_ms = sweep_ms
        self.arcs = arcs
        self.reactivity = reactivity


_ERROR = _Mood((0xF8, 0x71, 0x71), (0x7F, 0x2D, 0x2D), 0.78, 2200, 6000, 0.0, 0.0)
_OFFLINE = _Mood((0x55, 0x61, 0x7A), (0x23, 0x2A, 0x3A), 0.74, 5200, 9000, 0.0, 0.0)
_CONNECTING = _Mood((0xFB, 0xBF, 0x6B), (0x6A, 0x4A, 0x22), 0.80, 1800, 2600, 0.55, 0.0)
_SPEAKING = _Mood((0x7D, 0xD3, 0xFC), (0x1E, 0x4C, 0x63), 0.84, 1400, 5200, 1.0, 1.0)
_THINKING = _Mood((0xC4, 0xB5, 0xFD), (0x3B, 0x32, 0x66), 0.82, 1100, 1700, 0.9, 0.0)
_LISTENING = _Mood((0x6E, 0xE7, 0xB7), (0x1F, 0x51, 0x45), 0.82, 2400, 7000, 0.7, 0.85)
_SLEEPING = _Mood((0x64, 0x74, 0x8B), (0x23, 0x2A, 0x3A), 0.76, 4600, 9000, 0.0, 0.0)
_READY = _Mood((0x93, 0xC5, 0xFD), (0x24, 0x3B, 0x57), 0.80, 3200, 8000, 0.35, 0.2)


def mood_of(link: LinkState, assistant: AssistantState) -> _Mood:
    # The link comes first: what MARK LIII last said it was doing is not
    # interesting while this machine cannot reach it.
    if link is LinkState.ERROR:
        return _ERROR
    if link is LinkState.DISCONNECTED:
        return _OFFLINE
    if link in (LinkState.CONNECTING, LinkState.RECONNECTING):
        return _CONNECTING
    if assistant is AssistantState.SPEAKING:
        return _SPEAKING
    if assistant is AssistantState.THINKING:
        return _THINKING
    if assistant is AssistantState.LISTENING:
        return _LISTENING
    if assistant is AssistantState.SLEEPING:
        return _SLEEPING
    return _READY


class JarvisCoreWidget(QWidget):
    """The core. Give it a snapshot; it draws whatever that is."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._snapshot = Snapshot()
        self._level = 0.0
        self._last_paint = time.monotonic()
        #: Set false when the panel is collapsed or hidden — a core nobody can
        #: see should not be smoothing a level towards anything.
        self._animated = True

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot

    def set_animated(self, animated: bool) -> None:
        self._animated = animated

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # ── the drawing ──────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: ANN001, C901
        snapshot = self._snapshot
        mood = mood_of(snapshot.link, snapshot.assistant)

        now = time.monotonic()
        dt = max(0.0, min(0.25, now - self._last_paint))
        self._last_paint = now

        # The breath: present even at rest, so the core never looks frozen or
        # crashed. A triangle wave, because Compose's RepeatMode.Reverse is one.
        if self._animated:
            phase = (now * 1000.0 % (mood.breath_ms * 2)) / (mood.breath_ms * 2)
            breath = 1.0 - abs(phase * 2.0 - 1.0)
            sweep = (now * 1000.0 % mood.sweep_ms) / mood.sweep_ms
        else:
            breath, sweep = 0.5, 0.0

        # Voice drives the radius, but not directly: raw RMS jitters frame to
        # frame and the core would shiver. Asymmetric smoothing — quick to grow,
        # slower to fall — tracks a syllable without chasing every sample.
        target = 0.0
        if self._animated:
            source = (
                snapshot.speaker_level
                if snapshot.assistant is AssistantState.SPEAKING
                else snapshot.mic_level
            )
            target = max(0.0, min(1.0, source))
        tau = 0.090 if target > self._level else 0.260
        self._level += (target - self._level) * (1.0 - math.exp(-dt / tau))
        level = self._level

        # The wake acknowledgement: one ring, 0 -> 1, then gone.
        wake = 1.0
        if snapshot.woke_at > 0.0:
            age = now - snapshot.woke_at
            if age < WAKE_RING_MAX_AGE:
                wake = min(1.0, age / WAKE_RING_SECONDS)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._draw_core(painter, mood, breath, sweep, level, wake)
        finally:
            painter.end()

    def _draw_core(
        self,
        painter: QPainter,
        mood: _Mood,
        breath: float,
        sweep: float,
        level: float,
        wake: float,
    ) -> None:
        rect = self.rect()
        centre = QPointF(rect.width() / 2.0, rect.height() / 2.0)
        # 0.92 rather than 1.0: the halo reaches past every other primitive and
        # would be clipped flat against the widget edge without the margin.
        unit = min(rect.width(), rect.height()) / 2.0 * 0.92
        if unit <= 1.0:
            return

        voice = level * mood.reactivity
        # Everything scales off one number so the parts stay in proportion when
        # the voice moves them.
        pulse = 1.0 + (breath - 0.5) * 0.05 + voice * 0.16

        # ── halo ─────────────────────────────────────────────────────────────
        # Drawn first and very soft — it is what stops the core reading as a
        # flat sticker on a black rectangle.
        halo_radius = unit * 0.95 * pulse
        halo = QRadialGradient(centre, halo_radius)
        halo.setColorAt(0.0, alpha(mood.dim, 0.55))
        halo.setColorAt(1.0, alpha(mood.dim, 0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(centre, halo_radius, halo_radius)

        # ── the ring the arcs live on ────────────────────────────────────────
        ring_radius = unit * mood.ring_scale * pulse
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(alpha(mood.colour, 0.16 + voice * 0.20), 1.5))
        painter.drawEllipse(centre, ring_radius, ring_radius)

        ring_rect = QRectF(
            centre.x() - ring_radius,
            centre.y() - ring_radius,
            ring_radius * 2,
            ring_radius * 2,
        )

        if mood.arcs > 0.01:
            # ── two arcs, opposed, sweeping ──────────────────────────────────
            # Two rather than one because a single arc reads as a loading
            # spinner, and JARVIS is not loading.
            arc_alpha = mood.arcs * (0.55 + voice * 0.45)
            painter.setPen(QPen(alpha(mood.colour, arc_alpha), 2.5))
            rotation = sweep * 360.0
            for i in range(2):
                # Qt measures in 1/16th of a degree, anticlockwise from 3
                # o'clock; Compose measures clockwise. Negating both gives the
                # same direction of travel as the phone.
                start = -(12.0 + i * 180.0 + rotation)
                painter.drawArc(ring_rect, int(start * 16), int(-56.0 * 16))

            # ── orbiting marks ───────────────────────────────────────────────
            # They carry the sweep at a different rate so the whole thing does
            # not turn as one rigid object.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(alpha(mood.colour, mood.arcs * (0.35 + voice * 0.5)))
            orbit = ring_radius * 0.98
            dot = 1.8 + voice * 2.2
            for i in range(3):
                angle = (sweep * -1.4 + i / 3.0) * 2.0 * math.pi
                painter.drawEllipse(
                    QPointF(
                        centre.x() + math.cos(angle) * orbit,
                        centre.y() + math.sin(angle) * orbit,
                    ),
                    dot,
                    dot,
                )
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # ── inner ring ───────────────────────────────────────────────────────
        # The one the voice actually moves, so amplitude has somewhere to go
        # that is not the outline.
        inner = unit * (0.46 + voice * 0.20) * pulse
        painter.setPen(QPen(alpha(mood.colour, 0.30 + voice * 0.35), 1.0))
        painter.drawEllipse(centre, inner, inner)

        # ── "Hey Jarvis" was heard ───────────────────────────────────────────
        # One ring leaving the core, fading as it goes. Drawn over everything
        # else so it reads even mid-sentence.
        if wake < 1.0:
            wake_radius = unit * (0.30 + wake * 0.72)
            painter.setPen(
                QPen(QColor(255, 255, 255, int((1.0 - wake) * 0.5 * 255)),
                     2.5 * (1.0 - wake) + 0.5)
            )
            painter.drawEllipse(centre, wake_radius, wake_radius)

        # ── the centre ───────────────────────────────────────────────────────
        # Always lit, always the brightest thing on screen: it is the one
        # element that says the assistant exists at all.
        core_radius = unit * (0.30 + voice * 0.10) * pulse
        centre_gradient = QRadialGradient(centre, core_radius)
        centre_gradient.setColorAt(0.0, QColor(255, 255, 255, int(0.92 * 255)))
        centre_gradient.setColorAt(0.5, mood.colour)
        centre_gradient.setColorAt(1.0, alpha(mood.colour, 0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(centre_gradient)
        painter.drawEllipse(centre, core_radius, core_radius)
