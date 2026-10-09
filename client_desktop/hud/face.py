"""
The 3D face, inside the HUD's reactor — driven by `presence.Director`.

The face is an `<iframe>` on `jarvis://avatar/` inside the HUD page, and the
page cannot reach into another origin's `window.JARVIS`. The avatar's own
`bridge.js` already answers that: it listens for `postMessage` with the very
same payloads (`performance`, `speak`, `listen`) — the door built for the phone
and the dashboard. So this driver produces those messages and the HUD page
relays them to the frame.

Where the face is decided: here, locally, on every snapshot. The Director
resolves the *reflex* (listening, thinking, speaking) from state this client
already has, so the face reacts at the speed of the state change and not of a
round trip; what the server sends, when it sends anything, is the *intent*.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from ..state import LinkState, Snapshot

# ── timing and decisions, shared with the phone's renderer ──────────────────
# The same numbers the phone's renderer is tuned against.

#: How often the voice level is pushed. The mouth needs it far more often than
#: the face needs a decision — `avatar/js/bridge.js` explains why they are two
#: different calls — but 60 Hz of `runJavaScript` is a measurable cost for a
#: movement nobody can see at that rate. 25 Hz is under the threshold where a
#: mouth reads as laggy and a quarter of the calls.
SPEECH_INTERVAL_S = 1.0 / 25.0

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


#: A wake older than this stops showing on the face. The HUD's shockwave ring
#: lasts as long: the ring and the face must acknowledge the same wake.
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


class FaceDriver:
    """Snapshot in, `postMessage` payloads out. Silent until `enable(True)`."""

    def __init__(self, post: Callable[[dict], None], director=None) -> None:  # noqa: ANN001
        from presence import Director

        self._post = post
        self._director = director or Director()
        self._enabled = False
        self._ready = False
        self._last_state = ""
        self._last_payload: dict | None = None
        self._last_push_at = 0.0
        self._last_speech_at = 0.0
        self._last_speech = -1.0
        self._last_listen_at = 0.0
        self._last_listen = -1.0
        self._listening = False

    def enable(self, on: bool) -> None:
        self._enabled = on
        if not on:
            self._ready = False

    def frame_ready(self) -> None:
        """The iframe loaded: push the current face at once, not at the next change."""
        self._ready = True
        self._last_state = ""
        self._last_payload = None

    def set_snapshot(self, snapshot: Snapshot) -> None:
        if not (self._enabled and self._ready):
            return
        now = time.monotonic()
        state = _state_word(snapshot)
        if state != self._last_state:
            self._last_state = state
            self._push(self._director.resolve(state, speech_level=snapshot.speaker_level))
        elif now - self._last_push_at >= RERESOLVE_S:
            self._last_push_at = now
            perf = self._director.resolve(state, speech_level=max(0.0, self._last_speech), now=now)
            if _material(perf.as_json(), self._last_payload):
                self._push(perf)

        level = snapshot.speaker_level
        if now - self._last_speech_at >= SPEECH_INTERVAL_S and abs(level - self._last_speech) > 0.01:
            self._last_speech_at = now
            self._last_speech = level
            self._post({"type": "speak", "level": round(level, 3)})

        if state in _USER_FLOOR:
            mic = snapshot.mic_level
            if now - self._last_listen_at >= LISTEN_INTERVAL_S and abs(mic - self._last_listen) > 0.005:
                self._last_listen_at = now
                self._last_listen = mic
                self._listening = True
                self._post({"type": "listen", "level": round(mic, 3)})
        elif self._listening:
            self._listening = False
            self._last_listen = 0.0
            self._post({"type": "listen", "level": 0})

    def set_intent(self, event: object) -> None:
        directive, age = _envelope(event)
        if directive is None:
            return
        try:
            from presence import parse
            parsed = parse(json.dumps(directive))
        except Exception:
            return
        if parsed is None:
            return
        self._director.set_intent(parsed, now=time.monotonic() - age)
        if self._enabled and self._ready:
            self._push(self._director.resolve(
                self._last_state or "ACTIVE", speech_level=max(0.0, self._last_speech)))

    def _push(self, performance) -> None:  # noqa: ANN001
        data = performance.as_json()
        self._post({"type": "performance", "payload": data})
        self._last_payload = data
        self._last_push_at = time.monotonic()
