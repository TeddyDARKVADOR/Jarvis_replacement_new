"""
The panel — JARVIS's place on the screen, wherever the user has put it.

```
┌────────────────────────────────────────────────────┐
│                 work happens here                  │
│                                      ┌───────────┐ │
│                                      │  JARVIS   │ │
│                                      │     ◉     │ │
│                                      │ Je vous   │ │
│                                      │ écoute.   │ │
│                                      │ ●CONNECTED│ │
│                                      └───────────┘ │
└────────────────────────────────────────────────────┘
```

**Three properties, each a decision made once.**

*It does not take focus.* `WA_ShowWithoutActivating` on a `Qt.Tool` window: it
can appear, move, change state, flash a wake ring and raise a confirmation while
the caret stays in the editor being typed in.

*It is not in the taskbar and not in Alt-Tab.* This is furniture, not an
application to switch to.

*Topmost is a visibility setting and never an activation one.* `setGeometry` on
a window that is already visible does not activate it, and nothing here calls
`raise_()`, `activateWindow()` or `SetForegroundWindow` on its own initiative —
only `_show_panel`, which the user asks for explicitly. That distinction is the
whole difference between a panel that stays visible while you work and one that
interrupts you every time it moves.

**The geometry is not computed here.** `placement.py` does the arithmetic on
plain rectangles; this file asks it for one and applies it. Everything the panel
knows about its own position is therefore testable without a display.

**Responsive, and not by guessing at resolutions.** The content reflows off the
size it actually has: a LEFT/RIGHT anchor makes a tall column, TOP/BOTTOM makes
a wide strip, and a strip has no room for a scrolling history. The direction of
one `QBoxLayout` is flipped rather than the widget tree rebuilt — the core is a
single widget that cannot exist in two layouts, and destroying and recreating it
on every resize would restart its animation.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..placement import (
    MIN_HEIGHT,
    MIN_WIDTH,
    NARROW_WIDTH,
    SHORT_HEIGHT,
    Anchor,
    PlacementMode,
    Rect,
    apply_snap,
    clamp_to,
    compute,
    ensure_visible,
    screen_for,
)
from ..state import AssistantState, LinkState, Snapshot
from . import screens
from .core_widget import JarvisCoreWidget
from .theme import HEX, PANEL_STYLESHEET

#: Repaint rate for the core. 60 would be smoother and is not worth the wakeups
#: on a laptop; the animations are slow enough that 30 is indistinguishable.
PAINT_HZ = 30
#: Everything made of text updates far more slowly than the core moves.
TEXT_HZ = 5

COLLAPSED_WIDTH = 68
HISTORY_LINES = 24

_STATE_COLOURS = {
    "OFFLINE": HEX["muted"],
    "CONNECTING": HEX["warn"],
    "RECONNECTING": HEX["warn"],
    "ERROR": HEX["error"],
    "CONNECTED": HEX["sky"],
    "LISTENING": HEX["ok"],
    "THINKING": "#C4B5FD",
    "SPEAKING": HEX["sky"],
    "SLEEPING": HEX["muted"],
}


class JarvisPanel(QWidget):
    """The window. It renders a snapshot and emits intent; it decides nothing."""

    command_submitted = pyqtSignal(str)
    interrupt_requested = pyqtSignal()
    mic_toggled = pyqtSignal(bool)
    confirmation_answered = pyqtSignal(str, bool)
    debug_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    #: The user dragged the panel, so the mode is now FREE and was persisted.
    placement_changed = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._collapsed = False
        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._message_count = 0
        self._last_confirmation: str | None = None
        self._density = ""
        #: Set when the density actually changed, so the placement can be
        #: re-measured once the new layout exists. See `apply_placement`.
        self._density_changed = False
        #: The size the last density pass was computed for.
        self._intended_size = (0, 0)
        #: The window the panel is currently aligned against, logical pixels.
        self._target: Rect | None = None

        self.setWindowTitle("JARVIS")
        self._apply_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(PANEL_STYLESHEET)

        self._build()
        self._watch_screens()
        self.apply_placement()

        self._paint_timer = QTimer(self)
        self._paint_timer.timeout.connect(self._core.update)
        self._paint_timer.start(1000 // PAINT_HZ)

        self._text_timer = QTimer(self)
        self._text_timer.timeout.connect(self._refresh_text)
        self._text_timer.start(1000 // TEXT_HZ)

        self._snapshot = Snapshot()

    # ── construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        frame = QFrame()
        # Scoped by object name, not by type: `QFrame { … }` would also style
        # every QLabel, QScrollArea and QLineEdit, all of which derive from it.
        frame.setObjectName("shell")
        frame.setStyleSheet(
            f"QFrame#shell {{ background: {HEX['ink']};"
            f" border: 1px solid {HEX['outline_faint']}; border-radius: 10px; }}"
        )
        outer.addWidget(frame)

        root = QVBoxLayout(frame)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        # ── header: the drag handle, and the only chrome ─────────────────────
        self._header = QWidget()
        self._header.setFixedHeight(22)
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        self._title = QLabel("J A R V I S")
        self._title.setObjectName("title")
        header.addWidget(self._title)
        header.addStretch(1)

        self._collapse_button = self._chrome_button("—", "Réduire", self._toggle_collapse)
        self._debug_button = self._chrome_button(
            "⚙", "Developer / Debug", self.debug_requested.emit
        )
        header.addWidget(self._debug_button)
        header.addWidget(self._collapse_button)
        root.addWidget(self._header)

        # ── core + what JARVIS said: one box whose direction flips ───────────
        self._core_row = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._core_row.setSpacing(8)
        self._core_row.setContentsMargins(0, 0, 0, 0)

        self._core = JarvisCoreWidget()
        self._core.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._core.setFixedHeight(180)
        self._core.clicked.connect(self._on_core_clicked)
        self._core_row.addWidget(self._core)

        self._text_box = QWidget()
        text_layout = QVBoxLayout(self._text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self._spoken = QLabel("Je vous écoute.")
        self._spoken.setObjectName("spoken")
        self._spoken.setWordWrap(True)
        self._spoken.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._spoken.setMinimumHeight(40)
        text_layout.addWidget(self._spoken)

        self._state = QLabel()
        self._state.setObjectName("state")
        self._state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        text_layout.addWidget(self._state)
        self._core_row.addWidget(self._text_box, 1)

        root.addLayout(self._core_row)

        # ── the confirmation gate, hidden until there is one ─────────────────
        self._confirm_box = self._build_confirmation()
        self._confirm_box.hide()
        root.addWidget(self._confirm_box)

        self._separator = self._make_separator()
        root.addWidget(self._separator)

        # ── the discreet history ─────────────────────────────────────────────
        self._history_area = QScrollArea()
        self._history_area.setWidgetResizable(True)
        self._history_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._history_body = QWidget()
        # The scroll area already draws the panel background; a second opaque
        # widget on top of it produces a visible rectangle inside the border.
        self._history_body.setStyleSheet("background: transparent;")
        self._history = QVBoxLayout(self._history_body)
        self._history.setContentsMargins(8, 8, 8, 8)
        self._history.setSpacing(7)
        self._history.addStretch(1)
        self._history_area.setWidget(self._history_body)
        self._history_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._history_area, 1)

        # ── input ────────────────────────────────────────────────────────────
        self._input = QLineEdit()
        self._input.setPlaceholderText("Écrire à JARVIS…")
        self._input.returnPressed.connect(self._submit)
        root.addWidget(self._input)

        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(6)
        self._mic_button = QPushButton("🎙  Micro")
        self._mic_button.setCheckable(True)
        self._mic_button.setToolTip(
            "Ouvre le micro. Tant qu'il est fermé, aucun son ne quitte ce PC."
        )
        self._mic_button.clicked.connect(
            lambda: self.mic_toggled.emit(self._mic_button.isChecked())
        )
        self._button_row.addWidget(self._mic_button, 1)

        self._interrupt_button = QPushButton("■")
        self._interrupt_button.setObjectName("danger")
        self._interrupt_button.setToolTip("Couper JARVIS (INTERRUPT)")
        self._interrupt_button.setFixedWidth(40)
        self._interrupt_button.clicked.connect(self.interrupt_requested.emit)
        self._button_row.addWidget(self._interrupt_button)
        root.addLayout(self._button_row)

        self._collapsible = [
            self._text_box, self._history_area, self._input,
            self._mic_button, self._interrupt_button, self._separator,
        ]

    def _chrome_button(self, text: str, tip: str, slot) -> QPushButton:  # noqa: ANN001
        button = QPushButton(text)
        button.setFixedSize(20, 20)
        button.setToolTip(tip)
        button.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent;"
            f" color: {HEX['muted']}; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {HEX['sky']}; }}"
        )
        button.clicked.connect(slot)
        return button

    def _make_separator(self) -> QFrame:
        line = QFrame()
        line.setObjectName("separator")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        return line

    def _build_confirmation(self) -> QWidget:
        box = QFrame()
        box.setObjectName("confirmBox")
        box.setStyleSheet(
            f"QFrame#confirmBox {{ background: {HEX['surface_variant']};"
            f" border: 1px solid {HEX['warn']}; border-radius: 6px; }}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(9, 8, 9, 9)
        layout.setSpacing(5)

        self._confirm_title = QLabel()
        self._confirm_title.setWordWrap(True)
        self._confirm_title.setStyleSheet(
            f"color: {HEX['warn']}; font-weight: 600; border: none;"
        )
        layout.addWidget(self._confirm_title)

        self._confirm_detail = QLabel()
        self._confirm_detail.setWordWrap(True)
        self._confirm_detail.setStyleSheet(
            f"color: {HEX['muted']}; font-size: 11px; border: none;"
        )
        layout.addWidget(self._confirm_detail)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._confirm_yes = QPushButton("Confirmer")
        self._confirm_yes.setObjectName("confirm")
        self._confirm_yes.clicked.connect(lambda: self._answer(True))
        self._confirm_no = QPushButton("Annuler")
        self._confirm_no.setObjectName("danger")
        self._confirm_no.clicked.connect(lambda: self._answer(False))
        row.addWidget(self._confirm_yes, 1)
        row.addWidget(self._confirm_no, 1)
        layout.addLayout(row)
        return box

    # ── placement ────────────────────────────────────────────────────────────

    def _apply_window_flags(self) -> None:
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def sync_always_on_top(self) -> None:
        """Make the window match `settings.always_on_top`, without taking focus.

        Compared against the *window's own flag*, never against the setting: the
        settings object is written by the debug window before this is called, so
        a guard on the setting would find it already correct and never do
        anything. The flag is the only thing that knows the truth.

        Qt unmaps a visible window when its flags change and it has to be shown
        again; `show()` on a widget carrying `WA_ShowWithoutActivating` re-maps
        it without activating — which is the property that must survive this.
        """
        wanted = bool(self.settings.always_on_top)
        current = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if wanted == current:
            return
        visible = self.isVisible()
        geometry = self.geometry()
        self._apply_window_flags()
        if visible:
            self.show()
            # Re-showing can nudge the geometry on some Windows builds; putting
            # it back is cheaper than explaining why the panel drifts a few
            # pixels every time this is toggled.
            self.setGeometry(geometry)

    def set_target(self, target: Rect | None) -> None:
        """The window to align against, in logical pixels. None means none."""
        self._target = target

    def current_rect(self) -> Rect:
        geometry = self.geometry()
        return Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())

    def apply_placement(self) -> None:
        """Ask `placement` where to be, and go there.

        Never called while the user is dragging: recomputing a position under a
        held mouse button fights the drag, and the panel jitters between the
        cursor and the computed rectangle.
        """
        if self._dragging:
            return
        work_areas = screens.work_areas()
        if not work_areas:
            return

        config = self.settings.placement
        rect = compute(
            config,
            work_areas,
            target=self._target,
            free=self.settings.free_rect,
            current=self.current_rect(),
        )

        if self._collapsed:
            # A collapsed panel keeps its anchor but not its width.
            if config.anchor.is_vertical_edge:
                shift = rect.w - COLLAPSED_WIDTH
                x = rect.x + shift if config.anchor.value == "right" else rect.x
                rect = Rect(x, rect.y, COLLAPSED_WIDTH, rect.h)
            else:
                rect = Rect(rect.x, rect.y, COLLAPSED_WIDTH, rect.h)
            rect = ensure_visible(rect, work_areas)

        # Reflow *before* moving, never after.
        #
        # Qt will not shrink a window below its layout's minimum size, so a
        # panel still laid out as a tall column cannot be given a 241 px strip:
        # `setGeometry` silently clamps it to ~552 and the strip never happens.
        # Deciding the density from the rectangle we are about to apply — rather
        # than from the size we ended up with — is what breaks that circle.
        #
        # And the density is decided on the *unmodified* rectangle. Growing it
        # first and measuring afterwards makes the two feed each other: a strip
        # grown to fit a confirmation reads as "compact", whose taller core
        # needs more room, which grows it again.
        self._apply_density(rect.w, rect.h)

        # Now, and only now, give the content whatever it genuinely cannot do
        # without. In practice that is a pending confirmation in a strip: there
        # is no room for the banner in the computed height, and re-asserting
        # that height squashes it until its buttons overlap its own title —
        # an unreadable gate on an irreversible action.
        #
        # Measured off `minimumSizeHint` rather than off the banner, so this
        # covers anything else that ever needs the space, and shrinks back on
        # its own when it does not.
        needed = self.minimumSizeHint().height()
        if needed > rect.h:
            extra = needed - rect.h
            # Downwards normally; upwards for a bottom anchor, which would
            # otherwise walk off the edge of the screen.
            grown = (
                Rect(rect.x, rect.y - extra, rect.w, needed)
                if config.anchor is Anchor.BOTTOM
                else Rect(rect.x, rect.y, rect.w, needed)
            )
            rect = clamp_to(grown, work_areas[screen_for(grown, work_areas)])

        self.setGeometry(*rect.as_tuple())

        # A density change takes one turn of the event loop to reach
        # `minimumSizeHint`, so the measurement above used the *previous*
        # layout's minimum — 552 for a strip that needs 293. Re-asserting once
        # on the next turn measures the layout that now exists. It cannot loop:
        # the second pass computes the same density, so nothing reschedules it.
        if self._density_changed:
            self._density_changed = False
            QTimer.singleShot(0, self.apply_placement)

    # ── responsive content ───────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        # **The density is deliberately not recomputed here**, and that is the
        # opposite of the obvious thing to do.
        #
        # This window is frameless and has no resize grip, so the user cannot
        # resize it; every resize is either our own `setGeometry` or Qt growing
        # the window because its content needs more room. Re-deciding on the
        # latter is a runaway: a 241 px strip that grew to fit a confirmation
        # banner reads back as "compact", which restores the title and the tall
        # core, which raises the minimum again — measured settling at 421 px for
        # a panel that asked for 241.
        #
        # The density follows the size we *intend*, set in `apply_placement`.
        # Genuine outside changes — a screen added, removed or rescaled — are
        # handled by the signals wired in `_watch_screens`.

    def _apply_density(self, width: int, height: int) -> None:
        """Reflow for a given size, which may be one the panel does not have yet.

        Three densities, chosen off pixels rather than off which anchor is
        selected — the two usually agree, but a user who has dragged the panel
        into a short wide shape in FREE mode deserves the same reflow.

        Each density also sets the window's own minimum size. That is not
        decoration: the minimum is what Qt clamps `setGeometry` against, so a
        layout that can reflow but never lowers its minimum can still not be
        made small.
        """
        self._intended_size = (width, height)

        if self._collapsed:
            # Fixed, not merely minimum: the header and core would otherwise
            # hold the window at whatever their content happens to need, and
            # a "collapsed" panel 222 px wide is not collapsed.
            self.setFixedWidth(COLLAPSED_WIDTH)
            self.setMinimumHeight(90)
            self._activate_layout()
            return

        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)

        if height < SHORT_HEIGHT:
            density = "strip"
        elif width < NARROW_WIDTH or height < 460:
            density = "compact"
        else:
            density = "full"
        if density == self._density:
            return
        self._density = density
        self._density_changed = True

        if density == "strip":
            # A wide short panel: core beside the text, no room for history.
            self._core_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self._core.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
            )
            self._core.setFixedWidth(max(56, min(120, height - 90)))
            self._core.setMinimumHeight(48)
            self._core.setMaximumHeight(16777215)
            self._history_area.hide()
            self._separator.hide()
            self._title.hide()
            self._spoken.setMinimumHeight(0)
            self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        else:
            self._core_row.setDirection(QBoxLayout.Direction.TopToBottom)
            self._core.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self._core.setMinimumWidth(0)
            self._core.setMaximumWidth(16777215)
            self._core.setFixedHeight(120 if density == "compact" else 180)
            self._history_area.setVisible(density == "full")
            self._separator.setVisible(density == "full")
            self._title.show()
            self._spoken.setMinimumHeight(40)
            # `compact` already hides the history, so its floor is the core plus
            # the input row; `full` has to leave the history somewhere to live.
            self.setMinimumSize(MIN_WIDTH, 300 if density == "compact" else 420)

        self._activate_layout()

    def _watch_screens(self) -> None:
        """Re-place when the desktop itself changes underneath us.

        Undocking a laptop, unplugging a monitor or changing a scaling factor
        all invalidate a stored position, and none of them arrive as a resize
        this window could react to. `ensure_visible` inside `compute` is what
        rescues a rectangle that now points at nothing; these signals are what
        make it run at the moment it is needed rather than at the next restart.
        """
        application = QApplication.instance()
        if application is None:
            return
        application.screenAdded.connect(lambda _: self.apply_placement())
        application.screenRemoved.connect(lambda _: self.apply_placement())
        for screen in QApplication.screens():
            screen.geometryChanged.connect(lambda _: self.apply_placement())
            screen.availableGeometryChanged.connect(lambda _: self.apply_placement())
            screen.logicalDotsPerInchChanged.connect(lambda _: self.apply_placement())

    def _activate_layout(self) -> None:
        """Recompute the layout's minimum size now, not on the next event loop.

        Qt defers that work, so a `setGeometry` issued immediately after a
        density change is clamped against the *previous* density's minimum —
        which is exactly the size the reflow was supposed to make possible.
        """
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()

    # ── dragging ─────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._header.geometry().contains(event.position().toPoint())
        ):
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragging = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None:
            self._drag_offset = None
            self._dragging = False
            self._settle_after_drag()
        super().mouseReleaseEvent(event)

    def _settle_after_drag(self) -> None:
        """Snap where it landed, remember it, and switch to FREE.

        Dragging a panel that is anchored to the right edge and watching it jump
        back is the behaviour nobody wants; moving it *is* the request to stop
        anchoring it. The mode change is persisted and announced so the settings
        UI stops claiming the panel is still anchored.
        """
        work_areas = screens.work_areas()
        if not work_areas:
            return
        config = self.settings.placement
        rect = self.current_rect()
        index = screen_for(rect, work_areas)
        rect = apply_snap(rect, work_areas[index], self._target, config)
        rect = ensure_visible(rect, work_areas)
        self.setGeometry(*rect.as_tuple())

        names = screens.screen_names()
        self.settings.placement_mode = PlacementMode.FREE.value
        self.settings.remember_free(rect, names[index] if index < len(names) else "")
        self.placement_changed.emit()

    # ── collapse ─────────────────────────────────────────────────────────────

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        for widget in self._collapsible:
            widget.setVisible(not self._collapsed)
        self._confirm_box.setVisible(
            not self._collapsed and self._last_confirmation is not None
        )
        self._core.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._core.setMinimumWidth(0)
        self._core.setMaximumWidth(16777215)
        self._core.setFixedHeight(56 if self._collapsed else 180)
        self._collapse_button.setText("▢" if self._collapsed else "—")
        self._collapse_button.setToolTip("Agrandir" if self._collapsed else "Réduire")
        self._debug_button.setVisible(not self._collapsed)
        self._title.setVisible(not self._collapsed)
        # Force the next density pass to do its work: collapsing and expanding
        # change which widgets exist, not just how big they are.
        self._density = ""
        self.apply_placement()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def changeEvent(self, event) -> None:  # noqa: ANN001
        # A core nobody can see should not be smoothing a level towards
        # anything, and a hidden window should not be repainting at 30 Hz.
        if event.type() == QEvent.Type.WindowStateChange:
            self._core.set_animated(not self.isMinimized())
        super().changeEvent(event)

    # ── rendering a snapshot ─────────────────────────────────────────────────

    def apply(self, snapshot: Snapshot) -> None:
        """Called from the GUI thread with the latest snapshot."""
        self._snapshot = snapshot
        self._core.set_snapshot(snapshot)

    def _refresh_text(self) -> None:
        snapshot = self._snapshot

        name = snapshot.display_state
        colour = _STATE_COLOURS.get(name, HEX["muted"])
        suffix = ""
        if snapshot.link is LinkState.RECONNECTING and snapshot.next_retry_seconds:
            suffix = f"  ·  nouvel essai dans {snapshot.next_retry_seconds}s"
        elif snapshot.link is LinkState.ERROR and snapshot.last_error:
            suffix = f"  ·  {snapshot.last_error[:40]}"
        elif snapshot.mic_open:
            suffix = "  ·  micro ouvert"
        self._state.setText(
            f'<span style="color:{colour}">●</span> '
            f'<span style="color:{HEX["muted"]}">{name}{suffix}</span>'
        )

        if snapshot.link is LinkState.ERROR:
            self._spoken.setText(snapshot.last_error or "Erreur.")
        elif not snapshot.connected:
            self._spoken.setText("Hors ligne.")
        else:
            spoken = snapshot.last_spoken_line
            self._spoken.setText(spoken if spoken else "Je vous écoute.")

        self._mic_button.setChecked(snapshot.gate_open)
        self._mic_button.setEnabled(snapshot.connected)
        self._mic_button.setText("🎙  Micro ouvert" if snapshot.gate_open else "🎙  Micro")
        self._interrupt_button.setEnabled(
            snapshot.assistant is AssistantState.SPEAKING
        )
        self._input.setEnabled(snapshot.connected)

        self._refresh_confirmation(snapshot)
        self._refresh_history(snapshot)

    def _refresh_confirmation(self, snapshot: Snapshot) -> None:
        cid = snapshot.confirmation_id
        if cid is None:
            if self._last_confirmation is not None:
                self._last_confirmation = None
                self._confirm_box.hide()
                # Give the height the banner borrowed back.
                self.apply_placement()
            return

        if cid != self._last_confirmation:
            self._last_confirmation = cid
            self._confirm_title.setText(snapshot.confirmation_title or "Confirmer ?")
            self._confirm_detail.setText(snapshot.confirmation_detail)
            self._confirm_box.setVisible(not self._collapsed)
            # The banner needs room a strip does not have, so Qt will grow the
            # window past the rectangle it was given. Re-asserting the placement
            # keeps the *position* right while the height floats, and puts the
            # height back when the banner goes away.
            self.apply_placement()

        # The countdown is not decoration: an answer sent after the deadline is
        # discarded by the server, and a button that looks live and is not is
        # worse than no button at all.
        if snapshot.confirmation_deadline > 0.0:
            left = int(max(0.0, snapshot.confirmation_deadline - time.monotonic()))
            self._confirm_yes.setText(f"Confirmer ({left}s)")
            self._confirm_yes.setEnabled(left > 0)
            self._confirm_no.setEnabled(left > 0)
        else:
            self._confirm_yes.setText("Confirmer")

    def _refresh_history(self, snapshot: Snapshot) -> None:
        if len(snapshot.messages) == self._message_count:
            return
        self._message_count = len(snapshot.messages)

        while self._history.count() > 1:
            item = self._history.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for message in snapshot.messages[-HISTORY_LINES:]:
            self._history.insertWidget(
                self._history.count() - 1, _history_line(message)
            )

        QTimer.singleShot(0, self._scroll_history_to_end)

    def _scroll_history_to_end(self) -> None:
        bar = self._history_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ── intent ───────────────────────────────────────────────────────────────

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.command_submitted.emit(text)

    def _answer(self, confirmed: bool) -> None:
        cid = self._last_confirmation
        if cid:
            self.confirmation_answered.emit(cid, confirmed)

    def _on_core_clicked(self) -> None:
        # Tapping the core while JARVIS is talking is the fastest way to stop
        # it — the same gesture as the phone's.
        if self._snapshot.assistant is AssistantState.SPEAKING:
            self.interrupt_requested.emit()
        else:
            self.mic_toggled.emit(not self._snapshot.gate_open)


def _history_line(message) -> QLabel:  # noqa: ANN001
    label = QLabel()
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if message.from_jarvis:
        prefix = f'<span style="color:{HEX["sky"]}">JARVIS</span>  '
        colour = HEX["text"]
    else:
        prefix = f'<span style="color:{HEX["muted"]}">VOUS</span>  '
        colour = HEX["muted"]
    title = ""
    if message.title:
        title = f'<div style="color:{HEX["sky"]};font-size:10px">{message.title}</div>'
    label.setText(
        f'<div style="font-size:10px">{prefix}</div>{title}'
        f'<div style="color:{colour}">{_escape(message.text)}</div>'
    )
    label.setFont(QFont("Segoe UI", 8))
    return label


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
