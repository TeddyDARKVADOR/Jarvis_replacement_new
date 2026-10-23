"""
offline/stt.py — la voix en texte, sur cette machine, avec faster-whisper.

Modèle `small` multilingue : sur le GPU (CUDA, float16) quand ctranslate2 le
voit et que cuBLAS est installé, sinon sur le CPU en int8. Sur ce PC,
cublas64_12.dll manque : le modèle charge sur le GPU et le premier décodage
casse ; on retombe alors sur le CPU au lieu de couper l'écoute.

Le nom de l'assistant est passé en `initial_prompt`. Mesuré le 2026-10-13 sur
trois phrases dites par la voix française de Windows :
    base           « J'arvie, ouvre… », « J'arvie, mais le volume… »   1/3
    base + nom     le nom disparaît ou devient « J'arvis »             1/3
    small          « Jarvie, … » à chaque fois                         0/3
    small + nom    « Matthew, … » à chaque fois                         3/3
`hotwords` faisait pire : le nom sautait de la transcription. Le modèle se télécharge une fois
(~500 Mo pour small) dans le cache Hugging Face, puis plus rien ne sort.
"""
from __future__ import annotations

import re
import threading

import numpy as np

GPU_MODEL = CPU_MODEL = "small"
NO_SPEECH_MAX = 0.6
_HALLUCINATIONS = re.compile(
    r"amara\.org|sous-titr|merci d'avoir regard|abonnez-vous|thanks for watching|"
    r"subtitles by|transcribed by", re.IGNORECASE)
_gpu_broken = False


class Whisper:
    def __init__(self, language: str | None = None, log=print, hotwords: list[str] | None = None):
        self.language = language or None
        # Le nom de l'assistant en amorce : sans lui, « Matthew » devient « Jarvie ».
        self.prompt = ", ".join(dict.fromkeys(h.capitalize() for h in (hotwords or []) if h)) or None
        self._log = log
        self._model = None
        self.device = ""

    def _load(self, device: str):
        from faster_whisper import WhisperModel
        if device == "cuda":
            m = WhisperModel(GPU_MODEL, device="cuda", compute_type="float16")
        else:
            m = WhisperModel(CPU_MODEL, device="cpu", compute_type="int8")
        self._model, self.device = m, device
        self._log(f"SYS: hors-ligne — transcription locale prête ({device}).")

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import ctranslate2
            if not _gpu_broken and ctranslate2.get_cuda_device_count() > 0:
                try:
                    self._load("cuda")
                    return
                except Exception as e:
                    _mark_gpu_broken()
                    self._log(f"SYS: hors-ligne — GPU indisponible pour Whisper ({e}), CPU.")
        except Exception:
            pass
        self._load("cpu")

    def transcribe(self, pcm: bytes, rate: int = 16_000) -> str:
        self.load()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            return self._run(audio)
        except Exception as e:
            if self.device != "cuda":
                raise
            # cuBLAS/cuDNN absents : le modèle charge, le premier décodage casse.
            _mark_gpu_broken()
            self._log(f"SYS: hors-ligne — décodage GPU impossible ({e}), passage au CPU.")
            self._load("cpu")
            return self._run(audio)

    def _run(self, audio) -> str:
        # vad_filter : Silero (livré avec faster-whisper) retire le bruit autour de la
        # phrase avant Whisper. Essai du 2026-10-16 (voix SAPI dans du bruit) : texte
        # un peu plus juste, « Stop. » gardé, silence écarté sans décoder.
        segments, _info = self._model.transcribe(audio, language=self.language, beam_size=1,
                                                 vad_filter=True, condition_on_previous_text=False,
                                                 initial_prompt=self.prompt)
        kept = [s.text.strip() for s in segments
                if s.no_speech_prob < NO_SPEECH_MAX and not _HALLUCINATIONS.search(s.text)]
        return " ".join(kept).strip()


def _mark_gpu_broken() -> None:
    global _gpu_broken
    _gpu_broken = True


_shared: Whisper | None = None
_shared_lock = threading.Lock()


def shared(language: str | None = None, log=print, hotwords: list[str] | None = None) -> Whisper:
    """Le seul Whisper du processus : chargé une fois, réutilisé à chaque panne."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Whisper(language, log=log, hotwords=hotwords)
        else:
            _shared._log = log
        return _shared
