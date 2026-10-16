"""
meeting/capture.py — le son de la réunion : ce que le poste joue, et le micro.

Deux flux, deux voix : le son du poste (« participants » : tout ce que
l'application de visio fait entendre) et le micro (« moi »). Séparer les deux,
c'est savoir qui parle sans aucune reconnaissance de locuteur.

    cap = Capture(on_chunk=lambda stream, t0, samples: ...)
    cap.start(); …; cap.stop()

soundcard (bastibe/SoundCard) pour les trois systèmes : WASAPI loopback sous
Windows, moniteur PulseAudio/PipeWire sous Linux, CoreAudio sous macOS — où le
son du poste ne se capte pas sans pilote virtuel (BlackHole) : seul le micro
est alors enregistré, et il entend aussi les haut-parleurs.

Mesuré le 2026-10-16 (Windows, carte Cirrus Logic) : le loopback suit le volume
de Windows — haut-parleurs coupés, il ne rend que du silence ; à 2 %, un signal
faible mais transcrit. Le niveau est donc normalisé avant Whisper
(transcribe.py), et un poste muet est signalé au lieu d'être cru silencieux.

Découpage : des morceaux de 9 à 30 s, coupés au passage le plus calme des 3
dernières secondes (une phrase coupée en deux se transcrit mal). Le
périphérique par défaut est relu toutes les 10 s : un casque branché pendant
la réunion est suivi.
"""
from __future__ import annotations

import threading
import time
import warnings
from typing import Callable

import numpy as np

RATE = 16_000
BLOCK_S = 0.5
MIN_CHUNK_S = 12.0
MAX_CHUNK_S = 30.0
CUT_SEARCH_S = 3.0
RECHECK_DEVICE_S = 10.0


def cut_point(blocks: list[np.ndarray]) -> int:
    """L'indice du bloc le plus calme parmi les CUT_SEARCH_S dernières secondes : on coupe après lui. Pur."""
    n = max(1, int(CUT_SEARCH_S / BLOCK_S))
    tail = range(max(0, len(blocks) - n), len(blocks))
    return min(tail, key=lambda i: float(np.sqrt(np.mean(blocks[i] ** 2))) if blocks[i].size else 0.0)


def loopback_of(speaker, microphones):
    """L'entrée qui rend ce que joue `speaker`. Windows : le même id (WASAPI loopback) ; PulseAudio et
    PipeWire : « <sink>.monitor » — get_microphone(spk.id, include_loopback=True) ne le trouve pas
    (mesuré sous Debian 12, PulseAudio 16). Pur, testable."""
    loops = [m for m in microphones if getattr(m, "isloopback", False)]
    for m in loops:
        if m.id == speaker.id or m.id == f"{speaker.id}.monitor":
            return m
    raise LookupError(f"aucune entrée loopback pour « {speaker.name} »")


class Chunker:
    """Blocs de 0,5 s → morceaux de 12 à 30 s, coupés au calme. Sans E/S : testable."""

    def __init__(self, stream: str, on_chunk: Callable[[str, float, np.ndarray], None]):
        self.stream, self.on_chunk = stream, on_chunk
        self.blocks: list[np.ndarray] = []
        self.t0: float | None = None

    def feed(self, block: np.ndarray, t: float) -> None:
        if self.t0 is None:
            self.t0 = t
        self.blocks.append(block)
        dur = len(self.blocks) * BLOCK_S
        if dur >= MAX_CHUNK_S or (dur >= MIN_CHUNK_S and self._quiet(self.blocks[-1])):
            at = cut_point(self.blocks) if dur < MAX_CHUNK_S else len(self.blocks) - 1
            self._emit(at + 1)

    def _quiet(self, block: np.ndarray) -> bool:
        level = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
        peak = max(float(np.sqrt(np.mean(b ** 2))) for b in self.blocks) or 1e-9
        return level < 0.15 * peak

    def _emit(self, n: int) -> None:
        head, self.blocks = self.blocks[:n], self.blocks[n:]
        t0 = self.t0 or 0.0
        self.t0 = t0 + n * BLOCK_S if self.blocks else None
        if head:
            self.on_chunk(self.stream, t0, np.concatenate(head))

    def flush(self) -> None:
        self._emit(len(self.blocks))


UTT_MIN_S = 1.0
UTT_MAX_S = 12.0
UTT_END_BLOCKS = 2            # 1 s de silence clôt une phrase
UTT_FLOOR = 2e-5              # en dessous : silence, quel que soit le niveau de la phrase (son du poste, numérique)
#: Le micro porte le bruit de la pièce (RMS 0,00009 mesuré au repos, la voix 0,01 à 0,1) : un seuil plus haut,
#: sinon le souffle formerait des « phrases » de 12 s que Whisper décoderait pour rien.
UTT_FLOOR_BY_STREAM = {"moi": 1e-3}
#: Le micro en morceaux plus courts : aucun décodage du micro ne doit retenir longtemps une question.
UTT_MAX_BY_STREAM = {"moi": 6.0}


class UtteranceChunker:
    """Blocs → une phrase dès qu'elle finit (mode direct de l'assistant) : la question d'un participant
    arrive à Whisper une seconde après sa fin, pas 30 s plus tard. Le silence seul n'est jamais envoyé.
    Le seuil est relatif au niveau de la phrase en cours : le son du poste suit le volume de Windows
    (capture.py), et un seuil fixe raterait une réunion écoutée bas. Sans E/S : testable."""

    def __init__(self, stream: str, on_chunk: Callable[[str, float, np.ndarray], None]):
        self.stream, self.on_chunk = stream, on_chunk
        self.floor = UTT_FLOOR_BY_STREAM.get(stream, UTT_FLOOR)
        self.max_s = UTT_MAX_BY_STREAM.get(stream, UTT_MAX_S)
        self.blocks: list[np.ndarray] = []
        self.t0: float | None = None
        self.peak = 0.0
        self.quiet_run = 0

    def feed(self, block: np.ndarray, t: float) -> None:
        level = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
        if not self.blocks:
            if level <= self.floor:
                return                                   # silence avant la phrase : rien à garder
            self.t0, self.peak, self.quiet_run = t, level, 0
        self.blocks.append(block)
        self.peak = max(self.peak, level)
        self.quiet_run = self.quiet_run + 1 if level < max(self.floor, 0.12 * self.peak) else 0
        dur = len(self.blocks) * BLOCK_S
        speech = dur - self.quiet_run * BLOCK_S
        if self.quiet_run >= UTT_END_BLOCKS:
            if speech >= UTT_MIN_S:
                self._emit(len(self.blocks) - self.quiet_run + 1)
            else:
                self.blocks, self.t0 = [], None         # un bruit bref : jeté
        elif dur >= self.max_s:
            self._emit(len(self.blocks))

    def _emit(self, n: int) -> None:
        head, t0 = self.blocks[:n], self.t0 or 0.0
        self.blocks, self.t0, self.quiet_run, self.peak = [], None, 0, 0.0
        self.on_chunk(self.stream, t0, np.concatenate(head))

    def flush(self) -> None:
        if self.blocks and (len(self.blocks) - self.quiet_run) * BLOCK_S >= UTT_MIN_S:
            self._emit(len(self.blocks))


class Capture:
    """Les deux flux, chacun dans son fil. `status` dit ce qui est réellement capté. Les flux de `live`
    sont découpés à la phrase (UtteranceChunker) pour l'assistant en direct ; les autres en morceaux."""

    def __init__(self, on_chunk: Callable[[str, float, np.ndarray], None], log: Callable[[str], None] = print,
                 mic: bool = True, system: bool = True, live: tuple[str, ...] = ()):
        self.on_chunk, self.log = on_chunk, log
        self.live = set(live)
        self.want = {"moi": mic, "participants": system}
        self.status: dict[str, str] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.t_start = 0.0

    def start(self) -> None:
        self.t_start = time.monotonic()
        self._stop.clear()
        for stream, on in self.want.items():
            if on:
                t = threading.Thread(target=self._run, args=(stream,), name=f"meeting-{stream}", daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()

    def _device(self, sc, stream: str):
        if stream == "moi":
            return sc.default_microphone()
        return loopback_of(sc.default_speaker(), sc.all_microphones(include_loopback=True))

    def _run(self, stream: str) -> None:
        try:
            import soundcard as sc
            noisy = getattr(sc, "SoundcardRuntimeWarning", None)   # « data discontinuity » : Windows seulement
            if noisy is not None:
                warnings.filterwarnings("ignore", category=noisy)
        except Exception as e:
            self.status[stream] = f"indisponible : soundcard ({type(e).__name__})"
            self.log(f"SYS: réunion — {stream} non capté : {e}")
            return
        chunker = (UtteranceChunker if stream in self.live else Chunker)(stream, self.on_chunk)
        frames = int(RATE * BLOCK_S)
        silent_since = None
        while not self._stop.is_set():
            try:
                dev = self._device(sc, stream)
            except Exception as e:
                self.status[stream] = f"indisponible : {type(e).__name__}"
                if stream == "participants":
                    self.log(f"SYS: réunion — le son du poste ne se capte pas ici ({e}) : micro seul.")
                    return
                self._stop.wait(2)
                continue
            self.status[stream] = f"capté : {dev.name}"
            checked = time.monotonic()
            try:
                with dev.recorder(samplerate=RATE, channels=1, blocksize=frames) as rec:
                    while not self._stop.is_set():
                        block = np.asarray(rec.record(numframes=frames), dtype=np.float32).reshape(-1)
                        chunker.feed(block, time.monotonic() - self.t_start)
                        # Un flux tout à zéro pendant une minute : poste muet, ou mauvais périphérique.
                        if np.abs(block).max(initial=0.0) == 0.0:
                            silent_since = silent_since or time.monotonic()
                            if time.monotonic() - silent_since > 60 and "muet" not in self.status[stream]:
                                self.status[stream] += " — silence total depuis 1 min (son coupé ?)"
                        else:
                            silent_since = None
                        if time.monotonic() - checked > RECHECK_DEVICE_S:
                            checked = time.monotonic()
                            if self._device(sc, stream).id != dev.id:
                                self.log(f"SYS: réunion — {stream} : nouveau périphérique par défaut, je suis.")
                                break
            except Exception as e:
                self.status[stream] = f"interrompu : {type(e).__name__}"
                self._stop.wait(1)
        chunker.flush()
