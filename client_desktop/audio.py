"""
The two ends of the sound: this machine's microphone, and this machine's
speakers.

Neither talks to Gemini. The microphone produces the exact bytes `main.py`'s own
microphone would have produced, and the speaker consumes the exact bytes
`main.py`'s `_play_audio` would have written to a sound card. Everything between
them happens on the VPS.

```
   sounddevice in  ──16 kHz PCM──►  /ws/phone-audio  ──►  MARK LIII  ──►  Gemini
   sounddevice out ◄─announced Hz─  /ws/phone-out    ◄──  MARK LIII  ◄──  Gemini
```

**Why both streams are callback-driven and neither knows about asyncio.**
sounddevice calls back on a real-time thread owned by PortAudio. Anything that
blocks there — a lock held by a slow reader, an `await`, an allocation storm —
is a click in the audio. So the callbacks here do the cheapest possible thing
and hand off; the networking layer does the bridging.
"""

from __future__ import annotations

import collections
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from .protocol import (
    CHANNELS,
    DOWNLINK_SAMPLE_RATE_DEFAULT,
    SAMPLE_WIDTH_BYTES,
    UPLINK_FRAME_SAMPLES,
    UPLINK_SAMPLE_RATE,
)

# Lifted from `main.py` (`_LEVEL_FLOOR` / `_LEVEL_FULL`) rather than invented,
# so the core reacts to a voice here exactly as it does on the desktop HUD and
# on Android. Copied and not imported: importing `main` would construct the
# brain, which is the one thing this client must never do.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL = 2600.0


def pcm_level(samples: np.ndarray | bytes) -> float:
    """Map a block of int16 PCM to a 0.0-1.0 loudness for the core.

    Returns 0.0 on empty or invalid input so it can never raise on the audio
    thread.
    """
    try:
        if isinstance(samples, (bytes, bytearray, memoryview)):
            x = np.frombuffer(samples, dtype=np.int16).astype(np.float32)
        else:
            x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


def list_devices() -> tuple[list[str], list[str]]:
    """(inputs, outputs) by name. Never raises — an audio stack that cannot be
    queried must not stop the client from starting."""
    inputs: list[str] = []
    outputs: list[str] = []
    try:
        for device in sd.query_devices():
            name = str(device.get("name", "")).strip()
            if not name:
                continue
            if device.get("max_input_channels", 0) > 0 and name not in inputs:
                inputs.append(name)
            if device.get("max_output_channels", 0) > 0 and name not in outputs:
                outputs.append(name)
    except Exception:
        pass
    return inputs, outputs


def _resolve(name: str | None, want_input: bool) -> int | str | None:
    """Turn a saved device *name* back into something sounddevice accepts.

    Names are stored rather than indices because indices are not stable: plug in
    a headset and every index after it shifts, so a saved index silently starts
    recording from something else. A name that is no longer present resolves to
    None, which is "the system default" — the right fallback for a laptop whose
    headset is simply not plugged in today.
    """
    if not name:
        return None
    try:
        for index, device in enumerate(sd.query_devices()):
            channels = "max_input_channels" if want_input else "max_output_channels"
            if device.get(channels, 0) > 0 and str(device.get("name", "")) == name:
                return index
    except Exception:
        pass
    return None


class Microphone:
    """16 kHz mono int16, in 1024-sample blocks — `main.py`'s CHUNK_SIZE exactly.

    `on_frame` is called on the audio thread for every block, always, whether or
    not the gate is open: the wake-word detector needs to hear the room in order
    to open it. Deciding what may leave the machine is the caller's job, not
    this class's.
    """

    def __init__(
        self,
        on_frame: Callable[[bytes, float], None],
        device_name: str | None = None,
        on_error: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._on_frame = on_frame
        self._on_error = on_error
        self._device_name = device_name
        self._stream: sd.RawInputStream | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._stream is not None

    def start(self) -> bool:
        with self._lock:
            if self._stream is not None:
                return True
            try:
                self._stream = sd.RawInputStream(
                    samplerate=UPLINK_SAMPLE_RATE,
                    blocksize=UPLINK_FRAME_SAMPLES,
                    dtype="int16",
                    channels=CHANNELS,
                    device=_resolve(self._device_name, want_input=True),
                    callback=self._callback,
                )
                self._stream.start()
                return True
            except Exception as exc:
                self._stream = None
                self._on_error(f"microphone unavailable: {exc}")
                return False

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        # Real-time thread. Copy out of PortAudio's buffer (it is reused) and
        # hand over; do nothing else here.
        try:
            data = bytes(indata)
            self._on_frame(data, pcm_level(data))
        except Exception:
            pass


class Speaker:
    """JARVIS's voice, played at whatever rate the server announced.

    **Built from the announced rate, never from 24000.** The value comes from
    `RECEIVE_SAMPLE_RATE` in `main.py` and arrives in the first text frame of
    `/ws/phone-out`; hardcoding it means wrong-pitch audio the day it changes.
    `open(rate)` is therefore called after that frame, and re-called if a
    reconnect announces something different.

    **Why a ring buffer and a pull callback** rather than blocking writes: audio
    arrives in bursts far faster than real time — a measured 1.52 s of speech
    delivered in 0.41 s — so something has to hold it. A blocking `write()`
    would hold it inside the socket reader instead, which stalls the event loop
    that also has to notice an interrupt.
    """

    #: ~4 s at 24 kHz. Generous, because Gemini outruns real time by a wide
    #: margin and the alternative to buffering a burst is dropping the end of a
    #: sentence. Past this the oldest audio goes: a gap is recoverable, a
    #: conversation five seconds behind is not.
    MAX_BUFFER_BYTES = 24_000 * SAMPLE_WIDTH_BYTES * 4

    def __init__(
        self,
        device_name: str | None = None,
        on_level: Callable[[float], None] = lambda _: None,
        on_played: Callable[[int], None] = lambda _: None,
        on_error: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._device_name = device_name
        self._on_level = on_level
        self._on_played = on_played
        self._on_error = on_error

        self._stream: sd.RawOutputStream | None = None
        self._rate = 0
        self._buffer = collections.deque()  # type: collections.deque[bytes]
        self._buffered_bytes = 0
        self._lock = threading.Lock()

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def running(self) -> bool:
        return self._stream is not None

    def open(self, rate: int = DOWNLINK_SAMPLE_RATE_DEFAULT) -> bool:
        """Open (or re-open) the output at `rate`. Idempotent for the same rate."""
        if self._stream is not None and rate == self._rate:
            return True
        self.close()
        try:
            self._stream = sd.RawOutputStream(
                samplerate=rate,
                dtype="int16",
                channels=CHANNELS,
                device=_resolve(self._device_name, want_input=False),
                callback=self._callback,
            )
            self._stream.start()
            self._rate = rate
            return True
        except Exception as exc:
            self._stream = None
            self._rate = 0
            self._on_error(f"speaker unavailable: {exc}")
            return False

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.flush()

    def write(self, pcm: bytes) -> None:
        """Queue audio. Drops the *oldest* when full, never the newest."""
        if not pcm:
            return
        with self._lock:
            self._buffer.append(pcm)
            self._buffered_bytes += len(pcm)
            while self._buffered_bytes > self.MAX_BUFFER_BYTES and self._buffer:
                dropped = self._buffer.popleft()
                self._buffered_bytes -= len(dropped)

    def flush(self) -> None:
        """Drop everything not yet played.

        Called on interrupt, and it is not optional. The server drops what it
        has queued for us, but it cannot reach into this process: whatever has
        already arrived would otherwise go on being spoken for up to half a
        minute, and the INTERRUPT button would look broken.
        """
        with self._lock:
            self._buffer.clear()
            self._buffered_bytes = 0
        self._on_level(0.0)

    @property
    def buffered_bytes(self) -> int:
        return self._buffered_bytes

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        wanted = frames * CHANNELS * SAMPLE_WIDTH_BYTES
        chunk = bytearray()
        with self._lock:
            while self._buffer and len(chunk) < wanted:
                head = self._buffer[0]
                need = wanted - len(chunk)
                if len(head) <= need:
                    chunk += head
                    self._buffer.popleft()
                    self._buffered_bytes -= len(head)
                else:
                    chunk += head[:need]
                    self._buffer[0] = head[need:]
                    self._buffered_bytes -= need

        # Counted before the padding, and that is the point: `bytes_played`
        # exists to be compared against `bytes_received`, and a callback that
        # emitted mostly silence must not claim to have played a full block.
        # Counting `wanted` makes PLAYED exceed RX, which is impossible and
        # destroys the one diagnostic the two numbers exist for.
        real = len(chunk)

        if real < wanted:
            # Underrun: silence rather than whatever was in the buffer last
            # time, which is a buzz.
            chunk += b"\x00" * (wanted - real)

        outdata[:wanted] = bytes(chunk)

        # The level is measured here, on the way out, rather than on arrival.
        # Audio arrives in bursts far ahead of the voice, so a level taken from
        # the socket would spike before JARVIS spoke and flatline while it still
        # was.
        try:
            self._on_level(pcm_level(bytes(chunk)))
            if real:
                self._on_played(real)
        except Exception:
            pass
