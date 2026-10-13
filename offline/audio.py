"""
offline/audio.py — entendre une phrase entière, et parler sans s'entendre.

Micro : 16 kHz mono int16, trames de 30 ms jugées par webrtcvad (sinon par
l'énergie). Une phrase commence après 3 trames voisées sur les 10 dernières et
finit après 0,8 s de silence, ou au bout de 15 s.

Semi-duplex : pendant que JARVIS parle, le micro est ignoré puis vidé. Moins
fin que l'anti-écho de Mark LV, mais sans réglage et sans risque qu'il se
réponde à lui-même — en mode dégradé, c'est la bonne affaire.

Windows, Linux et macOS : sounddevice (PortAudio) partout.
"""
from __future__ import annotations

import collections
import queue
import threading
import time

import numpy as np

RATE = 16_000
FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2
START_VOICED, START_WINDOW = 3, 10
END_SILENCE_S = 0.8
MAX_UTTERANCE_S = 15.0
MIN_VOICED_S = 0.25           # de voix, pas de silence : un claquement n'est pas une phrase
ENERGY_THRESHOLD = 500           # RMS int16, seulement sans webrtcvad


def resolve_device(name: str | None, kind: str):
    """Un nom de périphérique enregistré → index sounddevice ; None = défaut."""
    if not name:
        return None
    try:
        from core import audio_devices
        return audio_devices.resolve(name, kind)
    except Exception:
        pass
    try:
        import sounddevice as sd
        key = "max_input_channels" if kind == "input" else "max_output_channels"
        for i, d in enumerate(sd.query_devices()):
            if d.get(key, 0) > 0 and name.lower() in d["name"].lower():
                return i
    except Exception:
        pass
    return None


class _Voice:
    """Est-ce une trame de voix ?"""

    def __init__(self):
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(2)
        except Exception:
            self._vad = None

    def __call__(self, frame: bytes) -> bool:
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame, RATE)
            except Exception:
                pass
        s = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(s * s))) > ENERGY_THRESHOLD


class Segmenter:
    """Trames de 30 ms → phrases. Sans E/S : testable avec des octets."""

    def __init__(self, is_voice=None):
        self._is_voice = is_voice or _Voice()
        self.reset()

    def reset(self) -> None:
        self._recent = collections.deque(maxlen=START_WINDOW)
        self._pre = collections.deque(maxlen=START_WINDOW)
        self._buf: list[bytes] | None = None
        self._silence = 0.0
        self._voiced = 0

    def feed(self, frame: bytes) -> bytes | None:
        voiced = self._is_voice(frame)
        if self._buf is None:
            self._recent.append(voiced)
            self._pre.append(frame)
            if sum(self._recent) >= START_VOICED:
                self._buf, self._silence, self._voiced = list(self._pre), 0.0, sum(self._recent)
            return None
        self._buf.append(frame)
        self._voiced += voiced
        self._silence = 0.0 if voiced else self._silence + FRAME_MS / 1000
        length = len(self._buf) * FRAME_MS / 1000
        if self._silence >= END_SILENCE_S or length >= MAX_UTTERANCE_S:
            pcm, voiced_s = b"".join(self._buf), self._voiced * FRAME_MS / 1000
            self.reset()
            return pcm if voiced_s >= MIN_VOICED_S else None
        return None


class Microphone:
    def __init__(self, device=None):
        self.device = device
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=1000)
        self._stream = None
        self.muted = threading.Event()
        self._seg = Segmenter()
        self._rest = b""

    def start(self) -> None:
        import sounddevice as sd
        self._stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                         blocksize=FRAME_BYTES // 2, device=self.device,
                                         callback=self._cb)
        self._stream.start()

    def _cb(self, indata, frames, time_info, status) -> None:      # noqa: ANN001
        if self.muted.is_set():
            return
        try:
            self._q.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def flush(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._seg.reset()
        self._rest = b""

    def next_utterance(self, stop: threading.Event) -> bytes | None:
        while not stop.is_set():
            try:
                chunk = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            data = self._rest + chunk
            while len(data) >= FRAME_BYTES:
                frame, data = data[:FRAME_BYTES], data[FRAME_BYTES:]
                pcm = self._seg.feed(frame)
                if pcm:
                    self._rest = data
                    return pcm
            self._rest = data
        return None

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


def play(pcm: bytes, rate: int, device=None, mic: Microphone | None = None) -> None:
    """Joue un PCM int16 mono et attend la fin ; le micro est sourd pendant ce temps."""
    import sounddevice as sd
    if mic:
        mic.muted.set()
    try:
        sd.play(np.frombuffer(pcm, dtype=np.int16), samplerate=rate, device=device, blocking=True)
        time.sleep(0.25)             # la queue du son quitte la pièce
    finally:
        if mic:
            mic.flush()
            mic.muted.clear()
