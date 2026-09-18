"""
"Hey Jarvis", and the gate it opens.

This module adds no detection of its own. `core/wake_word.py` already implements
exactly this, is already the engine the desktop assistant uses, and is already
the same `hey_jarvis` openWakeWord model the Android client proved numerically
identical to the reference implementation. It is imported, never modified —
`server/selftest.py` fails on a dirty `core/`.

**What is new here is the gate.** Detection is only half of it: the useful
question on a work machine is not "was the phrase said" but "may this microphone
leave the building right now". Those are deliberately separate:

```
   microphone  ──every frame──►  wake word          (local, always)
                                      │
                                   detected
                                      ▼
   microphone  ──while open───►  the gate  ──►  /ws/phone-audio   (the VPS)
```

The gate shuts on a timer. A gate that stayed open until something closed it is
an open microphone on a work laptop the first time a callback is missed, and the
failure is silent — which is the one kind of microphone bug nobody notices.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

# `python -m client_desktop` from the repository root puts the root on sys.path
# for free. A shortcut fired at login does not, and that is the case that
# matters: this has to come up with Windows without a shell to be started from.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def engine_available() -> bool:
    """True if openWakeWord is installed *and* its models are on disk."""
    try:
        from core.wake_word import is_ready

        return bool(is_ready())
    except Exception:
        return False


def engine_installed() -> bool:
    try:
        from core.wake_word import is_installed

        return bool(is_installed())
    except Exception:
        return False


def install(logger: Callable[[str], None] = print) -> tuple[bool, str]:
    """One-click setup, delegated whole to `core/wake_word.py`.

    Deliberately not called automatically: it pip-installs a package and
    downloads a model. A client that did that on its own at first login would be
    a program that installs software on a work machine without being asked.
    """
    try:
        from core.wake_word import install_and_download

        return install_and_download(logger)
    except Exception as exc:
        return False, f"wake word setup unavailable: {exc}"


class Gate:
    """May audio leave this machine, and for how much longer.

    Every method is safe to call from the audio thread.
    """

    def __init__(
        self,
        seconds: float,
        on_change: Callable[[bool], None] = lambda _: None,
    ) -> None:
        self._seconds = seconds
        self._on_change = on_change
        self._open_until = 0.0
        #: Set by the user, not by a timer — a held gate never expires.
        self._held = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._held or time.monotonic() < self._open_until

    @property
    def seconds_left(self) -> float:
        with self._lock:
            if self._held:
                return float("inf")
            return max(0.0, self._open_until - time.monotonic())

    @property
    def held(self) -> bool:
        return self._held

    def open(self, seconds: float | None = None) -> None:
        """Open for a while. Called by the wake word and by the panel's button."""
        was = self.is_open
        with self._lock:
            self._open_until = time.monotonic() + (seconds or self._seconds)
        if not was:
            self._on_change(True)

    def touch(self) -> None:
        """Extend an already-open gate. A no-op when shut: activity must not be
        able to open a gate that the wake word never opened."""
        with self._lock:
            if self._held or time.monotonic() < self._open_until:
                self._open_until = time.monotonic() + self._seconds

    def hold(self, held: bool) -> None:
        """Latch the gate open (or release it) — the panel's mic toggle."""
        was = self.is_open
        with self._lock:
            self._held = held
            if not held:
                self._open_until = 0.0
        now = self.is_open
        if now != was:
            self._on_change(now)

    def close(self) -> None:
        was = self.is_open
        with self._lock:
            self._held = False
            self._open_until = 0.0
        if was:
            self._on_change(False)

    def tick(self) -> None:
        """Notice an expiry. Called from the UI timer, because nothing else will:
        the gate closes by a clock running out, and a clock running out is not an
        event anyone delivers."""
        with self._lock:
            expired = (
                not self._held
                and self._open_until > 0.0
                and time.monotonic() >= self._open_until
            )
            if expired:
                self._open_until = 0.0
        if expired:
            self._on_change(False)


class WakeWord:
    """`core.wake_word.WakeWordDetector`, started only when it can actually run.

    One thing this cannot report is a per-frame score. `core/wake_word.py`
    invokes `on_detect()` and keeps the score to itself, and that file is not
    ours to change. The debug window therefore shows the engine name and the
    time of the last detection rather than a live meter — which is the honest
    display of what is actually known.
    """

    NAME = "openWakeWord / hey_jarvis"

    def __init__(
        self,
        on_detect: Callable[[], None],
        threshold: float = 0.5,
        logger: Callable[[str], None] = print,
    ) -> None:
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger = logger
        self._detector = None
        self._last_detection = 0.0

    @property
    def running(self) -> bool:
        return self._detector is not None

    @property
    def last_detection(self) -> float:
        return self._last_detection

    def start(self) -> bool:
        if self._detector is not None:
            return True
        if not engine_available():
            self._logger("Wake word: not installed — say it with the button instead.")
            return False
        try:
            from core.wake_word import WakeWordDetector

            detector = WakeWordDetector(
                on_detect=self._fire,
                threshold=self._threshold,
                logger=self._logger,
            )
            if not detector.start():
                return False
            self._detector = detector
            return True
        except Exception as exc:
            self._logger(f"Wake word: could not start — {exc}")
            return False

    def stop(self) -> None:
        detector, self._detector = self._detector, None
        if detector is not None:
            try:
                detector.stop()
            except Exception:
                pass

    def feed(self, pcm: bytes) -> None:
        """Called on the audio thread. Must stay cheap — `feed` only queues."""
        detector = self._detector
        if detector is None:
            return
        try:
            detector.feed(np.frombuffer(pcm, dtype=np.int16))
        except Exception:
            pass

    def _fire(self) -> None:
        self._last_detection = time.monotonic()
        try:
            self._on_detect()
        except Exception:
            pass
