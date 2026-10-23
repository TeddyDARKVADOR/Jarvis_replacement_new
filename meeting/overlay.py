"""
meeting/overlay.py — la réponse en tout petit, dans un coin de l'écran.

    ov = Overlay(); ov.show("« Teddy, le budget ? »", "8 000 € maximum, dit au début.")

Une fenêtre Qt sans cadre, en bas à droite, toujours au-dessus : elle ne prend
jamais le focus (on continue de taper dans la visio) et les clics la traversent.
Elle s'efface seule après SECONDS.

Invisible pour les autres : sous Windows 10 2004 et plus récent,
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) la retire des captures — le
partage d'écran de Teams, Zoom ou Meet ne la montre pas (ce n'est pas une
protection absolue : une photo de l'écran la montre). macOS : sharingType =
none si PyObjC est là (non vérifié). Linux : aucune API — elle est visible en
partage d'écran, et on le dit.

Appelable depuis n'importe quel fil : un pont (QObject) est déplacé dans le fil
de Qt, et ses signaux y portent l'affichage. Sans QApplication (Oracle, tests
sans écran), `available` est faux et l'appelant se rabat sur le HUD.
"""
from __future__ import annotations

import sys

SECONDS = 25
WDA_EXCLUDEFROMCAPTURE = 0x11
WIDTH = 440


def exclude_from_capture(win_id: int) -> bool:
    """Retire une fenêtre de ce processus des captures d'écran et du partage. Vrai si le système l'a accepté."""
    if sys.platform == "win32":
        import ctypes
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(win_id), WDA_EXCLUDEFROMCAPTURE))
    if sys.platform == "darwin":
        try:
            import objc
            from AppKit import NSView
            view = objc.objc_object(c_void_p=int(win_id))
            if isinstance(view, NSView) and view.window() is not None:
                view.window().setSharingType_(0)        # NSWindowSharingNone
                return True
        except Exception:
            return False
    return False


class Overlay:
    def __init__(self, seconds: int = SECONDS, hide_from_capture: bool = True):
        self.seconds, self.hide_from_capture = seconds, hide_from_capture
        self._bridge = None
        self.available: bool | None = None
        self.excluded: bool | None = None

    def _ensure(self) -> bool:
        if self.available is not None:
            return self.available
        try:
            from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
            from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
        except Exception:
            self.available = False
            return False
        app = QApplication.instance()
        if app is None:
            self.available = False
            return False
        owner = self

        class _Bridge(QObject):
            show_sig = pyqtSignal(str, str)
            hide_sig = pyqtSignal()

            def __init__(self):
                super().__init__()
                self.win = None
                self.timer = None
                self.show_sig.connect(self._show)
                self.hide_sig.connect(self._hide)

            def _build(self):
                win = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                              | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                              | Qt.WindowType.WindowTransparentForInput)
                win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                win.setObjectName("matthewAssistantMeetingOverlay")
                box = QVBoxLayout(win)
                box.setContentsMargins(0, 0, 0, 0)
                self.label = QLabel(win)
                self.label.setWordWrap(True)
                self.label.setTextFormat(Qt.TextFormat.RichText)
                self.label.setFixedWidth(WIDTH)
                self.label.setStyleSheet("QLabel { background: rgba(14, 18, 28, 225); color: #f2f5ff; "
                                         "border: 1px solid rgba(120, 170, 255, 140); border-radius: 10px; "
                                         "padding: 9px 12px; font-size: 10pt; }")
                box.addWidget(self.label)
                self.timer = QTimer(win)
                self.timer.setSingleShot(True)
                self.timer.timeout.connect(win.hide)
                self.win = win

            def _show(self, title: str, text: str):
                if self.win is None:
                    self._build()
                from html import escape
                self.label.setText(f"<div style='color:#8fb4ff;font-size:8.5pt'>Matthew · {escape(title)}</div>"
                                   f"<div style='margin-top:3px'>{escape(text)}</div>")
                self.win.adjustSize()
                screen = QApplication.primaryScreen().availableGeometry()
                self.win.move(screen.right() - self.win.width() - 24, screen.bottom() - self.win.height() - 24)
                self.win.show()
                if owner.hide_from_capture and owner.excluded is None:
                    owner.excluded = exclude_from_capture(int(self.win.winId()))
                self.timer.start(owner.seconds * 1000)

            def _hide(self):
                if self.win is not None:
                    self.win.hide()

        bridge = _Bridge()
        bridge.moveToThread(app.thread())                # ses signaux s'exécutent dans le fil de Qt
        self._bridge = bridge
        self.available = True
        return True

    def show(self, title: str, text: str) -> bool:
        if not self._ensure():
            return False
        self._bridge.show_sig.emit(str(title), str(text))
        return True

    def hide(self) -> None:
        if self._bridge is not None:
            self._bridge.hide_sig.emit()
