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
import os
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

#: `JARVIS_AVATAR_DEBUG=1` : chaque decision poussee au visage s'ecrit dans le
#: journal, avec sa trace causale et ce que le moteur en a REELLEMENT fait.
#: Silencieux par defaut — une ligne par decision est du bruit en production,
#: et une information precieuse le jour ou un visage surprend.
DEBUG = os.environ.get("JARVIS_AVATAR_DEBUG", "").strip() not in ("", "0", "false")

#: The user's voice reaches the face at the rate the microphone measures it.
#:
#: WHY THE FACE HEARS THE MICROPHONE
#:     A listener nods at the speaker's pauses — "go on, I'm following" — and
#:     blinks there too. Without the user's voice level the engine cannot know
#:     where those pauses are, and listening was a head tilt held still: the
#:     face of someone waiting, not of someone listening.
#:
#:     Only the LEVEL crosses, only while the user has the floor
#:     (`_USER_FLOOR`), and a zero when he stops having it. Not while JARVIS
#:     speaks: his own voice leaks into the microphone, and nodding along to
#:     yourself is the one thing worse than not nodding.
LISTEN_INTERVAL_S = 1.0 / 15.0
_USER_FLOOR = frozenset({"LISTENING", "CONFIRM"})

#: How often a decision that is still ageing is asked for again, with no state
#: change to prompt it.
#:
#: WHY THIS EXISTS
#:     A performance used to be pushed on a state change and on an intent's
#:     arrival, never otherwise. Two things the Director computes therefore
#:     never reached the face between state changes: the intent's EXPIRY (a
#:     `warn` sent during a sentence, then a quiet minute in ACTIVE, and the
#:     face stayed concerned for the whole minute — twenty-five seconds is the
#:     documented lifetime), and the affect's DECAY (`affect.decayed()` is "the
#:     whole difference" between calming down and rebooting, and it was only
#:     ever sampled at state changes, in steps).
#:
#:     Re-resolving is a few microseconds of Python; what costs is a push, so
#:     one is sent only when the face would materially change — see
#:     `_material`. The engine recognises a re-sent decision by its
#:     `gesture_id` and follows the new values without restarting anything.
RERESOLVE_S = 1.5

#: Below these, a re-resolved face is the same face. Rounding noise and a
#: decay too slow to see do not cost a `runJavaScript`.
_MATERIAL_SHAPE = 0.03
_MATERIAL_SCALAR = 0.05


def _material(new: dict, old: dict | None) -> bool:
    """Would pushing `new` after `old` change anything anyone could see?"""
    if old is None:
        return True
    for key in ("expression", "gaze", "posture", "gesture_id", "gaze_source", "state"):
        if new.get(key) != old.get(key):
            return True
    if abs(new.get("intensity", 0) - old.get("intensity", 0)) >= _MATERIAL_SHAPE:
        return True
    for key in ("tempo", "stillness"):
        if abs(new.get(key, 0) - old.get(key, 0)) >= _MATERIAL_SCALAR:
            return True
    a, b = new.get("blendshapes", {}), old.get("blendshapes", {})
    return any(abs(a.get(k, 0.0) - b.get(k, 0.0)) >= _MATERIAL_SHAPE for k in set(a) | set(b))


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


def _envelope(event: object) -> tuple[dict | None, float]:
    """The directive and its age, from whatever shape carried them here.

    Two shapes are accepted and that is deliberate. The store emits
    `{"directive": {...}, "age": s}`. A bare directive is also taken, because
    this widget used to be handed exactly that and dropped it: every intent
    JARVIS sent died on this line, while the half before it and the half after
    it each passed their own test. Accepting both means a caller written
    against either contract reaches the Director.

    A dict with no `directive` key is read as a bare directive only if it has
    something a directive can have — an empty or foreign dict stays ignored.
    """
    if not isinstance(event, dict):
        return None, 0.0
    if "directive" in event:
        directive = event.get("directive")
        try:
            age = max(0.0, float(event.get("age", 0.0) or 0.0))
        except (TypeError, ValueError):
            age = 0.0
        return (directive if isinstance(directive, dict) else None), age
    if event and not {"type", "ts"} & set(event):
        return event, 0.0
    return None, 0.0


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
        #: The last JSON pushed, and when — what `_material` compares against.
        self._last_payload: dict | None = None
        self._last_push_at = 0.0
        self._last_listen_at = 0.0
        self._last_listen = -1.0
        self._listening = False

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
        # En mode diagnostic, la page enregistre aussi sa seance — rejouable
        # dans le labo avec `window.JARVIS.recording()`. Voir recorder.js.
        record = "window.JARVIS_RECORD = true;" if DEBUG else ""
        script.setSourceCode(f"window.JARVIS_MANIFEST = {manifest};{record}")
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
        now = time.monotonic()
        if state != self._last_state:
            self._last_state = state
            self._push(self._director.resolve(state, speech_level=snapshot.speaker_level))
        elif now - self._last_push_at >= RERESOLVE_S:
            self.reresolve(now)

        level = snapshot.speaker_level
        if now - self._last_speech_at >= SPEECH_INTERVAL_S and abs(level - self._last_speech) > 0.01:
            self._last_speech_at = now
            self._last_speech = level
            self._run(f"window.JARVIS&&window.JARVIS.speak({level:.3f})")

        self._forward_listen(state, snapshot.mic_level, now)

    def _forward_listen(self, state: str, level: float, now: float) -> None:
        """The user's voice level, while he has the floor. See LISTEN_INTERVAL_S."""
        if state in _USER_FLOOR:
            if now - self._last_listen_at >= LISTEN_INTERVAL_S and abs(level - self._last_listen) > 0.005:
                self._last_listen_at = now
                self._last_listen = level
                self._listening = True
                self._run(f"window.JARVIS&&window.JARVIS.listen&&window.JARVIS.listen({level:.3f})")
        elif self._listening:
            self._listening = False
            self._last_listen = 0.0
            self._run("window.JARVIS&&window.JARVIS.listen&&window.JARVIS.listen(0)")

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

    def set_intent(self, directive, age: float = 0.0) -> None:  # noqa: ANN001
        """An `avatar` event arrived from the server. Outranks the reflex.

        Pushed immediately rather than waiting for the next state change: the
        expression JARVIS picked for a sentence has to arrive with the sentence.

        `age` backdates the intent to when JARVIS decided it, so its 25 s are
        counted from the decision and not from the packet's arrival.
        """
        self._director.set_intent(directive, now=time.monotonic() - max(0.0, age))

        # Resolved against the state we are actually in, and NOT by clearing
        # `_last_state` to force the next tick to redo it. Two reasons, both
        # visible: resolving against "ACTIVE" while JARVIS is mid-sentence is
        # resolving against the wrong state, and letting the next tick push a
        # second performance 16 ms later restarts the gesture that this one just
        # began — a nod that begins twice reads as a stutter.
        #
        # The speech level is carried across for the same kind of reason.
        # `Performance.speech_level` defaults to 0, and `main.js` feeds it
        # straight to the lip-sync, so pushing without it shuts the mouth in the
        # middle of the sentence the expression was chosen FOR. It reopens on
        # the next `speak()`, 40 ms later, which is long enough to see.
        self._push(self._director.resolve(
            self._last_state or "ACTIVE",
            speech_level=max(0.0, self._last_speech),
        ))

    def set_intent_json(self, event: dict) -> None:
        """The same thing, straight off the wire.

        The event carries the directive as JARVIS asked for it — not as anything
        resolved — and it is read here with `presence.parse`, the same function
        the server validated it with. That is the point: one definition of the
        form, used at both ends, so the wire cannot carry something one side
        calls a directive and the other does not.

        `parse` takes text rather than a mapping, which looks like a detour and
        is not: the bare-object path it grew for a model that forgot its fence
        is exactly this shape, so re-serialising costs a few microseconds and
        buys the guarantee that no second parser exists to drift from the first.

        A directive that does not survive the parse is dropped in silence. That
        is the designed answer everywhere else in `presence/` — an unrecognised
        word costs a plainer face and never an error — and the face JARVIS falls
        back to is the one his machine state implies, which is still correct.
        """
        directive, age = _envelope(event)
        if directive is None:
            return
        if DEBUG:
            _log(f"WIRE = {json.dumps(directive, ensure_ascii=False)[:120]} "
                 f"(decide il y a {age * 1000:.0f} ms)")
        try:
            from presence import parse
            parsed = parse(json.dumps(directive))
        except Exception:
            return
        if parsed is not None:
            self.set_intent(parsed, age=age)

    def reresolve(self, now: float | None = None) -> bool:
        """Ask the Director again, in the same state. Push only what changed.

        Called by `set_snapshot` every `RERESOLVE_S`. Returns whether a
        performance was pushed — the chain test reads it.
        """
        now = time.monotonic() if now is None else now
        self._last_push_at = now
        performance = self._director.resolve(
            self._last_state or "ACTIVE",
            speech_level=max(0.0, self._last_speech),
            now=now,
        )
        if not _material(performance.as_json(), self._last_payload):
            return False
        self._push(performance)
        return True

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _push(self, performance) -> None:  # noqa: ANN001
        data = performance.as_json()
        payload = json.dumps(data, separators=(",", ":"))
        self._run(f"window.JARVIS&&window.JARVIS.perform({payload})")
        self._last_gesture = performance.gesture.value
        self._last_payload = data
        self._last_push_at = time.monotonic()
        if DEBUG:
            self._trace(performance)

    def _trace(self, performance) -> None:  # noqa: ANN001
        """La decision en lignes, puis ce que le moteur en a fait.

        La trace dit ce que JARVIS a decide ; RENDER dit ce que le corps a joue
        — `frozen` si `rig.motion` tenait le membre, `absent` si le modele n'a
        pas l'os. C'est l'ecart entre les deux qu'on cherche quand un visage
        surprend, et aucun des deux ne le montre seul.
        """
        try:
            from presence.director import trace
            for line in trace(performance):
                _log(line)
        except Exception as exc:
            _log(f"TRACE indisponible ({exc})")
        if not self._loaded:
            return
        try:
            self._view.page().runJavaScript(
                "JSON.stringify(window.__engine && window.__engine.decision)",
                lambda raw: _log(_render_line(raw)))
        except Exception:
            pass

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
        if DEBUG:
            # Le profil MESURE du modele charge — ce que le moteur va piloter.
            self._view.page().runJavaScript(
                "window.JARVIS && window.JARVIS.profileText && window.JARVIS.profileText()",
                lambda text: [_log(line) for line in str(text or "").splitlines()])


def _log(line: str) -> None:
    """Une ligne horodatee, en ASCII : la console Windows est cp1252."""
    stamp = time.strftime("%H:%M:%S")
    try:
        print(f"[avatar] {stamp} " + line.encode("ascii", "replace").decode("ascii"),
              flush=True)
    except Exception:
        pass


def _render_line(raw) -> str:  # noqa: ANN001
    try:
        decision = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        decision = None
    if not decision:
        # Le modele se charge encore : le pont (bridge.js) garde la derniere
        # decision et la jouera des que le corps existe.
        return "RENDER = en attente — modele en chargement, decision gardee par le pont"
    played = decision.get("gesture_played")
    status = "success" if played in ("procedural", "clip", "deja en cours") else played
    accent = decision.get("accent")
    return (f"RENDER = {status} ({decision.get('gesture')} {played}"
            + (f", accent {accent} {'joue' if decision.get('accent_played') else 'NON joue'}"
               if accent else "") + ")")
