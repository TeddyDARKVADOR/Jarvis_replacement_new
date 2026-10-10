"""
`PreviewWindow` — the Code Studio's web preview, in a window of its own.

WHY NOT AN IFRAME IN THE HUD
    The HUD page carries the QWebChannel bridge (`hud.send`, `hud.rpc`…), and
    Qt exposes the channel transport to sub-frames too. A generated page in an
    iframe could then talk to JARVIS. Here the page gets a view of its own,
    with no channel at all.

WHAT THE PAGE GETS
    • an off-the-record profile: no cookie, cache or storage shared with the
      HUD or kept after the window closes;
    • only its own local server (http://127.0.0.1:<port>), data: and blob:
      URLs. Every other request — CDN, API, tracker — is blocked and counted
      in the toolbar, exactly like the Studio's browser check;
    • no new windows, no downloads, no permission prompts (Qt denies them when
      nobody answers).

This is a real run of the project's JavaScript, not a simulation: the toolbar
says whether the Studio validated the project, and ARRÊTER stops the server.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

BADGES = {
    "ok": ("VALIDÉ", "#6EE7B7"),
    "partial": ("PARTIELLEMENT VÉRIFIÉ", "#FBBF6B"),
    "unverified": ("NON VÉRIFIÉ", "#FBBF6B"),
    "blocked": ("NON VALIDÉ — PROBLÈME RESTANT", "#F87171"),
}

_STYLE = """
QWidget#preview { background: #03060B; }
QLabel { color: #b9c6da; font-family: 'Segoe UI'; font-size: 12px; }
QLabel#title { color: #7DD3FC; font-size: 14px; letter-spacing: 2px; }
QLabel#note { color: #6b7a90; font-size: 11px; }
QPushButton { background: rgba(125,211,252,0.06); color: #7DD3FC; border: 1px solid rgba(125,211,252,0.35);
              padding: 5px 12px; font-size: 11px; letter-spacing: 1px; }
QPushButton:hover { background: rgba(125,211,252,0.16); }
QPushButton#stop { color: #F87171; border-color: rgba(248,113,113,0.5); }
"""


def allowed(url: QUrl, port: int) -> bool:
    """Only the project's own local server, and inline data. Pure: the selftest uses it."""
    scheme = url.scheme()
    if scheme in ("data", "blob", "about"):
        return True
    return scheme in ("http", "ws") and url.host() == "127.0.0.1" and url.port() == port


class _Interceptor(QWebEngineUrlRequestInterceptor):
    """Runs on Chromium's IO thread: decides, then reports through a signal."""

    blocked = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.port = -1

    def interceptRequest(self, info) -> None:  # noqa: N802, ANN001
        url = info.requestUrl()
        if not allowed(url, self.port):
            info.block(True)
            self.blocked.emit(url.toString()[:200])


class _Page(QWebEnginePage):
    navigation_blocked = pyqtSignal(str)

    def __init__(self, profile: QWebEngineProfile, parent: QObject, port_ref) -> None:
        super().__init__(profile, parent)
        self._port = port_ref

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802, ANN001
        if allowed(url, self._port()):
            return True
        self.navigation_blocked.emit(url.toString()[:200])
        return False

    def createWindow(self, _type):  # noqa: N802, ANN001
        return None                       # target=_blank / window.open: refused


class PreviewWindow(QWidget):
    """One window, reused for whichever project is previewed."""

    def __init__(self, on_stop=None, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("preview")
        self.setStyleSheet(_STYLE)
        self.setWindowTitle("JARVIS — Aperçu du Studio")
        self.resize(1180, 820)
        self._on_stop = on_stop
        self.project = ""
        self.url = ""
        self._blocked: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 8, 14, 8)
        self._title = QLabel("", bar)
        self._title.setObjectName("title")
        self._badge = QLabel("", bar)
        self._blocked_label = QLabel("", bar)
        row.addWidget(self._title)
        row.addWidget(self._badge)
        row.addStretch(1)
        row.addWidget(self._blocked_label)
        for text, slot, name in (("ACTUALISER", self._reload, ""), ("NAVIGATEUR", self._external, ""),
                                 ("ARRÊTER L'APERÇU", self._stop, "stop"), ("FERMER", self.close, "")):
            b = QPushButton(text, bar)
            if name:
                b.setObjectName(name)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addWidget(bar)
        note = QLabel("Exécution réelle du projet dans un navigateur à part : profil privé, aucun lien avec "
                      "JARVIS, Internet bloqué (seul le serveur local du projet répond).", self)
        note.setObjectName("note")
        note.setContentsMargins(14, 0, 14, 6)
        root.addWidget(note)

        # The view first, then the profile: Qt deletes children in creation
        # order, and a page must go before its profile.
        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile(self)               # no storage name → off the record
        self._interceptor = _Interceptor(self._profile)
        self._interceptor.blocked.connect(self._count_blocked)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        page = _Page(self._profile, self._view, lambda: self._interceptor.port)
        page.navigation_blocked.connect(self._count_blocked)
        self._view.setPage(page)
        root.addWidget(self._view, 1)

    def show_preview(self, project: str, url: str, title: str = "", status: str = "") -> None:
        qurl = QUrl(url)
        self.project, self.url = project, url
        self._interceptor.port = qurl.port()
        self._blocked = []
        self._blocked_label.setText("")
        self._title.setText((title or project).upper())
        label, color = BADGES.get(status, ("", "#b9c6da"))
        self._badge.setText(label)
        self._badge.setStyleSheet(f"color: {color}; border: 1px solid {color}; padding: 2px 8px;"
                                  if label else "")
        self.setWindowTitle(f"JARVIS — Aperçu : {title or project}")
        self._view.setUrl(qurl)
        self.show()
        self.raise_()
        self.activateWindow()

    def _count_blocked(self, url: str) -> None:
        if url not in self._blocked:
            self._blocked.append(url)
        self._blocked_label.setText(f"{len(self._blocked)} requête(s) externe(s) bloquée(s)")
        self._blocked_label.setToolTip("\n".join(self._blocked[:20]))

    def _reload(self) -> None:
        self._view.reload()

    def _external(self) -> None:
        if self.url:
            QDesktopServices.openUrl(QUrl(self.url))

    def _stop(self) -> None:
        project, fn = self.project, self._on_stop
        if callable(fn) and project:
            threading.Thread(target=fn, args=(project,), daemon=True, name="studio-preview-stop").start()
        self._view.setUrl(QUrl("about:blank"))
        self.close()
