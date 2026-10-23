"""
offline/ — quand Gemini ou Oracle tombe, Matthew continue de parler.

Micro → faster-whisper (local) → Ollama avec outils (local) → voix du système
(pyttsx3). Rien ne sort de la machine, aucune clé n'est utilisée. C'est un mode
dégradé : moins fin que Gemini Live, sans vision ni recherche web, mais Matthew
répond, ouvre une application, règle le volume, crée un rappel.

Idée reprise de bertrandmbanwi/Jarvis (OFFLINE_MODE, MIT) et des modules
stt/tts du Mark XL de FatihMakes ; le code est écrit pour ce dépôt.

DEUX POINTS D'ENTRÉE (paquet racine, contrat V2)
  - main.py (mode local) : `attach(matthew_assistant)`. La boucle de connexion signale
    chaque échec (`on_live_down`) et chaque connexion (`on_live_up`).
  - client_desktop (HUD relié à Oracle) : `Supervisor` créé par app.py, nourri
    par l'état du lien. Le client reste sans Gemini : ce second cerveau est
    Ollama, local, et ne s'allume que quand Oracle est injoignable.
  Supprimer le dossier : plus de relais, Matthew se tait pendant une panne
  comme avant.

QUAND IL PARLE
  La panne doit durer DOWN_AFTER_S (20 s) : une reconnexion ordinaire ne doit
  pas le réveiller. Il annonce le mode hors-ligne une fois. Sans mot d'éveil
  local, il ne répond qu'aux phrases qui contiennent son nom (« Matthew, quelle
  heure est-il ? »), puis à la suite pendant FOLLOW_UP_S — sinon il répondrait
  à la télévision. Dès que Gemini revient, il se tait.
"""
from __future__ import annotations

import difflib
import re
import threading
import time
import unicodedata

DOWN_AFTER_S = 20.0
#: L'horodatage joint à chaque question, que le modèle recopie parfois (« … [22:59] »).
_STAMP = re.compile(r"\s*\[[^\]]*\d{1,2}[:h]\d{2}[^\]]*\]\s*")
FOLLOW_UP_S = 25.0


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def addressed(text: str, names: list[str]) -> bool:
    """Le nom est dans la phrase, à une lettre près pour un nom de 5 lettres ou
    plus : prononcé à la française, « Matthew » est transcrit « Jarvie » (mesuré)."""
    words = set(re.findall(r"[a-z0-9]+", _fold(text)))
    for name in (_fold(n) for n in names if n):
        if name in words:
            return True
        if len(name) >= 5 and any(abs(len(w) - len(name)) <= 1 and w[:3] == name[:3]
                                  and difflib.SequenceMatcher(None, w, name).ratio() >= 0.8 for w in words):
            return True
    return False


def _settings() -> dict:
    try:
        from memory.config_manager import load_api_keys
        return load_api_keys() or {}
    except Exception:
        return {}


def _names() -> list[str]:
    names = ["matthew", "jarvis"]
    try:
        from memory.config_manager import get_assistant_name
        names.append(get_assistant_name())
    except Exception:
        pass
    return names


SYSTEM = (
    "Tu es {name}, l'assistant vocal de l'utilisateur, en MODE HORS-LIGNE : Internet ou "
    "Gemini est indisponible, tu tournes entièrement sur cet ordinateur. Réponds dans la "
    "langue de l'utilisateur, en une à trois phrases courtes, à l'oral (pas de liste, pas de "
    "markdown). Utilise tes outils pour agir sur l'ordinateur ; n'invente jamais un résultat. "
    "Pour ce qui demande Internet (météo, actualités, recherche), dis simplement que ce "
    "sera possible quand la connexion reviendra. Chaque message porte l'heure locale "
    "actuelle entre crochets : c'est la seule heure à croire."
)


class OfflineVoice:
    """La boucle écouter → comprendre → agir → répondre, dans son propre fil."""

    def __init__(self, log, input_device: str | None = None, output_device: str | None = None,
                 language: str | None = None, names: list[str] | None = None):
        self.log = log
        self.input_device, self.output_device = input_device, output_device
        self.language = language
        self.names = names or _names()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mic = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="offline-voice")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._mic is not None:
            self._mic.close()

    def _say(self, voice, text: str, out_dev) -> None:
        from . import audio
        self.log(f"Matthew (hors-ligne) : {text}")
        try:
            pcm, rate = voice.synthesize(text)
            audio.play(pcm, rate, device=out_dev, mic=self._mic)
        except Exception as e:
            self.log(f"SYS: hors-ligne — voix indisponible ({e}), réponse affichée seulement.")

    def _run(self) -> None:
        from datetime import datetime

        from . import audio, llm, stt, tools, tts
        cfg = _settings()
        picked = llm.pick_model(cfg.get("offline_model"))
        if picked is None:
            self.log("SYS: hors-ligne impossible — Ollama ne tourne pas ou n'a aucun modèle capable "
                     "d'outils (ollama pull qwen3:4b-instruct).")
            return
        model, caps = picked
        self.model = model                  # lu par le badge « qui répond » du HUD (matthew/hud.py)
        brain = llm.Brain(model, caps,
                          SYSTEM.format(name=self.names[-1] or "Matthew"),
                          tools.build(self.log))
        threading.Thread(target=brain.warm, daemon=True, name="offline-warm").start()
        voice = tts.shared(self.language or cfg.get("language"))
        whisper = stt.shared(self.language, log=self.log, hotwords=self.names)
        in_dev = audio.resolve_device(self.input_device, "input")
        out_dev = audio.resolve_device(self.output_device, "output")
        try:
            whisper.load()
            self._mic = audio.Microphone(in_dev)
            self._mic.start()
        except Exception as e:
            self.log(f"SYS: hors-ligne impossible — {type(e).__name__}: {e}")
            return
        if self._stop.is_set():
            self._mic.close()
            return
        self.log(f"SYS: mode hors-ligne actif ({model}). Dites « {self.names[0].capitalize()} » "
                 "au début de la phrase.")
        self._say(voice, "Je suis passé en mode hors-ligne. Appelez-moi par mon nom, je vous écoute.", out_dev)
        last_reply = 0.0
        try:
            while not self._stop.is_set():
                pcm = self._mic.next_utterance(self._stop)
                if not pcm or self._stop.is_set():
                    continue
                try:
                    text = whisper.transcribe(pcm)
                except Exception as e:
                    self.log(f"SYS: hors-ligne — transcription : {e}")
                    continue
                if not text:
                    continue
                if not addressed(text, self.names) and time.monotonic() - last_reply > FOLLOW_UP_S:
                    continue
                self.log(f"VOUS (hors-ligne) : {text}")
                try:
                    stamp = datetime.now().strftime("%A %d/%m/%Y %H:%M")
                    reply = brain.ask(f"[{stamp}] {text}", on_tool=lambda n, a: self.log(f"SYS: hors-ligne → {n}"))
                except Exception as e:
                    reply = "Mon cerveau local ne répond pas pour l'instant."
                    self.log(f"SYS: hors-ligne — Ollama : {e}")
                if self._stop.is_set():
                    break
                reply = _STAMP.sub(" ", reply or "").strip()
                if reply:
                    self._say(voice, reply, out_dev)
                last_reply = time.monotonic()
        finally:
            self._mic.close()
            self.log("SYS: mode hors-ligne terminé.")


class Supervisor:
    """Allume la voix hors-ligne après DOWN_AFTER_S de panne, l'éteint au retour."""

    def __init__(self, make_voice, log, down_after: float = DOWN_AFTER_S, clock=time.monotonic):
        self._make_voice, self._log = make_voice, log
        self.down_after, self._clock = down_after, clock
        self._down_since: float | None = None
        self._timer: threading.Timer | None = None
        self.voice = None
        self._lock = threading.Lock()

    def on_down(self, reason: str = "") -> None:
        with self._lock:
            if self._down_since is not None:
                return
            self._down_since = self._clock()
            self._timer = threading.Timer(self.down_after, self._engage)
            self._timer.daemon = True
            self._timer.start()

    def _engage(self) -> None:
        with self._lock:
            if self._down_since is None:
                return
            if self.voice is None:
                self.voice = self._make_voice()
            voice = self.voice
        self._log("SYS: Gemini injoignable depuis 20 s — relais hors-ligne.")
        voice.start()

    def on_up(self) -> None:
        with self._lock:
            self._down_since = None
            if self._timer:
                self._timer.cancel()
                self._timer = None
            voice = self.voice
        if voice is not None and voice.running:
            voice.stop()
            self._log("SYS: connexion revenue — fin du mode hors-ligne.")

    @property
    def active(self) -> bool:
        return self.voice is not None and self.voice.running


def attach(matthew_assistant) -> None:
    """Hook de main.py, mode local seulement (le serveur n'a ni micro ni haut-parleur)."""
    ui = getattr(matthew_assistant, "ui", None)
    if not getattr(ui, "local_mode", False):
        return
    log = getattr(ui, "write_log", print)

    def _voice():
        try:
            from memory.config_manager import get_input_device, get_output_device
            ind, outd = get_input_device(), get_output_device()
        except Exception:
            ind = outd = None
        return OfflineVoice(log, ind, outd)

    sup = Supervisor(_voice, log)

    class _Hooks:
        on_live_down = staticmethod(sup.on_down)
        on_live_up = staticmethod(sup.on_up)
        supervisor = sup

    matthew_assistant._offline = _Hooks()
