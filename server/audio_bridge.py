"""
server/audio_bridge.py — a sound card made of network sockets.

THE PROBLEM
    A VPS has no microphone and no speaker. main.py opens both inside its
    session TaskGroup:

        _listen_audio  → sd.InputStream(...)        (mic → Gemini)
        _play_audio    → sd.RawOutputStream(...)    (Gemini → speaker)

    Neither can be skipped without editing main.py, and if either raises, the
    TaskGroup unwinds and the reconnect loop spins forever.

THE ANSWER
    main.py does `import sounddevice as sd` at module level, so a stand-in
    installed in sys.modules BEFORE that import is what it gets. This module
    builds that stand-in:

        InputStream      → opens, never fires its callback.
                           The server has no ears of its own; the phone is the
                           microphone, and its PCM already reaches Gemini
                           through dashboard's /ws/phone-audio → the existing
                           _relay_phone_audio task. Nothing to add.

        RawOutputStream  → every stream.write(pcm) is published to whoever is
                           listening on /ws/phone-out.
                           This single line is the whole downlink: JARVIS's
                           voice, which has always gone to the desktop
                           speakers, now goes to the phone instead — and
                           _play_audio is untouched, still batching ~200 ms
                           and still honouring interrupt().

    Format is whatever main.py opened the stream at: 24 kHz, mono, int16, the
    RECEIVE_SAMPLE_RATE constant. It is forwarded verbatim — no resampling, no
    codec, nothing to get out of sync with. Android's AudioTrack takes exactly
    this.

WHAT THIS IS NOT
    Not a mixer, not an audio engine. Bytes in, bytes out, with one deliberate
    policy decision: when a subscriber cannot keep up, the OLDEST frames are
    dropped, never the newest. Dropping the newest would keep every byte and
    grow the delay without bound — a conversation five seconds behind is worse
    than one with a gap in it.
"""
from __future__ import annotations

import asyncio
import threading
import types

# ~200 ms per write from _play_audio's batching.
#
# 200 slots ≈ 40 s, not the 25 (five seconds) this started with. Measured
# against a live session: _play_audio delivered 1.52 s of speech in 0.41 s of
# wall time, because Gemini generates faster than real time and nothing in the
# path paces it until the client's speaker does. Five seconds of slack is
# therefore not "a slow listener" — it is a normal answer of any length, and at
# 25 slots the drop-oldest policy would cut the middle out of the sentence.
#
# The bytes are already in RAM; holding more references to them costs nothing
# worth counting.
_QUEUE_SLOTS = 200


class _Subscriber:
    """One connected listener (one /ws/phone-out socket)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, slots: int = _QUEUE_SLOTS):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=slots)
        self.dropped = 0


class AudioHub:
    """Fan-out from the (threaded) audio writer to the (async) sockets.

    publish() is called from the thread asyncio.to_thread gave _play_audio, so
    every hand-off to the loop goes through call_soon_threadsafe. Getting that
    wrong does not fail loudly — it corrupts the queue and drops audio at
    random — so it is done in exactly one place, here."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: set[_Subscriber] = set()
        self.frames_published = 0
        self.bytes_published  = 0

    # ── subscriber side (async) ──────────────────────────────────────────────

    def subscribe(self) -> _Subscriber:
        sub = _Subscriber(asyncio.get_running_loop())
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def listeners(self) -> int:
        with self._lock:
            return len(self._subs)

    # ── producer side (any thread) ───────────────────────────────────────────

    def publish(self, data: bytes) -> None:
        if not data:
            return

        # Counted before the subscriber check on purpose: these say how much
        # JARVIS actually said, not how much got delivered. "speaking, 0
        # listeners" in /status is precisely the symptom worth being able to
        # see — it is what a phone that thinks it is connected looks like.
        self.frames_published += 1
        self.bytes_published  += len(data)

        with self._lock:
            subs = list(self._subs)
        if not subs:
            return   # nobody listening: the audio is simply discarded

        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(self._offer, sub, data)
            except RuntimeError:
                # Loop closed between the snapshot and now — the socket is on
                # its way out anyway.
                self.unsubscribe(sub)

    @staticmethod
    def _offer(sub: _Subscriber, data: bytes) -> None:
        """Runs on the event loop. Drop-oldest, see the module docstring."""
        q = sub.queue
        if q.full():
            try:
                q.get_nowait()
                sub.dropped += 1
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            sub.dropped += 1


# ── the stand-in module ──────────────────────────────────────────────────────

def build_module(hub: AudioHub, log=print) -> types.ModuleType:
    """Return an object that can stand in for `sounddevice`.

    The surface is exactly what this repository calls, no more:
      main.py            → InputStream, RawOutputStream
      core/audio_devices → InputStream, RawOutputStream, query_devices,
                           query_hostapis
      core/tts.py        → play, wait, stop   (not on main.py's import path)
    """

    class PortAudioError(Exception):
        pass

    class _NullInputStream:
        """Opens, reports itself active, and delivers nothing.

        The callback is stored and never called on purpose: server-side audio
        capture is the one thing a headless MARK LIII must NOT do. The phone
        holds the microphone."""

        def __init__(self, *_a, callback=None, **kw):
            self._callback   = callback
            self.samplerate  = kw.get("samplerate")
            self.channels    = kw.get("channels", 1)
            self.blocksize   = kw.get("blocksize", 0)
            self.device      = kw.get("device")
            self.active      = False
            self.closed      = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False
            self.closed = True

        # main.py uses `with _mic_stream:` — without these two it raises
        # AttributeError inside the TaskGroup and the session dies at connect.
        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *_exc):
            self.stop()
            self.close()
            return False

    class _PhoneOutputStream:
        """Every write() is one step of JARVIS's voice on its way to the phone."""

        def __init__(self, *_a, **kw):
            self.samplerate = kw.get("samplerate")
            self.channels   = kw.get("channels", 1)
            self.blocksize  = kw.get("blocksize", 0)
            self.device     = kw.get("device")
            self.active     = False
            self.closed     = False

        def start(self):
            self.active = True

        def write(self, data):
            # bytes / bytearray / memoryview all arrive here depending on the
            # caller; normalise once so subscribers never have to care.
            if isinstance(data, memoryview):
                data = data.tobytes()
            elif isinstance(data, bytearray):
                data = bytes(data)
            hub.publish(data)

        def stop(self):
            self.active = False

        def close(self):
            self.active = False
            self.closed = True

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *_exc):
            self.stop()
            self.close()
            return False

    # A headless host truthfully has no audio devices. Returning an empty list
    # makes core/audio_devices list nothing and resolve() answer None for every
    # saved device name — which is the path main.py already takes for "system
    # default", so it opens our stand-ins and carries on.
    def query_devices(*_a, **_kw):
        return []

    def query_hostapis(*_a, **_kw):
        return []

    _warned = {"play": False}

    def play(*_a, **_kw):
        # core/tts.py only — never reached from main.py's Live path. Logged
        # once rather than silently swallowed, so a plugin that tries to speak
        # locally is diagnosable from /status instead of just being inaudible.
        if not _warned["play"]:
            _warned["play"] = True
            log("SYS: local sd.play() ignored — headless server has no speaker "
                "(JARVIS's voice goes to the phone via /ws/phone-out).")

    def wait(*_a, **_kw):
        return None

    def stop(*_a, **_kw):
        return None

    mod = types.ModuleType("sounddevice")
    mod.InputStream     = _NullInputStream
    mod.RawOutputStream = _PhoneOutputStream
    mod.OutputStream    = _PhoneOutputStream
    mod.query_devices   = query_devices
    mod.query_hostapis  = query_hostapis
    mod.play            = play
    mod.wait            = wait
    mod.stop            = stop
    mod.PortAudioError  = PortAudioError
    mod.default         = types.SimpleNamespace(device=(None, None),
                                                samplerate=None, channels=(1, 1))
    mod.__headless__    = True   # so a test can tell the stand-in from the real one
    return mod
