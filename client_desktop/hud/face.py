"""
The 3D face, inside the HUD — driven by the same Director as in the panel.

In the panel the face is its own `QWebEngineView` and is driven with
`runJavaScript`. In the HUD it is an `<iframe>` on `jarvis://avatar/` inside the
HUD page, and the page cannot reach into another origin's `window.JARVIS`. The
avatar's own `bridge.js` already answers that: it listens for `postMessage`
with the very same payloads (`performance`, `speak`, `listen`) — the door built
for the phone and the dashboard. So this driver produces those messages and the
HUD page relays them to the frame.

What is decided, and when, is not re-invented here: the state word, the
"material change" test and the timing constants are imported from
`ui/avatar_view.py`, so the face behaves identically in both hosts.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from ..state import Snapshot
from ..ui.avatar_view import (
    LISTEN_INTERVAL_S,
    RERESOLVE_S,
    SPEECH_INTERVAL_S,
    _USER_FLOOR,
    _envelope,
    _material,
    _state_word,
)


class FaceDriver:
    """Snapshot in, `postMessage` payloads out. Silent until `enable(True)`."""

    def __init__(self, post: Callable[[dict], None]) -> None:
        from presence import Director

        self._post = post
        self._director = Director()
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
