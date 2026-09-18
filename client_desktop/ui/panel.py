"""
The panel — JARVIS's permanent place on the screen.

```
┌────────────────────────────────────────────────────┐
│                                                    │
│                 work happens here                  │
│                                      ┌───────────┐ │
│                                      │  JARVIS   │ │
│                                      │     ◉     │ │
│                                      │ Je vous   │ │
│                                      │ écoute.   │ │
│                                      │ ● CONNECTED│ │
│                                      └───────────┘ │
└────────────────────────────────────────────────────┘
```

**Three properties, and each is a decision that had to be made once.**

*It does not take focus.* `WA_ShowWithoutActivating` plus a `Tool` window: it
can appear, change state, flash a wake ring and put a confirmation on screen
while the caret stays in the editor the user is typing in. An assistant that
stole focus to say "I am listening" would be worse than no assistant.

*It is not in the taskbar and not in Alt-Tab.* `Qt.Tool` does both. This is
furniture, not an application the user switches to — it is already there.

*It stays on top, and that is why it must be narrow.* A 20 % strip that is
always visible is presence; a window that is always on top and takes half the
screen is an obstruction. The width comes from the brief and is clamped to a
readable range, because 20 % of a 1366-wide laptop is not the same object as
20 % of an ultrawide.

The measured target on this machine: 1920×1080 at 125 % scaling = 1536×864
logical, so 20 % is **307 px**.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
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

from ..state import AssistantState, LinkState, Snapshot
from .core_widget import JarvisCoreWidget
from .theme import HEX, PANEL_STYLESHEET

#: Repaint rate for the core. 60 would be smoother and is not worth the wakeups
#: on a laptop; the animations are slow enough that 30 is indistinguishable.
PAINT_HZ = 30
#: Everything that is text updates far more slowly than the core moves.
TEXT_HZ = 5

MIN_WIDTH = 260
MAX_WIDTH = 460
COLLAPSED_WIDTH = 68

#: How many turns the discreet history keeps on screen.
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

    def __init__(
        self,
        width_fraction: float = 0.20,
        dock: str = "right",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._width_fraction = width_fraction
        self._dock = dock
        self._collapsed = False
        self._drag_offset: QPoint | None = None
        self._message_count = 0
        self._last_confirmation: str | None = None

        self.setWindowTitle("JARVIS")
        self.setWindowFlags(
            Qt.WindowType.Tool                      # no taskbar entry, no Alt-Tab
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # The one that matters: appear, and keep the caret where it was.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(PANEL_STYLESHEET)

        self._build()
        self._place()

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

        title = QLabel("J A R V I S")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)

        self._collapse_button = self._chrome_button("—", "Réduire", self._toggle_collapse)
        self._debug_button = self._chrome_button("⚙", "Developer / Debug",
                                                 self.debug_requested.emit)
        header.addWidget(self._debug_button)
        header.addWidget(self._collapse_button)
        root.addWidget(self._header)

        # ── the core ─────────────────────────────────────────────────────────
        self._core = JarvisCoreWidget()
        self._core.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._core.setFixedHeight(180)
        self._core.clicked.connect(self._on_core_clicked)
        root.addWidget(self._core)

        # ── what JARVIS last said ────────────────────────────────────────────
        self._spoken = QLabel("Je vous écoute.")
        self._spoken.setObjectName("spoken")
        self._spoken.setWordWrap(True)
        self._spoken.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._spoken.setMinimumHeight(46)
        root.addWidget(self._spoken)

        # ── the state line ───────────────────────────────────────────────────
        self._state = QLabel()
        self._state.setObjectName("state")
        self._state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self._state)

        # ── the confirmation gate, hidden until there is one ─────────────────
        self._confirm_box = self._build_confirmation()
        self._confirm_box.hide()
        root.addWidget(self._confirm_box)

        root.addWidget(self._separator())

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

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._mic_button = QPushButton("🎙  Micro")
        self._mic_button.setCheckable(True)
        self._mic_button.setToolTip(
            "Ouvre le micro. Tant qu'il est fermé, aucun son ne quitte ce PC."
        )
        self._mic_button.clicked.connect(
            lambda: self.mic_toggled.emit(self._mic_button.isChecked())
        )
        buttons.addWidget(self._mic_button, 1)

        self._interrupt_button = QPushButton("■")
        self._interrupt_button.setObjectName("danger")
        self._interrupt_button.setToolTip("Couper JARVIS (INTERRUPT)")
        self._interrupt_button.setFixedWidth(40)
        self._interrupt_button.clicked.connect(self.interrupt_requested.emit)
        buttons.addWidget(self._interrupt_button)
        root.addLayout(buttons)

        # Everything below the core collapses away together.
        self._body = [
            self._spoken, self._state, self._history_area, self._input,
        ]
        self._body_layouts = [buttons]

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

    def _separator(self) -> QFrame:
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

    # ── geometry ─────────────────────────────────────────────────────────────

    def _place(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = COLLAPSED_WIDTH if self._collapsed else self._target_width(area.width())
        # Full height, minus a small inset so the rounded corners read as a
        # panel rather than as a window that failed to maximise.
        inset = 8
        height = area.height() - inset * 2
        x = (
            area.right() - width - inset
            if self._dock == "right"
            else area.left() + inset
        )
        self.setGeometry(x, area.top() + inset, width, height)

    def _target_width(self, available: int) -> int:
        return max(MIN_WIDTH, min(MAX_WIDTH, int(available * self._width_fraction)))

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        for widget in self._body:
            widget.setVisible(not self._collapsed)
        for layout in self._body_layouts:
            for i in range(layout.count()):
                item = layout.itemAt(i).widget()
                if item is not None:
                    item.setVisible(not self._collapsed)
        self._confirm_box.setVisible(
            not self._collapsed and self._last_confirmation is not None
        )
        self._core.setFixedHeight(56 if self._collapsed else 180)
        self._collapse_button.setText("▢" if self._collapsed else "—")
        self._collapse_button.setToolTip("Agrandir" if self._collapsed else "Réduire")
        self._debug_button.setVisible(not self._collapsed)
        self._place()

    # ── dragging, on the header only ─────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._header.geometry().contains(event.position().toPoint())
        ):
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._drag_offset = None
        super().mouseReleaseEvent(event)

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

        # ── state line ───────────────────────────────────────────────────────
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

        # ── the line under the core ──────────────────────────────────────────
        if snapshot.link is LinkState.ERROR:
            self._spoken.setText(snapshot.last_error or "Erreur.")
        elif not snapshot.connected:
            self._spoken.setText("Hors ligne.")
        else:
            spoken = snapshot.last_spoken_line
            self._spoken.setText(spoken if spoken else "Je vous écoute.")

        # ── buttons ──────────────────────────────────────────────────────────
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
            return

        if cid != self._last_confirmation:
            self._last_confirmation = cid
            self._confirm_title.setText(snapshot.confirmation_title or "Confirmer ?")
            self._confirm_detail.setText(snapshot.confirmation_detail)
            self._confirm_box.setVisible(not self._collapsed)

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
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
