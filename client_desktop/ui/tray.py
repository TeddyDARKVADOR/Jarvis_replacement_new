"""
The tray icon — where JARVIS goes when the panel is not wanted, and where
notifications come from.

`QSystemTrayIcon.showMessage` rather than `win10toast`, which is in the
repository's requirements for the desktop assistant. Two reasons, and the second
is the real one: win10toast spawns a thread and a hidden window per toast and is
unmaintained against current Python, but more importantly a toast raised by Qt
is owned by this process, so clicking it can bring the panel forward. A toast
from a separate window class cannot.

Notifications are also the reason the client is useful while the panel is
collapsed: a confirmation that expires in ninety seconds must reach someone who
is looking at an editor, not at a 68-pixel strip.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ..state import LinkState, Snapshot
from .theme import INK, MUTED, OK, SKY, WARN
from .theme import ERROR as ERROR_COLOUR

_ICON_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent / "config" / "jarvis.ico",
)

_LINK_COLOURS = {
    LinkState.CONNECTED: SKY,
    LinkState.CONNECTING: WARN,
    LinkState.RECONNECTING: WARN,
    LinkState.ERROR: ERROR_COLOUR,
    LinkState.DISCONNECTED: MUTED,
}


def _fallback_icon(colour) -> QIcon:  # noqa: ANN001
    """A lit dot, drawn, for when `config/jarvis.ico` is not where we expect.

    The client must still start from a checkout missing that file — an icon is
    not a reason to refuse to run.
    """
    pixmap = QPixmap(32, 32)
    pixmap.fill(INK)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(0)
        painter.setBrush(colour)
        painter.drawEllipse(8, 8, 16, 16)
    finally:
        painter.end()
    return QIcon(pixmap)


class JarvisTray(QSystemTrayIcon):
    """The menu, the icon, and every notification the client raises."""

    show_panel = pyqtSignal()
    toggle_panel = pyqtSignal()
    debug_requested = pyqtSignal()
    mic_toggled = pyqtSignal(bool)
    reconnect_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_icon: QIcon | None = None
        for candidate in _ICON_CANDIDATES:
            if candidate.is_file():
                self._base_icon = QIcon(str(candidate))
                break
        self.setIcon(self._base_icon or _fallback_icon(MUTED))
        self.setToolTip("JARVIS — hors ligne")

        menu = QMenu()
        self._show_action = QAction("Afficher / masquer", menu)
        self._show_action.triggered.connect(self.toggle_panel.emit)
        menu.addAction(self._show_action)

        self._mic_action = QAction("Ouvrir le micro", menu)
        self._mic_action.setCheckable(True)
        self._mic_action.toggled.connect(self.mic_toggled.emit)
        menu.addAction(self._mic_action)

        menu.addSeparator()
        reconnect = QAction("Reconnecter maintenant", menu)
        reconnect.triggered.connect(self.reconnect_requested.emit)
        menu.addAction(reconnect)

        debug = QAction("Developer / Debug…", menu)
        debug.triggered.connect(self.debug_requested.emit)
        menu.addAction(debug)

        menu.addSeparator()
        quit_action = QAction("Quitter JARVIS", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

        self._last_state = ""

    def _on_activated(self, reason) -> None:  # noqa: ANN001
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_panel.emit()

    def apply(self, snapshot: Snapshot) -> None:
        state = snapshot.display_state
        if state == self._last_state:
            return
        self._last_state = state

        colour = _LINK_COLOURS.get(snapshot.link, MUTED)
        if snapshot.connected and snapshot.gate_open:
            colour = OK
        # The bundled .ico has no state variants, so a coloured dot is the only
        # honest way to show one. When the file is present it is still preferred
        # for the connected case, where it is simply "JARVIS".
        self.setIcon(
            self._base_icon
            if (self._base_icon and snapshot.connected and not snapshot.gate_open)
            else _fallback_icon(colour)
        )
        self.setToolTip(f"JARVIS — {state}")

        self._mic_action.blockSignals(True)
        self._mic_action.setChecked(snapshot.gate_open)
        self._mic_action.blockSignals(False)

    # ── notifications ────────────────────────────────────────────────────────

    def notify(self, title: str, body: str, seconds: int = 6) -> None:
        if not self.supportsMessages():
            return
        self.showMessage(
            title,
            body,
            self._base_icon or _fallback_icon(SKY),
            seconds * 1000,
        )
