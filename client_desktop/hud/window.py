"""
`HudWindow` — JARVIS on this PC: one window, whichever brain it shows.

WHO DRIVES IT
    Online, `app.JarvisDesktop` (the network worker's store, Oracle's events).
    Local, `local_hud.py` (main.py's brain, through `LocalUI`). Both talk to it
    the same way: a `JarvisStore` it pulls snapshots from, seven intent signals
    out, and a handful of methods in. Nothing in here knows which one it is.

WHAT IT IS
    A real window you look at and type in, maximised by default. Its compact
    form is the *mini* mode: a small topmost orb in the corner — presence
    without obstruction.

TWO DIRECTIONS, TWO MECHANISMS
    Python → page   `runJavaScript("HUD.tick(...)")`, ~30 Hz for levels and
                    state, slower for text. Cheap, ordered, no serialisation
                    surprises.
    page → Python   a `QWebChannel` object (`HudBridge`) with one slot per
                    intent. The page never computes state; it asks.
"""

from __future__ import annotations

import base64
import json
import threading
import time

from PyQt6.QtCore import (
    QFile,
    QIODevice,
    QObject,
    QRect,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QFileDialog, QVBoxLayout, QWidget

from ..config import Settings, save as save_settings
from ..state import LinkState, Snapshot
from ..ui import avatar_scheme
from . import metrics as metrics_mod
from . import scheme
from . import settings as settings_mod

#: Levels and state. The orb interpolates between pushes, so 30 is plenty —
#: and the page lowers it (`setPushRate`) on a PC its governor finds slow.
FAST_HZ = 30
#: Counters and the confirmation countdown, then the machine metrics.
SLOW_S = 0.2
METRICS_S = 1.0
LOG_LINES = 80

MINI_SIZE = (260, 300)

#: The page's names for the look-and-feel settings, and where each one lives in
#: `config.Settings` — the one settings file, shared by both modes.
PREF_FIELDS = {
    "accent": "accent",
    "face": "avatar_enabled",
    "sound": "sound",
    "volume": "sound_volume",
    "maximized": "maximized",
    "quality": "quality",
    "quality_tier": "quality_tier",
    "clipboard": "clipboard_assist",
}

_EDGES = {
    "l": Qt.Edge.LeftEdge,
    "r": Qt.Edge.RightEdge,
    "t": Qt.Edge.TopEdge,
    "b": Qt.Edge.BottomEdge,
}


def _face_available() -> bool:
    """The 3D face needs the `jarvis://` scheme and the `presence` package."""
    try:
        import presence  # noqa: F401
    except Exception:
        return False
    return bool(avatar_scheme.AVAILABLE)


def _app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#7DD3FC"))
    p.setBrush(QColor(125, 211, 252, 60))
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QColor("#7DD3FC"))
    p.drawEllipse(24, 24, 16, 16)
    p.end()
    return QIcon(pix)


class HudBridge(QObject):
    """Everything the page can ask for. Each slot only emits or forwards."""

    def __init__(self, window: "HudWindow") -> None:
        super().__init__(window)
        self._w = window

    @pyqtSlot()
    def ready(self) -> None:
        self._w._on_page_ready()

    @pyqtSlot(str)
    def send(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._w.command_submitted.emit(text)

    @pyqtSlot(bool)
    def mic(self, open_: bool) -> None:
        self._w.mic_toggled.emit(bool(open_))

    @pyqtSlot()
    def interrupt(self) -> None:
        self._w.interrupt_requested.emit()

    @pyqtSlot(str, bool)
    def confirm(self, cid: str, accepted: bool) -> None:
        if cid:
            self._w.confirmation_answered.emit(cid, bool(accepted))

    @pyqtSlot()
    def settings(self) -> None:
        """The developer window (counters, raw log). The HUD's own settings
        page is `requestSettings` / `saveSettings`."""
        self._w.debug_requested.emit()

    @pyqtSlot()
    def requestSettings(self) -> None:  # noqa: N802
        self._w._push_settings()

    @pyqtSlot(str)
    def saveSettings(self, raw: str) -> None:  # noqa: N802
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if isinstance(data, dict):
            self._w._save_settings(data)

    @pyqtSlot(str)
    def testConnection(self, raw: str) -> None:  # noqa: N802
        try:
            data = json.loads(raw)
        except ValueError:
            return
        self._w._test_connection(data if isinstance(data, dict) else {})

    @pyqtSlot()
    def installWakeWord(self) -> None:  # noqa: N802
        self._w.wake_install_requested.emit()

    @pyqtSlot()
    def quit(self) -> None:
        self._w.quit_requested.emit()

    @pyqtSlot()
    def hideToTray(self) -> None:  # noqa: N802
        self._w.hide()

    @pyqtSlot()
    def minimize(self) -> None:
        self._w.showMinimized()

    @pyqtSlot()
    def toggleMaximize(self) -> None:  # noqa: N802
        self._w._toggle_maximize()

    @pyqtSlot()
    def fullscreen(self) -> None:
        self._w._toggle_fullscreen()

    @pyqtSlot()
    def startMove(self) -> None:  # noqa: N802
        handle = self._w.windowHandle()
        if handle is not None and not self._w.isMaximized() and not self._w.isFullScreen():
            handle.startSystemMove()

    @pyqtSlot(str)
    def startResize(self, edges: str) -> None:  # noqa: N802
        handle = self._w.windowHandle()
        if handle is None or self._w.isMaximized() or self._w._mini or self._w.isFullScreen():
            return
        flags = Qt.Edge(0)
        for ch in edges:
            if ch in _EDGES:
                flags |= _EDGES[ch]
        if flags:
            handle.startSystemResize(flags)

    @pyqtSlot(bool)
    def setMini(self, mini: bool) -> None:  # noqa: N802
        self._w._set_mini(bool(mini))

    @pyqtSlot(str)
    def savePrefs(self, raw: str) -> None:  # noqa: N802
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if isinstance(data, dict):
            self._w._update_prefs(data)

    @pyqtSlot(int)
    def setPushRate(self, hz: int) -> None:  # noqa: N802
        self._w._set_push_rate(hz)

    @pyqtSlot()
    def faceLoaded(self) -> None:  # noqa: N802
        if self._w._face is not None:
            self._w._face.frame_ready()

    @pyqtSlot(str)
    def copy(self, text: str) -> None:
        self._w._own_copy = text or ""
        QApplication.clipboard().setText(text or "")

    @pyqtSlot(str, str, str)
    def rpc(self, rid: str, op: str, payload: str) -> None:
        """One request to the assistant's backend; answered by `HUD.rpcResult`."""
        try:
            data = json.loads(payload) if payload else {}
        except ValueError:
            data = {}
        self._w._rpc(rid, op, data if isinstance(data, dict) else {})

    @pyqtSlot()
    def attachFile(self) -> None:  # noqa: N802
        path, _ = QFileDialog.getOpenFileName(self._w, "Fichier à analyser par JARVIS")
        if path:
            self._w.attach_file(path)

    @pyqtSlot()
    def clearFile(self) -> None:  # noqa: N802
        if self._w.backend is not None:
            self._w.backend.clear_file()

    @pyqtSlot(str)
    def openUrl(self, url: str) -> None:  # noqa: N802
        if url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))

    @pyqtSlot(str, str, str, str)
    def openPreview(self, project: str, url: str, title: str, status: str) -> None:  # noqa: N802
        """The Studio's web preview, in its own isolated window (hud/preview.py)."""
        self._w.request_preview(project, url, title, status)


class _Page(QWebEnginePage):
    """Links in JARVIS's answers open in the real browser, never inside the HUD."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802, ANN001
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802, ANN001
        # Errors only: a HUD that fails to draw should say why in the terminal.
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            try:
                print(f"[hud] {source}:{line} {message}".encode("ascii", "replace").decode(),
                      flush=True)
            except Exception:
                pass


class HudWindow(QWidget):
    """The HUD. Renders snapshots and emits intent; decides nothing."""

    command_submitted = pyqtSignal(str)
    interrupt_requested = pyqtSignal()
    mic_toggled = pyqtSignal(bool)
    confirmation_answered = pyqtSignal(str, bool)
    debug_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    placement_changed = pyqtSignal()
    #: HUD-only: the settings page saved; carries the keys that changed. Wired
    #: to the controller by `hud/__main__.py`, since the controller predates it.
    settings_saved = pyqtSignal(list)
    wake_install_requested = pyqtSignal()
    #: Carries a connection-test result from its worker thread to the GUI one.
    _test_done = pyqtSignal(dict)
    #: Backend answers, from their worker threads to the GUI one.
    _rpc_done = pyqtSignal(str, dict)
    _file_done = pyqtSignal(dict)
    #: Any thread → GUI thread: show the Studio's preview window.
    _preview_requested = pyqtSignal(str, str, str, str)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        #: Geometry is written a second after the window stops moving, not on
        #: every pixel of a drag.
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(1000)
        self._geometry_timer.timeout.connect(lambda: save_settings(self.settings))
        self._mini = False
        self._restore_geometry: QRect | None = None
        self._restore_maximized = False
        self._loaded = False
        self._store = None
        self._snapshot = Snapshot()
        self._last_slow = 0.0
        self._last_metrics = 0.0
        self._last_messages: tuple | None = None
        self._last_log: tuple | None = None
        self._first_show = True
        self._metrics = metrics_mod.Metrics()
        self._face = None
        self._face_available = _face_available()
        #: True when the brain runs on this PC (`python main.py --local`). The
        #: HUD is the same; only what it says about "the server" changes.
        self.local_mode = False
        #: Who answers the settings page's "assistant" questions and receives
        #: attached files — see hud/backends.py. Set by the host.
        self.backend = None
        #: Local mode's audio and wake settings belong to main.py's config, not
        #: to the network client's: the host overlays them on the page here.
        self.settings_overrides = None
        self._own_copy = ""
        self._preview = None

        self.setWindowTitle("J.A.R.V.I.S — HUD")
        self.setWindowIcon(_app_icon())
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(*MINI_SIZE)
        self.setStyleSheet("background: #03060B;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(self)
        page = _Page(self._view)
        self._view.setPage(page)
        page.setBackgroundColor(QColor("#03060B"))
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        if not scheme.install(page.profile()):
            raise RuntimeError("schema jarvis-hud:// non enregistre avant QApplication")
        # The face lives on jarvis://avatar/ — same profile, its own handler.
        if self._face_available and avatar_scheme.install(page.profile()):
            from .face import FaceDriver
            self._face = FaceDriver(self._post_face)
        else:
            self._face_available = False

        self._channel = QWebChannel(page)
        self._bridge = HudBridge(self)
        self._channel.registerObject("hud", self._bridge)
        page.setWebChannel(self._channel)
        self._inject_scripts(page)

        self._view.loadFinished.connect(self._on_loaded)
        self._view.setUrl(QUrl(scheme.INDEX_URL))
        layout.addWidget(self._view)

        self._restore_saved_geometry()
        self._test_done.connect(
            lambda r: self._run(f"window.HUD&&HUD.settingsTest({json.dumps(r, ensure_ascii=False)})"))
        self._rpc_done.connect(
            lambda rid, r: self._run(f"window.HUD&&HUD.rpcResult({json.dumps(rid)},{json.dumps(r, ensure_ascii=False)})"))
        self._file_done.connect(
            lambda r: self._run(f"window.HUD&&HUD.fileAttached({json.dumps(r, ensure_ascii=False)})"))
        self._preview_requested.connect(self._show_preview)

        # A file dropped anywhere on the HUD is a file for JARVIS. The view must
        # not take the drop itself, or Chromium would navigate to the file.
        self._view.setAcceptDrops(False)
        self.setAcceptDrops(True)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fast_tick)
        self._timer.start(1000 // FAST_HZ)

    # ── the Studio's preview window ──────────────────────────────────────────

    def request_preview(self, project: str, url: str, title: str = "", status: str = "") -> bool:
        """Thread-safe. Only the project's own local server can be previewed."""
        if not str(url).startswith("http://127.0.0.1:"):
            return False
        self._preview_requested.emit(str(project), str(url), str(title), str(status))
        return True

    def _show_preview(self, project: str, url: str, title: str, status: str) -> None:
        if self._preview is None:
            from .preview import PreviewWindow

            def stop(name: str) -> None:
                backend = self.backend
                if backend is not None:
                    try:
                        backend.call("studio.stop_preview", {"project": name})
                    except Exception:
                        pass

            self._preview = PreviewWindow(on_stop=stop)
            self._preview.setWindowIcon(self.windowIcon())
        self._preview.show_preview(project, url, title, status)

    @property
    def prefs(self) -> dict:
        """The look-and-feel settings, under the names the page uses."""
        return {key: getattr(self.settings, attr) for key, attr in PREF_FIELDS.items()}

    # ── page setup ───────────────────────────────────────────────────────────

    def _inject_scripts(self, page: QWebEnginePage) -> None:
        scripts = page.scripts()

        source = QFile(":/qtwebchannel/qwebchannel.js")
        if source.open(QIODevice.OpenModeFlag.ReadOnly):
            js = bytes(source.readAll()).decode("utf-8")
            source.close()
            channel = QWebEngineScript()
            channel.setName("qwebchannel")
            channel.setSourceCode(js)
            channel.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            channel.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            channel.setRunsOnSubFrames(False)
            scripts.insert(channel)

        if self._face_available:
            # The face reads its manifest from a global, never from fetch —
            # `ui/avatar_scheme.py` explains why. Here it is in a
            # sub-frame, so the script must run on sub-frames.
            try:
                raw = (avatar_scheme.ROOT / "manifest.json").read_text(encoding="utf-8")
                manifest = json.dumps(json.loads(raw), separators=(",", ":"))
                m = QWebEngineScript()
                m.setName("jarvis-manifest")
                from .face import DEBUG as face_debug
                # In diagnostic mode the face also records its session.
                record = "window.JARVIS_RECORD = true;" if face_debug else ""
                m.setSourceCode(f"window.JARVIS_MANIFEST = {manifest};{record}")
                m.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
                m.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
                m.setRunsOnSubFrames(True)
                scripts.insert(m)
            except Exception:
                pass

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = bool(ok)

    def _on_page_ready(self) -> None:
        """The page's bridge is connected: hand it everything that is static."""
        self._loaded = True
        self._last_messages = None
        self._last_log = None
        init = {
            "prefs": self.prefs,
            "identity": metrics_mod.identity(),
            "server": {
                "host": self.settings.host,
                "port": self.settings.port,
                "tls": self.settings.use_tls,
                "device": self.settings.device_name,
                "paired": self.settings.is_configured or self.local_mode,
                "local": self.local_mode,
            },
            "local": self.local_mode,
            "faceAvailable": self._face_available,
            "caps": self.backend.capabilities() if self.backend is not None else {},
            "mini": self._mini,
        }
        self._run(f"window.HUD&&HUD.init({json.dumps(init)})")
        if self._face is not None:
            self._face.enable(bool(self.prefs.get("face")) and self._face_available)
        self._push(self._snapshot, full=True)
        tab = getattr(self, "_pending_settings_tab", None)
        if tab:
            self._pending_settings_tab = None
            self._run(f"window.HUD&&HUD.openSettings({json.dumps(tab)})")

    # ── the controller's surface ─────────────────────────────────────────────

    def attach_store(self, store) -> None:  # noqa: ANN001
        """Let the HUD pull snapshots at its own rate (the controller pushes at 10 Hz)."""
        self._store = store

    def set_avatar_intent(self, event: dict) -> None:
        if self._face is not None:
            self._face.set_intent(event)
        # The HUD's reactor also reacts to the expression JARVIS chose.
        directive = event.get("directive") if isinstance(event, dict) else None
        if isinstance(directive, dict):
            self._run(f"window.HUD&&HUD.intent({json.dumps(directive)})")

    def show(self) -> None:  # noqa: D401 - Qt override
        if self._first_show:
            self._first_show = False
            if self.prefs.get("maximized", True) and not self._mini:
                super().showMaximized()
                return
        super().show()
        if self.isMinimized():
            # Clear only the minimised bit, so a maximised HUD comes back maximised.
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.activateWindow()

    # ── the settings page ────────────────────────────────────────────────────

    def open_settings(self, tab: str = "connexion") -> None:
        """Show the HUD and its settings page. Used for the first run, when
        this PC has never been paired, instead of the developer window."""
        self.show()
        self.raise_()
        self._pending_settings_tab = tab
        if self._loaded:
            self._run(f"window.HUD&&HUD.openSettings({json.dumps(tab)})")

    def _push_settings(self) -> None:
        try:
            data = settings_mod.snapshot(self.settings)
            if callable(self.settings_overrides):
                data.update(self.settings_overrides())
            data["local"] = self.local_mode
        except Exception as exc:  # an audio stack that cannot be listed, etc.
            data = {"error": str(exc)}
        self._run(f"window.HUD&&HUD.settingsData({json.dumps(data, ensure_ascii=False)})")

    def _save_settings(self, data: dict) -> None:
        changed = settings_mod.apply(self.settings, data)
        result = {"ok": True, "changed": changed, "detail": "Paramètres enregistrés."}
        if "autostart" in data and bool(data["autostart"]) != settings_mod.autostart_enabled():
            ok, detail = settings_mod.set_autostart(bool(data["autostart"]))
            result["autostart"] = detail if ok else f"échec : {detail}"
            if not ok:
                result["ok"] = False
        # Emitted even with nothing changed: the controller saves the file, and
        # "Enregistrer" on an unchanged form should still mean "it is on disk".
        self.settings_saved.emit(changed)
        if set(changed) & settings_mod.RECONNECT_KEYS:
            result["detail"] = "Enregistré — reconnexion à JARVIS…"
        self._run(f"window.HUD&&HUD.settingsSaved({json.dumps(result, ensure_ascii=False)})")
        self._push_settings()
        server = {"host": self.settings.host, "port": self.settings.port, "tls": self.settings.use_tls,
                  "device": self.settings.device_name,
                  "paired": self.settings.is_configured or self.local_mode, "local": self.local_mode}
        self._run(f"window.HUD&&HUD.server({json.dumps(server, ensure_ascii=False)})")

    def _test_connection(self, data: dict) -> None:
        def work() -> None:
            try:
                result = settings_mod.test_connection(
                    str(data.get("host", "")), int(data.get("port") or 8000),
                    bool(data.get("use_tls")), str(data.get("device_token", "")))
            except Exception as exc:
                result = {"ok": False, "step": "error", "detail": str(exc)}
            self._test_done.emit(result)
        threading.Thread(target=work, name="hud-settings-test", daemon=True).start()

    # ── the assistant's backend ──────────────────────────────────────────────

    def _rpc(self, rid: str, op: str, data: dict) -> None:
        backend = self.backend

        def work() -> None:
            if backend is None:
                result = {"error": "indisponible dans ce mode"}
            else:
                try:
                    result = backend.call(op, data)
                except Exception as exc:
                    result = {"error": str(exc)}
            self._rpc_done.emit(rid, result if isinstance(result, dict) else {"result": result})

        threading.Thread(target=work, name=f"hud-rpc-{op}", daemon=True).start()

    def attach_file(self, path: str) -> None:
        """Hand a file to JARVIS ("analyse ce fichier"). Uploads can be slow."""
        backend = self.backend
        self._run("window.HUD&&HUD.fileAttached({\"busy\":true})")

        def work() -> None:
            try:
                result = backend.attach_file(path) if backend else {"error": "indisponible"}
            except Exception as exc:
                result = {"error": str(exc)}
            self._file_done.emit(result)

        threading.Thread(target=work, name="hud-attach", daemon=True).start()

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self._run("window.HUD&&HUD.dragging(true)")

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._run("window.HUD&&HUD.dragging(false)")

    def dropEvent(self, event) -> None:  # noqa: ANN001, N802
        self._run("window.HUD&&HUD.dragging(false)")
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if files:
            event.acceptProposedAction()
            self.attach_file(files[0])

    def _on_clipboard(self) -> None:
        """Something was copied: offer to translate / summarise / explain / fix it."""
        if not self.settings.clipboard_assist or not self._loaded:
            return
        if not self.isVisible() or self.isMinimized():
            return
        text = (QApplication.clipboard().text() or "").strip()
        if len(text) < 8 or text == self._own_copy.strip():
            return
        self._run(f"window.HUD&&HUD.clipboard({json.dumps(text[:800], ensure_ascii=False)})")

    def show_camera_frame(self, jpeg: bytes | None) -> None:
        """A webcam (or screen) frame for the picture-in-picture; None hides it."""
        if jpeg is None:
            self._run("window.HUD&&HUD.camera(null)")
            return
        b64 = base64.b64encode(jpeg).decode("ascii")
        self._run(f"window.HUD&&HUD.camera('data:image/jpeg;base64,{b64}')")

    # ── window behaviour ─────────────────────────────────────────────────────

    def _restore_saved_geometry(self) -> None:
        geo = self.settings.window_geometry
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1600, 900)
        if isinstance(geo, list) and len(geo) == 4:
            rect = QRect(*[int(v) for v in geo])
            if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                self.setGeometry(rect)
                return
        w, h = int(avail.width() * 0.8), int(avail.height() * 0.85)
        self.setGeometry(avail.x() + (avail.width() - w) // 2,
                         avail.y() + (avail.height() - h) // 2, w, h)

    def _toggle_maximize(self) -> None:
        if self._mini:
            return
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_prefs({"maximized": self.isMaximized()})

    def _toggle_fullscreen(self) -> None:
        if self._mini:
            return
        if self.isFullScreen():
            self.showMaximized() if self.prefs.get("maximized") else self.showNormal()
        else:
            self.showFullScreen()

    def _set_mini(self, mini: bool) -> None:
        if mini == self._mini:
            return
        if mini:
            self._restore_maximized = self.isMaximized() or self.isFullScreen()
            self._restore_geometry = self.normalGeometry()
            self._mini = True
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint)
            screen = self.screen() or QGuiApplication.primaryScreen()
            avail = screen.availableGeometry()
            w, h = MINI_SIZE
            self.setGeometry(avail.right() - w - 16, avail.bottom() - h - 16, w, h)
            super().show()
        else:
            self._mini = False
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
            if self._restore_geometry is not None:
                self.setGeometry(self._restore_geometry)
            if self._restore_maximized:
                super().showMaximized()
            else:
                super().show()
            self.activateWindow()
        self._run(f"window.HUD&&HUD.setMini({'true' if mini else 'false'})")

    def _update_prefs(self, data: dict) -> None:
        face_before = bool(self.settings.avatar_enabled)
        for key, value in data.items():
            if key in PREF_FIELDS:
                setattr(self.settings, PREF_FIELDS[key], value)
        save_settings(self.settings)
        face_now = bool(self.prefs.get("face")) and self._face_available
        if self._face is not None and face_now != face_before:
            self._face.enable(face_now)

    def _save_geometry(self) -> None:
        if self._mini or self.isMaximized() or self.isFullScreen() or self.isMinimized():
            return
        g = self.geometry()
        self.settings.window_geometry = [g.x(), g.y(), g.width(), g.height()]
        self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # noqa: ANN001, N802
        super().moveEvent(event)
        self._save_geometry()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._save_geometry()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self._timer.stop()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().changeEvent(event)
        # A minimised HUD stops animating — a WebGL loop behind the taskbar is a
        # GPU cost with nobody watching.
        if self._loaded:
            self._run(f"window.HUD&&HUD.setAnimated({'false' if self.isMinimized() else 'true'})")

    # ── pushing state ────────────────────────────────────────────────────────

    def _fast_tick(self) -> None:
        if self._store is not None:
            self._snapshot = self._store.snapshot()
        if not self._loaded or not self.isVisible() or self.isMinimized():
            return
        # Time-based, not tick-counted, so a lower push rate keeps the same
        # counter and metrics cadence.
        now = time.monotonic()
        full = now - self._last_slow >= SLOW_S
        if full:
            self._last_slow = now
        self._push(self._snapshot, full=full)
        if self._face is not None:
            self._face.set_snapshot(self._snapshot)
        if now - self._last_metrics >= METRICS_S:
            self._last_metrics = now
            self._run(f"window.HUD&&HUD.metrics({json.dumps(self._metrics.sample())})")

    def _set_push_rate(self, hz: int) -> None:
        hz = max(10, min(FAST_HZ, int(hz or FAST_HZ)))
        self._timer.setInterval(1000 // hz)

    def _push(self, s: Snapshot, full: bool) -> None:
        if not self._loaded:
            return
        now = time.monotonic()
        data: dict = {
            "state": s.display_state,
            "link": s.link.value,
            "assistant": s.assistant.value,
            "gate": s.gate_open,
            "micOpen": s.mic_open,
            "mic": round(s.mic_level, 3),
            "voice": round(s.speaker_level, 3),
            "wakeAge": round(now - s.woke_at, 2) if s.woke_at else None,
        }
        if s.awaiting_confirmation:
            data["confirm"] = {
                "id": s.confirmation_id,
                "title": s.confirmation_title,
                "detail": s.confirmation_detail,
                "remaining": max(0.0, s.confirmation_deadline - now) if s.confirmation_deadline else None,
            }
        else:
            data["confirm"] = None
        if full:
            data["counters"] = {
                "sent": s.bytes_sent, "received": s.bytes_received, "played": s.bytes_played,
                "framesSent": s.frames_sent, "framesReceived": s.frames_received,
                "framesPlayed": s.frames_played, "dropped": s.frames_dropped,
                "rate": s.downlink_rate,
            }
            data["wake"] = {"name": s.wake_word_name, "score": round(s.wake_word_score, 3)}
            data["retry"] = {"attempt": s.attempt, "next": s.next_retry_seconds}
            data["error"] = s.last_error
            if s.messages is not self._last_messages:
                self._last_messages = s.messages
                data["messages"] = [
                    {"j": m.from_jarvis, "t": m.text, "a": m.at, "ti": m.title}
                    for m in s.messages
                ]
            if s.log is not self._last_log:
                self._last_log = s.log
                data["log"] = list(s.log[-LOG_LINES:])
        self._run(f"window.HUD&&HUD.tick({json.dumps(data, ensure_ascii=False)})")

    def _post_face(self, message: dict) -> None:
        self._run(f"window.HUD&&HUD.face({json.dumps(message, separators=(',', ':'))})")

    def _run(self, script: str) -> None:
        try:
            self._view.page().runJavaScript(script)
        except Exception:
            pass
