"""
What the client knows, in one place.

A port of `client-android/.../JarvisState.kt`, with one deliberate difference:
Android exposes a `StateFlow` and Compose recomposes on every emission. Qt has
no equivalent, and wiring a signal to every update here would be wrong anyway —
the microphone writes a level fifteen times a second and the byte counters far
more often than that. A signal per write would repaint the panel hundreds of
times a second to move a circle by a pixel.

So this module splits the two things a UI actually needs:

```
  snapshot()        pulled, ~60 fps, by whatever is drawing   (levels, states)
  on_event(...)     pushed, once each, for things that happen (messages, errors)
```

The core widget already runs a paint timer — animation needs one regardless —
so it reads the latest snapshot when it repaints and costs nothing in between.
Discrete events are rare and genuinely need to wake someone up.

**The one rule, unchanged from Android: the network layer writes, the UI reads.**
The UI asks for changes by calling the client, never by editing a snapshot.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable

MAX_MESSAGES = 60
MAX_LOG_LINES = 200


class LinkState(Enum):
    """Transport. What this client can or cannot reach."""

    DISCONNECTED = "OFFLINE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class AssistantState(Enum):
    """What MARK LIII says it is doing, straight from its own state stream."""

    UNKNOWN = "UNKNOWN"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    SLEEPING = "SLEEPING"


@dataclass(frozen=True)
class Message:
    """One side of one turn.

    `at` is 0.0 when the server did not date it. The wire format is an ISO-8601
    *string* (`datetime.now().isoformat()` in main.py), not a number — parsed
    once on arrival so the history does not re-parse a date on every repaint.

    `title` is set only for `content` events: the structured panel the desktop
    HUD shows under the core (a search result, a list, a file summary).
    """

    from_jarvis: bool
    text: str
    at: float = 0.0
    title: str | None = None


@dataclass(frozen=True)
class Snapshot:
    link: LinkState = LinkState.DISCONNECTED
    assistant: AssistantState = AssistantState.UNKNOWN

    #: True while audio is allowed to leave this machine.
    gate_open: bool = False
    #: True while the uplink socket is open (the server believes a mic is live).
    mic_open: bool = False

    # ── counters, for the debug window ───────────────────────────────────────
    bytes_sent: int = 0
    bytes_received: int = 0
    #: Kept separate from `bytes_received` on purpose, and it is the whole
    #: diagnostic: received climbing while played stays flat means the socket is
    #: fine and the output stream is not, which is a completely different fault
    #: from nothing arriving at all.
    bytes_played: int = 0
    frames_sent: int = 0
    frames_received: int = 0
    frames_played: int = 0
    frames_dropped: int = 0

    #: Sample rate the server announced on /ws/phone-out. 0 until it does.
    downlink_rate: int = 0

    #: Microphone level 0..1.
    mic_level: float = 0.0
    #: Loudness of JARVIS's own voice 0..1, taken from the playback loop and not
    #: from the socket: audio arrives in bursts far faster than real time, so a
    #: level measured on arrival would run ahead of the voice and then flatline
    #: while JARVIS was still speaking.
    speaker_level: float = 0.0

    wake_word_name: str = ""
    wake_word_score: float = 0.0
    #: `time.monotonic()` of the last wake. Drives the acknowledgement ring.
    woke_at: float = 0.0

    attempt: int = 0
    next_retry_seconds: int = 0
    last_error: str | None = None

    messages: tuple[Message, ...] = field(default_factory=tuple)
    log: tuple[str, ...] = field(default_factory=tuple)

    # ── the pending irreversible action, if any ──────────────────────────────
    #
    # Three fields on the existing snapshot rather than a second state machine:
    # a confirmation is something JARVIS is *doing*, concurrent with LISTENING
    # or SPEAKING, not a mode the client enters.
    confirmation_id: str | None = None
    confirmation_title: str = ""
    confirmation_detail: str = ""
    #: `time.monotonic()` when the pending confirmation dies, 0 if none.
    #: monotonic, not wall time: an NTP correction must not make a 90-second
    #: countdown jump.
    confirmation_deadline: float = 0.0

    @property
    def connected(self) -> bool:
        return self.link is LinkState.CONNECTED

    @property
    def awaiting_confirmation(self) -> bool:
        return self.confirmation_id is not None

    @property
    def display_state(self) -> str:
        """The single word the panel puts under the core.

        The link comes first: what MARK LIII last said it was doing is not
        interesting while this machine cannot reach it. That ordering is what
        collapses two enums into the eight states the UI names.
        """
        if self.link is not LinkState.CONNECTED:
            return self.link.value
        if self.assistant in (
            AssistantState.LISTENING,
            AssistantState.THINKING,
            AssistantState.SPEAKING,
        ):
            return self.assistant.value
        if self.assistant is AssistantState.SLEEPING:
            return "SLEEPING"
        return LinkState.CONNECTED.value

    @property
    def last_spoken_line(self) -> str | None:
        """The line the panel shows under the core, or None while JARVIS has
        said nothing yet. Only JARVIS's own words: showing users their own
        sentence back is the one thing they already know."""
        for message in reversed(self.messages):
            if message.from_jarvis:
                return message.text
        return None


class JarvisStore:
    """The snapshot, and the only thing allowed to replace it.

    Every mutator takes the lock, builds a new frozen snapshot and publishes it.
    Readers get whatever was last published without blocking on a writer, which
    is what lets a 60 fps paint timer coexist with a microphone callback.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        self._listeners: list[Callable[[str, object], None]] = []

    # ── reading ──────────────────────────────────────────────────────────────

    def snapshot(self) -> Snapshot:
        # No lock: rebinding a name is atomic under the GIL and the object is
        # frozen, so a reader either sees the old snapshot whole or the new one
        # whole. Taking the lock here would put the paint timer behind the
        # microphone thread for no gain in correctness.
        return self._snapshot

    # ── discrete events, for whoever needs waking ────────────────────────────

    def subscribe(self, listener: Callable[[str, object], None]) -> None:
        """Register a callback for things that *happen*, not for every change.

        Called from whichever thread caused the event — a Qt listener must
        marshal to the GUI thread itself, which a queued signal does for free.
        """
        with self._lock:
            self._listeners.append(listener)

    def _emit(self, kind: str, payload: object = None) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                # A broken listener must never take down the network thread that
                # happened to be carrying the event.
                pass

    # ── writing ──────────────────────────────────────────────────────────────

    def _update(self, **changes: object) -> Snapshot:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)  # type: ignore[arg-type]
            return self._snapshot

    def set_link(self, link: LinkState, error: str | None = None) -> None:
        with self._lock:
            current = self._snapshot
            self._snapshot = replace(
                current,
                link=link,
                last_error=error if error is not None
                else (current.last_error if link is LinkState.ERROR else None),
            )
        self._emit("link", link)

    def set_assistant(self, state: AssistantState) -> None:
        self._update(assistant=state)
        self._emit("assistant", state)

    def set_mic_open(self, open_: bool) -> None:
        self._update(mic_open=open_, **({} if open_ else {"mic_level": 0.0}))

    def set_gate_open(self, open_: bool) -> None:
        """Stamp `woke_at` only on the rising edge.

        The gate re-opens on every interaction while the window is alive, and
        flashing the core each time would turn an acknowledgement into a
        flicker.
        """
        with self._lock:
            current = self._snapshot
            rising = open_ and not current.gate_open
            self._snapshot = replace(
                current,
                gate_open=open_,
                woke_at=time.monotonic() if rising else current.woke_at,
            )
        if open_:
            self._emit("gate_open", None)

    def set_retry(self, attempt: int, seconds: int) -> None:
        self._update(attempt=attempt, next_retry_seconds=seconds)

    def set_downlink_rate(self, rate: int) -> None:
        self._update(downlink_rate=rate)

    def set_mic_level(self, level: float) -> None:
        self._update(mic_level=level)

    def set_speaker_level(self, level: float) -> None:
        self._update(speaker_level=level)

    def set_wake_word(self, name: str) -> None:
        self._update(wake_word_name=name)

    def set_wake_score(self, score: float) -> None:
        self._update(wake_word_score=score)

    def set_error(self, message: str | None) -> None:
        self._update(last_error=message)
        if message:
            self._emit("error", message)

    def add_message(self, message: Message) -> None:
        with self._lock:
            current = self._snapshot
            self._snapshot = replace(
                current,
                messages=(current.messages + (message,))[-MAX_MESSAGES:],
            )
        self._emit("message", message)

    def log(self, line: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')}  {line}"
        with self._lock:
            current = self._snapshot
            self._snapshot = replace(
                current, log=(current.log + (stamped,))[-MAX_LOG_LINES:]
            )
        self._emit("log", stamped)

    def set_avatar_intent(self, directive: dict, *, age: float = 0.0) -> None:
        """JARVIS chose a face for what he is saying. An event, never state.

        Emitted as `{"directive": ..., "age": ...}` — the same envelope the
        wire uses — because that is the shape `avatar_view.set_intent_json`
        reads. It used to emit the bare directive, and the widget looked for a
        `directive` key inside it: every intent JARVIS sent was dropped on the
        last hop, silently, with each half tested against itself.

        `age` is how old the decision already was on arrival. The Director
        counts the intent's lifetime from the moment JARVIS decided, not from
        the moment the packet landed — otherwise a directive delayed by twenty
        seconds would live for forty-five.

        Deliberately absent from `Snapshot`. A directive has a lifetime of its
        own — `presence.director.INTENT_TTL_S` — and the object that owns that
        lifetime is the `Director` inside the avatar widget. Putting it in the
        snapshot would create a second, slower copy of the same truth: one that
        expires on the paint timer, contradicts the first for a frame or two,
        and has to be cleared by somebody.

        Nothing here inspects the payload. The client's own `presence` decides
        what it means, and a client built without a body ignores the event —
        which is the same answer as a client that has never heard of it.
        """
        self._emit("avatar", {"directive": directive, "age": max(0.0, float(age))})

    # ── counters ─────────────────────────────────────────────────────────────

    def count_sent(self, byte_count: int) -> None:
        with self._lock:
            c = self._snapshot
            self._snapshot = replace(
                c, bytes_sent=c.bytes_sent + byte_count, frames_sent=c.frames_sent + 1
            )

    def count_received(self, byte_count: int) -> None:
        with self._lock:
            c = self._snapshot
            self._snapshot = replace(
                c,
                bytes_received=c.bytes_received + byte_count,
                frames_received=c.frames_received + 1,
            )

    def count_played(self, byte_count: int) -> None:
        with self._lock:
            c = self._snapshot
            self._snapshot = replace(
                c,
                bytes_played=c.bytes_played + byte_count,
                frames_played=c.frames_played + 1,
            )

    def count_dropped(self) -> None:
        with self._lock:
            c = self._snapshot
            self._snapshot = replace(c, frames_dropped=c.frames_dropped + 1)

    # ── the confirmation gate ────────────────────────────────────────────────

    def set_confirmation(
        self, cid: str, title: str, detail: str, timeout_seconds: int
    ) -> None:
        """A `confirm` event arrived. Replaces any banner already up: the server
        keeps exactly one pending request, so showing two would be a lie."""
        self._update(
            confirmation_id=cid,
            confirmation_title=title,
            confirmation_detail=detail,
            # A server that sends no timeout gets no countdown rather than a
            # guessed one: an invented deadline is worse than none.
            confirmation_deadline=(
                time.monotonic() + timeout_seconds if timeout_seconds > 0 else 0.0
            ),
        )
        self._emit("confirm", (cid, title, detail))

    def clear_confirmation(self) -> None:
        """The request is over — answered here, answered elsewhere, or expired.
        Clearing is all this does; nothing is executed or cancelled locally."""
        self._update(
            confirmation_id=None,
            confirmation_title="",
            confirmation_detail="",
            confirmation_deadline=0.0,
        )
        self._emit("confirm_hide", None)

    def reset_session_counters(self) -> None:
        """Called when the client stops. A stopped client owns nothing any more:
        leaving the byte counters up while the link shows OFFLINE reads as "it is
        still doing something", which is exactly the confusion the debug window
        exists to remove."""
        with self._lock:
            c = self._snapshot
            self._snapshot = Snapshot(
                log=c.log, messages=c.messages, last_error=c.last_error,
                wake_word_name=c.wake_word_name,
            )
