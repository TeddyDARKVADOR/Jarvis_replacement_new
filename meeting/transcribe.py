"""
meeting/transcribe.py — les morceaux de son deviennent des lignes datées.

faster-whisper, sur ce poste (rien ne sort pendant la réunion), avec le filtre
Silero (vad_filter) qui écarte le bruit et le silence avant de décoder. Le
niveau est normalisé d'abord : le son du poste suit le volume de Windows
(capture.py). Un fil unique décode les deux flux dans l'ordre d'arrivée ; s'il
prend plus de 5 minutes de retard, il passe à un modèle plus petit.

L'écho : sans casque, le micro entend les haut-parleurs. Une ligne « moi »
presque identique (≥ 0,8) à une ligne « participants » à moins de 8 s est
retirée — c'était l'écho, pas moi.
"""
from __future__ import annotations

import difflib
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

NO_SPEECH_MAX = 0.6
BACKLOG_DOWNGRADE_S = 300.0
ECHO_RATIO = 0.8
ECHO_WINDOW_S = 8.0
_HALLUCINATIONS = re.compile(
    r"amara\.org|sous-titr|merci d'avoir regard|abonnez-vous|thanks for watching|subtitles by|transcribed by",
    re.IGNORECASE)
SMALLER = {"large-v3": "medium", "medium": "small", "small": "base", "base": "tiny"}


@dataclass
class Line:
    t: float          # secondes depuis le début de la réunion
    speaker: str      # « moi » ou « participants »
    text: str

    def render(self) -> str:
        m, s = divmod(int(self.t), 60)
        h, m = divmod(m, 60)
        who = "Moi" if self.speaker == "moi" else "Participants"
        return f"[{h:02d}:{m:02d}:{s:02d}] {who} : {self.text}"


MIC_MAX_GAIN = 4.0
MIN_LOGPROB = -1.0
MAX_COMPRESSION = 2.4


def normalize(samples: np.ndarray, stream: str = "participants", target: float = 0.5) -> np.ndarray:
    """Le son du poste est numérique (aucun bruit de fond) mais suit le volume de Windows : on le remonte
    entièrement. Le micro, lui, porte le bruit de la pièce : le remonter sans limite fait d'un souffle une
    « voix » que Whisper transcrit en phrases inventées (mesuré le 2026-10-16). Gain du micro plafonné."""
    peak = float(np.abs(samples).max(initial=0.0))
    if peak < 1e-5:
        return samples
    gain = target / peak
    if stream == "moi":
        gain = min(gain, MIC_MAX_GAIN)
    return (samples * gain).astype(np.float32)


def confident(seg) -> bool:
    """Les garde-fous habituels contre les hallucinations de Whisper : parole probable, décodage sûr,
    texte qui ne tourne pas en boucle."""
    return (seg.no_speech_prob < NO_SPEECH_MAX and getattr(seg, "avg_logprob", 0.0) >= MIN_LOGPROB
            and getattr(seg, "compression_ratio", 1.0) <= MAX_COMPRESSION)


def _fold(text: str) -> str:
    return re.sub(r"[^\w ]", "", text.lower()).strip()


def drop_echo(lines: list[Line]) -> list[Line]:
    """Retire les lignes « moi » qui répètent une ligne « participants » proche. Pur, testable."""
    others = [x for x in lines if x.speaker == "participants"]
    out = []
    for x in lines:
        if x.speaker == "moi" and any(
                abs(o.t - x.t) <= ECHO_WINDOW_S
                and difflib.SequenceMatcher(None, _fold(o.text), _fold(x.text)).ratio() >= ECHO_RATIO
                for o in others):
            continue
        out.append(x)
    return out


class Transcriber:
    """Un fil qui décode ; `lines` grandit pendant la réunion."""

    def __init__(self, model: str = "small", language: str | None = None, log: Callable[[str], None] = print,
                 whisper_factory: Callable[[str], object] | None = None):
        self.model_name, self.language, self.log = model, language, log
        self._factory = whisper_factory or _load_whisper
        self._model = None
        self.lines: list[Line] = []
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.decoded_s = 0.0
        self.error = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="meeting-whisper", daemon=True)
        self._thread.start()

    def submit(self, stream: str, t0: float, samples: np.ndarray) -> None:
        self._q.put((stream, t0, samples, time.monotonic()))

    def backlog_s(self) -> float:
        return sum(len(item[2]) for item in list(self._q.queue) if item is not None) / 16_000   # None : la fin

    def finish(self, timeout: float = 1800) -> list[Line]:
        """Attend la fin du décodage (ce qui reste dans la file), puis rend la transcription sans écho."""
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=timeout)
        with self._lock:
            return drop_echo(sorted(self.lines, key=lambda x: x.t))

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            stream, t0, samples, _queued = item
            try:
                if self._model is None or (self.backlog_s() > BACKLOG_DOWNGRADE_S and self.model_name in SMALLER):
                    if self._model is not None:
                        self.model_name = SMALLER[self.model_name]
                        self.log(f"SYS: réunion — transcription en retard, modèle plus léger : {self.model_name}")
                    self._model = self._factory(self.model_name)
                segments, _ = self._model.transcribe(normalize(samples, stream), language=self.language, beam_size=1,
                                                     vad_filter=True, condition_on_previous_text=False)
                for s in segments:
                    text = s.text.strip()
                    if text and confident(s) and not _HALLUCINATIONS.search(text):
                        with self._lock:
                            self.lines.append(Line(t0 + float(s.start), stream, text))
                self.decoded_s += len(samples) / 16_000
            except Exception as e:     # un morceau perdu ne doit pas arrêter la réunion
                self.error = f"{type(e).__name__}: {e}"[:200]
                self.log(f"SYS: réunion — un morceau n'a pas été transcrit : {self.error}")


def _load_whisper(name: str):
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            model = WhisperModel(name, device="cuda", compute_type="float16")
            segments, _ = model.transcribe(np.zeros(16_000, dtype=np.float32), beam_size=1)
            list(segments)                     # le décodage est paresseux : cuDNN absent casse ici
            return model
    except Exception:
        pass
    return WhisperModel(name, device="cpu", compute_type="int8")
