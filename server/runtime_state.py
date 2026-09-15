"""
server/runtime_state.py — what JARVIS is doing, in one readable place.

WHY THIS EXISTS AT ALL
    main.py already knows its own state: it calls ui.set_state("LISTENING") /
    "THINKING" / "SPEAKING" / "SLEEPING" at every transition, and it writes a
    log line at every connection event. On the desktop those two streams drive
    the HUD. Headless, they have nowhere to go — so this module catches them.

    It is therefore NOT a second state machine. It is a recorder wired to the
    state stream MARK LIII already emits. Nothing here decides anything; the
    only authority on whether the Live session is up is main.py itself, and
    snapshot() asks it directly through `bind_session_probe`.

WHY THE PROBE INSTEAD OF PARSING LOG TEXT
    The first sketch derived "connected" from log strings ("SYS: JARVIS
    online."). That works until somebody rephrases a log line, and then /status
    lies without a single test failing. `jarvis.session is not None` is the same
    fact read from the object that owns it, and it cannot drift.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from enum import Enum


class State(str, Enum):
    """str-valued so it serialises to JSON with no encoder."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING   = "CONNECTING"
    CONNECTED    = "CONNECTED"
    LISTENING    = "LISTENING"
    THINKING     = "THINKING"
    SPEAKING     = "SPEAKING"
    SLEEPING     = "SLEEPING"
    RECONNECTING = "RECONNECTING"
    ERROR        = "ERROR"


# States that only make sense while a Live session is actually open. If the
# probe says the session is gone while one of these is current, the UI stream
# is simply stale — snapshot() corrects it rather than reporting LISTENING to a
# phone that nothing is listening on.
_NEEDS_SESSION = {
    State.CONNECTED, State.LISTENING, State.THINKING,
    State.SPEAKING, State.SLEEPING,
}

# MARK LIII's UI vocabulary → ours. SLEEPING is MARK LIII's word for the
# wake-word idle state and it is kept as-is: renaming it would make the phone
# and the desktop disagree about the same moment.
_UI_MAP = {
    "LISTENING": State.LISTENING,
    "THINKING":  State.THINKING,
    "SPEAKING":  State.SPEAKING,
    "SLEEPING":  State.SLEEPING,
}


class RuntimeState:
    """Thread-safe. Written from the asyncio loop, from sounddevice's writer
    thread, and from action executor threads; read from FastAPI handlers."""

    def __init__(self, label: str = "MARK LIII"):
        self._lock       = threading.Lock()
        self._label      = label
        self._state      = State.DISCONNECTED
        self._since      = time.time()
        self.started_at  = time.time()

        self._last_error    = ""
        self._last_error_at = 0.0
        self._log: deque    = deque(maxlen=200)

        self._audio_level      = 0.0
        self._phone_streams    = 0      # open /ws/phone-out sockets
        self._phone_last_seen  = 0.0
        self._pending_confirm  = ""

        self._counts = {
            "sessions":       0,   # Live sessions opened since boot
            "reconnects":     0,   # of those, the ones that followed a drop
            "phone_connects": 0,   # successful phone pairings/logins
            "errors":         0,
        }

        # Link history, maintained by note_link.
        self._link_live: bool | None = None
        self._link_since: float | None = None
        self._longest_session = 0.0

        # () -> bool | None. Set by run_headless to `jarvis.session is not None`.
        self._session_probe = None
        # () -> dict. Optional extra facts (wake word state, registry sizes).
        self._extra_probe = None

    # ── wiring ───────────────────────────────────────────────────────────────

    def bind_session_probe(self, fn) -> None:
        self._session_probe = fn

    def bind_extra_probe(self, fn) -> None:
        self._extra_probe = fn

    # ── writes ───────────────────────────────────────────────────────────────

    def set(self, state: State) -> None:
        with self._lock:
            if state is not self._state:
                self._state = state
                self._since = time.time()

    def note_ui_state(self, raw: str) -> None:
        """Fed by HeadlessUI.set_state — i.e. by main.py, unchanged."""
        mapped = _UI_MAP.get(str(raw).upper().strip())
        if mapped is not None:
            self.set(mapped)

    def note_session_opened(self) -> None:
        with self._lock:
            self._counts["sessions"] += 1

    def note_link(self, live: bool) -> None:
        """Fed by a 1 s poll of `jarvis.session is not None` (run_headless).

        Counting Live sessions is what turns "it seems stable" into a number:
        a 24/7 run is judged on how many times the link came back, and on the
        longest stretch it held. Neither is visible from a state that only ever
        reports *now*.

        Polled rather than parsed out of the log, and polled rather than left to
        snapshot(): /status is queried on demand, so a session that opened and
        closed between two queries would leave no trace at all."""
        with self._lock:
            was = self._link_live
            self._link_live = live
            now = time.time()
            if live and not was:
                self._counts["sessions"] += 1
                if was is not None:
                    self._counts["reconnects"] += 1
                self._link_since = now
            elif was and not live:
                held = now - (self._link_since or now)
                if held > self._longest_session:
                    self._longest_session = held
                self._link_since = None

    def note_phone_connected(self) -> None:
        with self._lock:
            self._counts["phone_connects"] += 1
            self._phone_last_seen = time.time()

    def note_phone_stream(self, delta: int) -> None:
        with self._lock:
            self._phone_streams = max(0, self._phone_streams + delta)
            self._phone_last_seen = time.time()

    def note_error(self, message: str) -> None:
        with self._lock:
            self._last_error    = str(message)[:400]
            self._last_error_at = time.time()
            self._counts["errors"] += 1

    def note_pending_confirmation(self, title: str) -> None:
        with self._lock:
            self._pending_confirm = str(title)[:200]

    def set_audio_level(self, level: float) -> None:
        # Deliberately unlocked: a float store is atomic under the GIL and this
        # is called for every audio block. Cosmetic value, never a decision.
        self._audio_level = float(level)

    def log(self, line: str) -> None:
        """Every line main.py would have written to the HUD.

        The NET:/ERR: prefixes are MARK LIII's own convention (see write_log
        callers in main.py); reading them here costs nothing and gives /status
        a `last_error` without inventing a parallel error channel."""
        text = str(line)
        entry = {"ts": time.time(), "text": text[:500]}
        with self._lock:
            self._log.append(entry)
        head = text[:4].upper()
        if head.startswith("ERR:") or head.startswith("NET:"):
            self.note_error(text)

    # ── read ─────────────────────────────────────────────────────────────────

    def snapshot(self, log_lines: int = 20) -> dict:
        live = None
        if self._session_probe is not None:
            try:
                live = bool(self._session_probe())
            except Exception:
                live = None

        with self._lock:
            state   = self._state
            since   = self._since
            counts  = dict(self._counts)
            log     = list(self._log)[-log_lines:] if log_lines else []
            err     = self._last_error
            err_at  = self._last_error_at
            streams = self._phone_streams
            seen    = self._phone_last_seen
            confirm = self._pending_confirm

        # The UI stream can be a few hundred milliseconds stale across a
        # teardown. The session object is the truth; report accordingly.
        if live is False and state in _NEEDS_SESSION:
            state = State.RECONNECTING if counts["sessions"] else State.CONNECTING

        now = time.time()
        held = (now - self._link_since) if self._link_since else 0.0
        out = {
            "label":        self._label,
            "state":        state.value,
            "state_since":  round(now - since, 1),
            "uptime_s":     round(now - self.started_at, 1),
            "session_live": live,
            "session_held_s": round(held, 1),
            "longest_session_s": round(max(self._longest_session, held), 1),
            "counts":       counts,
            "audio_level":  round(self._audio_level, 3),
            "phone": {
                "audio_streams": streams,
                "last_seen_s":   round(now - seen, 1) if seen else None,
            },
            "last_error":    err or None,
            "last_error_s":  round(now - err_at, 1) if err_at else None,
            "pending_confirmation": confirm or None,
            "log": log,
        }

        if self._extra_probe is not None:
            try:
                extra = self._extra_probe() or {}
                if isinstance(extra, dict):
                    out.update(extra)
            except Exception as e:
                out["extra_error"] = f"{type(e).__name__}: {e}"

        return out
