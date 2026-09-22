"""
The body, hosted — `avatar/` inside the panel, or nothing at all.

WHY A BROWSER AND NOT Qt3D
    Qt has a 3D module. It has no usable glTF morph-target support, no animation
    retargeting, and no path to the asset ecosystem this whole design rests on —
    Mixamo clips, ARKit blendshapes, Ready Player Me, MetaHuman exports. three.js
    reads all of it, and `PyQt6-WebEngine` was already installed on this machine.

    The second reason is the one that actually decided it: the phone. A renderer
    written against Qt would have to be written a second time against Compose,
    in Kotlin, and the two would drift exactly the way the 2D core did NOT drift
    only because someone ported it primitive for primitive. A web renderer is
    hosted by `QWebEngineView` here and by a `WebView` there, with one copy of
    the code and one protocol. That is the phase-three work, done in advance.

WHY THIS WIDGET IS SHAPED LIKE `JarvisCoreWidget`
    Same three methods, same signal: `set_snapshot`, `set_animated`, `update`,
    `clicked`. `panel.py` therefore builds one or the other and changes nothing
    else. A body that required the panel to be rewritten would be a body that
    could not be turned off — and it must be possible to turn off, because
    WebEngine is a Chromium process and a workstation under load is a real
    reason to prefer the 2 KB core.

WHERE THE FACE IS DECIDED
    Here, locally, on every snapshot — not on the server. `presence.Director`
    resolves the *reflex* (listening, thinking, speaking) from state this client
    already has, so the face reacts at the speed of the state change and not at
    the speed of a round trip. What the server sends, when it sends anything, is
    the *intent*: the expression JARVIS chose because of what he was saying.
    `set_intent` feeds that in, and it outranks the reflex for as long as
    `presence.director.INTENT_TTL_S` allows.

    The consequence worth stating: against an Oracle that has never heard of
    `presence/`, this widget still works completely. It simply never receives an
    intent, and JARVIS has a face that follows his state instead of his words.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from . import avatar_scheme
from ..state import LinkState, Snapshot

#: True when this build can host a body at all. `panel.py` reads it and falls
#: back to the 2D core — an install without PyQt6-WebEngine is a supported
#: install, not a broken one.
try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    AVAILABLE = True
    IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on the local install
    QWebEngineView = None  # type: ignore[assignment]
    QWebEngineSettings = None  # type: ignore[assignment]
    AVAILABLE = False
    IMPORT_ERROR = str(exc)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

#: How often the voice level is pushed. The mouth needs it far more often than
#: the face needs a decision — `avatar/js/bridge.js` explains why they are two
#: different calls — but 60 Hz of `runJavaScript` is a measurable cost for a
#: movement nobody can see at that rate. 25 Hz is under the threshold where a
#: mouth reads as laggy and a quarter of the calls.
SPEECH_INTERVAL_S = 1.0 / 25.0

#: A wake older than this stops showing on the face. Matches
#: `core_widget.WAKE_RING_MAX_AGE` deliberately: the ring and the face must
#: acknowledge the same wake or they contradict each other.
WAKE_MAX_AGE_S = 1.5


def _state_word(snapshot: Snapshot) -> str:
    """The one word `presence.Director` reasons about.

    Not `Snapshot.display_state`, and the difference matters. That property
    answers "what should the label under the core say", which collapses every
    healthy case to CONNECTED. The face needs the opposite: it must distinguish
    a pending confirmation from ordinary listening, because those are the two
    moments a user most needs to read JARVIS's expression correctly.
    """
    if snapshot.awaiting_confirmation:
        return "CONFIRM"
    if snapshot.link is not LinkState.CONNECTED:
        return snapshot.link.value
    if snapshot.woke_at and (time.monotonic() - snapshot.woke_at) < WAKE_MAX_AGE_S:
        return "WAKING"
    return snapshot.assistant.value


class JarvisAvatarWidget(QWidget):
    """A `QWebEngineView` wearing `JarvisCoreWidget`'s interface."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        # Imported lazily so this module can be imported — and its constants
        # read — on a machine with no WebEngine at all.
        from presence import Director

        self._director = Director()
        self._animated = True
        self._loaded = False
        self._last_state = ""
        self._last_gesture = ""
        self._last_speech_at = 0.0
        self._last_speech = -1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = QWebEngineView(self)
        page = self._view.page()
        # Transparent, or the panel gets a black rectangle inside its rounded
        # translucent frame. Both halves are required: the page's own CSS
        # background is transparent, and this is Chromium's compositor.
        page.setBackgroundColor(Qt.GlobalColor.transparent)
        self._view.setStyleSheet("background: transparent;")
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # The panel owns dragging, collapsing and the click that opens the
        # input. A view that swallowed mouse events would make the area where
        # JARVIS's face is the one area of the panel that cannot be dragged.
        self._view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        if QWebEngineSettings is not None:
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.ShowScrollBars, False
            )

        # The body is served from `jarvis://avatar/`, never from `file://`.
        # `avatar_scheme` documents why at length; the short version is that a
        # file:// document gets an opaque origin, every ES module import fails,
        # and the page then loads successfully with no JARVIS in it.
        if not avatar_scheme.install(page.profile()):
            raise RuntimeError(
                "schema jarvis:// non enregistre — avatar_scheme.register() "
                "doit etre appele avant QApplication"
            )

        self._inject_manifest(page)

        self._view.loadFinished.connect(self._on_loaded)
        self._view.setUrl(QUrl(avatar_scheme.INDEX_URL))
        layout.addWidget(self._view)

    def _inject_manifest(self, page) -> None:  # noqa: ANN001
        """Hand the page the manifest instead of letting it fetch one.

        Two reasons, and the second is the one that would bite later.

        *`fetch` does not work under `jarvis://`.* QtWebEngine 6.7 accepts the
        scheme registration and keeps none of its flags, so the document's
        origin is `jarvis://` with no host and the scheme is never CORS-enabled.
        ES modules and XMLHttpRequest are fine — which is why the renderer loads
        and three.js can still read a `.glb` — but `fetch` fails, and on some
        call paths it takes the process down rather than rejecting.
        `ui/avatar_scheme.py` carries the full finding.

        *One read, one truth.* `presence/catalog.py` already parses this file, to
        decide what JARVIS may ask for. If the page fetched its own copy, the two
        could be read a moment apart and disagree — the director would offer a
        gesture the renderer had just decided was not installed, and the symptom
        would be a gesture that silently does nothing.

        Injected at DocumentCreation so it exists before the module runs. A
        manifest that cannot be read is simply not injected, and the page falls
        back to the procedural body on its own.
        """
        from PyQt6.QtWebEngineCore import QWebEngineScript

        try:
            raw = (BASE_DIR / "avatar" / "manifest.json").read_text(encoding="utf-8")
            manifest = json.dumps(json.loads(raw), separators=(",", ":"))
        except Exception as exc:
            print(f"[client] manifeste avatar illisible ({exc}) — corps procedural",
                  flush=True)
            return

        script = QWebEngineScript()
        script.setName("jarvis-manifest")
        script.setSourceCode(f"window.JARVIS_MANIFEST = {manifest};")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        page.scripts().insert(script)

    # ── the JarvisCoreWidget surface ─────────────────────────────────────────

    def set_snapshot(self, snapshot: Snapshot) -> None:
        """Called on every repaint tick, which is roughly 60 times a second.

        Almost every call does nothing, on purpose. A performance is pushed only
        when the resolved state changes; the voice level is pushed on its own
        timer. Sending a full performance at 60 Hz would re-trigger the gesture
        on every frame, and JARVIS would nod continuously.
        """
        if not self._loaded:
            return

        state = _state_word(snapshot)
        if state != self._last_state:
            self._last_state = state
            self._push(self._director.resolve(state, speech_level=snapshot.speaker_level))

        now = time.monotonic()
        level = snapshot.speaker_level
        if now - self._last_speech_at >= SPEECH_INTERVAL_S and abs(level - self._last_speech) > 0.01:
            self._last_speech_at = now
            self._last_speech = level
            self._run(f"window.JARVIS&&window.JARVIS.speak({level:.3f})")

    def set_animated(self, animated: bool) -> None:
        """Stop rendering while the panel is minimised.

        `renderer.setAnimationLoop(null)` is the three.js way to stop the frame
        callback entirely rather than keep drawing into a hidden surface. This
        is the same economy the 2D core makes, and it matters more here: a
        WebGL context left running behind a maximised editor is a GPU cost with
        no viewer, all day.
        """
        if animated == self._animated:
            return
        self._animated = animated
        self._run(
            "window.JARVIS&&window.JARVIS.setAnimated&&"
            f"window.JARVIS.setAnimated({'true' if animated else 'false'})"
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # ── what JARVIS chose, when he chose something ───────────────────────────

    def set_intent(self, directive) -> None:  # noqa: ANN001
        """An `avatar` event arrived from the server. Outranks the reflex.

        Pushed immediately rather than waiting for the next state change: the
        expression JARVIS picked for a sentence has to arrive with the sentence.
        """
        self._director.set_intent(directive)
        self._last_state = ""      # force la prochaine resolution
        self._push(self._director.resolve(self._last_state or "ACTIVE"))

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _push(self, performance) -> None:  # noqa: ANN001
        payload = json.dumps(performance.as_json(), separators=(",", ":"))
        self._run(f"window.JARVIS&&window.JARVIS.perform({payload})")
        self._last_gesture = performance.gesture.value

    def _run(self, script: str) -> None:
        if not self._loaded:
            return
        try:
            self._view.page().runJavaScript(script)
        except Exception:
            # A body that throws must never take down the panel that hosts it.
            # The face freezing is a cosmetic fault; the panel dying is not.
            pass

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = bool(ok)
        if not ok:
            return
        self._last_state = ""
        self._push(self._director.resolve("ACTIVE"))
