"""
offline/tts.py — le texte en voix, sans réseau, sur les trois systèmes.

  Windows  SAPI (Hortense en français) piloté par COM (pywin32), écrit en WAV.
  Linux    espeak-ng (ou espeak) : `sudo apt install espeak-ng`.
  macOS    say, en WAV 16 bits.

Le WAV est joué par sounddevice : le haut-parleur choisi dans les réglages est
respecté, et le micro peut être rendu sourd pendant ce temps.

POURQUOI PAS pyttsx3
    Essayé d'abord. Son runAndWait() se bloque au deuxième appel sous Windows
    (constaté le 2026-10-13 : la deuxième phrase ne sortait jamais), et
    pyttsx3.init() rend le même moteur, lié au fil du premier appel. SAPI
    directement, dans un fil qui lui est propre, n'a ni l'un ni l'autre.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import wave

SAPI_22K_16BIT_MONO = 22         # SpeechAudioFormatType.SAFT22kHz16BitMono
SSFM_CREATE_FOR_WRITE = 3


class _Sapi:
    """Vit dans le fil de la Voice : COM est initialisé pour ce fil-là."""

    def __init__(self, language: str):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        self._client = win32com.client
        self.voice = win32com.client.Dispatch("SAPI.SpVoice")
        want = {"fr": "french", "en": "english", "de": "german", "es": "spanish", "it": "italian"}.get(language, language)
        for token in self.voice.GetVoices():
            if want in token.GetDescription().lower():
                self.voice.Voice = token
                break
        self.voice.Rate = 1

    def to_wav(self, text: str, path: str) -> None:
        stream = self._client.Dispatch("SAPI.SpFileStream")
        fmt = self._client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = SAPI_22K_16BIT_MONO
        stream.Format = fmt
        stream.Open(path, SSFM_CREATE_FOR_WRITE)
        try:
            self.voice.AudioOutputStream = stream
            self.voice.Speak(text)
        finally:
            stream.Close()


class _Command:
    """espeak-ng / espeak (Linux) ou say (macOS)."""

    def __init__(self, language: str):
        self.language = language
        if sys.platform == "darwin":
            self.argv = ["say", "--data-format=LEI16@22050", "-o"]
        else:
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            if not exe:
                raise RuntimeError("aucune voix locale : installez espeak-ng (sudo apt install espeak-ng)")
            self.argv = [exe, "-v", language, "-s", "165", "-w"]

    def to_wav(self, text: str, path: str) -> None:
        subprocess.run(self.argv + [path, text], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class Voice:
    def __init__(self, language: str | None = None):
        self.language = (language or "fr").lower()[:2]
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.error = ""

    def _loop(self) -> None:
        try:
            engine = _Sapi(self.language) if sys.platform == "win32" else _Command(self.language)
        except Exception as e:
            self.error, engine = f"{type(e).__name__}: {e}", None
        while True:
            text, box, done = self._jobs.get()
            if engine is None:
                box["error"] = self.error or "aucun moteur de synthèse"
                done.set()
                continue
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                engine.to_wav(text, path)
                with wave.open(path, "rb") as w:
                    box.update(pcm=w.readframes(w.getnframes()), rate=w.getframerate(),
                               width=w.getsampwidth(), ch=w.getnchannels())
            except Exception as e:
                box["error"] = f"{type(e).__name__}: {e}"
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
                done.set()

    def synthesize(self, text: str, timeout: float = 30.0) -> tuple[bytes, int]:
        """(PCM int16 mono, fréquence). Lève RuntimeError si aucune voix."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="offline-tts")
            self._thread.start()
        box, done = {}, threading.Event()
        self._jobs.put((text, box, done))
        if not done.wait(timeout):
            raise RuntimeError("synthèse vocale trop lente")
        if "error" in box:
            raise RuntimeError(box["error"])
        pcm = box["pcm"]
        if box["width"] != 2:
            raise RuntimeError(f"format audio inattendu ({box['width'] * 8} bits)")
        if box["ch"] == 2:
            import numpy as np
            pcm = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2).mean(axis=1).astype(np.int16).tobytes()
        return pcm, box["rate"]


_shared: Voice | None = None
_shared_lock = threading.Lock()


def shared(language: str | None = None) -> Voice:
    """La seule Voice du processus (un seul fil de synthèse) ; la langue est
    celle du premier appel."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Voice(language)
        return _shared
