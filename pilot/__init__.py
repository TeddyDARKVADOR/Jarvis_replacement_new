"""
pilot/ — JARVIS prend la main sur l'ordinateur, pour n'importe quelle application.

« Ouvre Teams et envoie à Paul : je serai en retard », « fais-moi le diagramme de
classes sur mon site UML », « dans VS Code, ouvre le fichier où est l'erreur » :
le pilote regarde l'écran, choisit une action, la fait, vérifie, recommence.
Rien n'est écrit en dur pour une application : il lit ce que le système dit
de l'écran (arbre d'accessibilité : UI Automation, AT-SPI, AX), ce que la
machine déclare (applications, schémas d'URL, sites déjà visités) et ce qui a
déjà marché sur cette machine (recettes).

    a11y.py      l'observation : fenêtre active, éléments numérotés, capture
    discover.py  applications, liens profonds, sites déjà visités, ouvrir
    inputs.py    clavier et souris (pyautogui ; wdotool/ydotool sous Wayland)
    brain.py     le modèle (Gemini) : une action par étape
    agent.py     la boucle, et la garde (confirmation avant tout envoi)
    recipes.py   ce qui a marché, en indice pour la prochaine fois

DEUX POINTS D'ENTRÉE (paquet racine, contrat V2)
  - main.py (mode local) : `attach(jarvis)` — clé locale, confirmation sur le
    HUD (core/confirm.py), compte rendu à la voix.
  - client_desktop (PC relié à Oracle) : `pilot.client.start(...)` — le PC
    regarde et agit ; Oracle choisit l'action, demande la confirmation et
    annonce le résultat (server/assist.py : /api/pilot/step, /confirm, /report).
  L'outil vocal est plugins/pilot.py (PLUGIN_DEVICE : il tourne sur le PC).

Arrêt : « stop » à la voix (outil pilot, action stop), ou la souris jetée
dans un coin de l'écran (pyautogui.FAILSAFE).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

_pilot: "Pilot | None" = None


class Pilot:
    """Une tâche à la fois, dans son fil ; la voix reçoit le résultat à la fin."""

    def __init__(self, make_brain: Callable[[], object], confirm: Callable[[str], bool],
                 report: Callable[[str, str], None], log: Callable[[str], None] = print,
                 recipes_path: Path | None = None, agent_factory=None):
        self.make_brain, self.confirm, self.report, self.log = make_brain, confirm, report, log
        self.recipes_path = recipes_path
        self._agent_factory = agent_factory
        self.agent = None
        self.task = ""
        self.last: dict = {}
        self._thread: threading.Thread | None = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, task: str) -> str:
        task = (task or "").strip()
        if not task:
            return "Dis-moi ce que je dois faire sur l'ordinateur."
        if self.running():
            return f"Je suis déjà sur une tâche (« {self.task[:80]} »). Dis « stop » pour l'arrêter."
        from pilot.agent import Agent
        from pilot.recipes import Recipes
        recipes = Recipes(self.recipes_path) if self.recipes_path else None
        self.agent = (self._agent_factory or Agent)(self.make_brain(), confirm=self.confirm, log=self.log,
                                                    recipes=recipes)
        self.task = task
        self._thread = threading.Thread(target=self._run, args=(self.agent, task), name="pilot", daemon=True)
        self._thread.start()
        return ("Je m'en occupe sur l'ordinateur ; je te dis quand c'est fait. Je te demanderai avant d'envoyer "
                "ou de supprimer quoi que ce soit. Dis « stop » pour m'arrêter.")

    def _run(self, agent, task: str) -> None:
        t0 = time.monotonic()
        try:
            result = agent.run(task)
            outcome, message, steps = result.outcome, result.message, len(result.steps)
        except Exception as e:                         # le fil ne meurt pas en silence
            outcome, message, steps = "error", f"{type(e).__name__}: {e}", 0
        self.last = {"task": task, "outcome": outcome, "message": message, "steps": steps,
                     "seconds": round(time.monotonic() - t0)}
        self.log(f"SYS: pilote — {outcome} en {steps} étape(s) : {message[:200]}")
        try:
            self.report(outcome, message)
        except Exception as e:
            self.log(f"SYS: pilote — compte rendu non transmis : {e}")

    def stop(self) -> str:
        if not self.running() or self.agent is None:
            return "Je ne pilotais rien."
        self.agent.stop()
        return "J'arrête."

    def status(self) -> str:
        if self.running() and self.agent is not None:
            return f"En cours : « {self.task[:80]} » — {self.agent.status or 'je regarde l écran'}."
        if self.last:
            return (f"Dernière tâche : « {self.last['task'][:80]} » — {self.last['outcome']} en "
                    f"{self.last['steps']} étape(s) : {self.last['message'][:200]}")
        return "Aucune tâche en cours."


def get() -> "Pilot | None":
    return _pilot


def install(pilot: Pilot) -> Pilot:
    global _pilot
    _pilot = pilot
    return pilot


def spoken(outcome: str, message: str) -> str:
    """La consigne donnée à la voix quand la tâche se termine."""
    what = {"done": "is done", "fail": "could not be done", "need_info": "needs an answer from the user",
            "stopped": "was stopped", "max_steps": "took too many steps and was stopped",
            "error": "failed"}.get(outcome, "ended")
    return (f"[PILOT] The task you were doing on the computer {what}: {message[:600]} "
            "Tell the user in one or two short sentences, in their language. If it needs an answer, ask the "
            "question. Do not call any tool unless the user answers.")


def confirm_on_hud(title: str, timeout: float = 95.0, say: Callable[[str], None] | None = None) -> bool:
    """Bloque jusqu'à CONFIRMER (vrai) ou ANNULER / délai (faux), par core/confirm.py. Pour le mode
    local et pour Oracle : le modèle ne peut pas fabriquer ce jeton."""
    from core import confirm
    if confirm.pending_title():
        return False                                   # une autre confirmation attend : on ne l'écrase pas
    accepted = threading.Event()
    sentence = confirm.request("pilot", "JARVIS va : " + title[:120], title,
                               lambda: (accepted.set(), "fait")[1])
    if not confirm.pending_id():
        return False                                   # pas d'interface : request() a refusé
    cid = confirm.pending_id()
    if say:
        try:
            say(sentence)
        except Exception:
            pass
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if accepted.wait(0.25):
            return True
        if confirm.pending_id() != cid:                # annulée, expirée ou remplacée
            return accepted.is_set()
    return False


def attach(jarvis) -> None:
    """Hook de main.py, mode local seulement (sur Oracle, c'est le PC relié qui pilote)."""
    global _pilot
    if _pilot is not None or not getattr(getattr(jarvis, "ui", None), "local_mode", False):
        return

    def settings() -> dict:
        try:
            from memory.config_manager import load_api_keys
            return load_api_keys() or {}
        except Exception:
            return {}

    def make_brain():
        from pilot.brain import MODELS, LocalBrain
        cfg = settings()
        first = str(cfg.get("pilot_model") or "").strip()
        models = (first,) + tuple(m for m in MODELS if m != first) if first else MODELS
        return LocalBrain(str(cfg.get("gemini_api_key") or ""), models)

    def say(text: str) -> None:
        if getattr(jarvis, "session", None):
            jarvis.plugin_say(text)

    def report(outcome: str, message: str) -> None:
        try:
            jarvis.ui.write_log(f"SYS: Pilote — {outcome} : {message[:200]}")
        except Exception:
            pass
        say(spoken(outcome, message))

    def log(text: str) -> None:
        try:
            jarvis.ui.write_log(text)
        except Exception:
            print(text)

    base = Path(__file__).resolve().parent.parent
    install(Pilot(make_brain, lambda title: confirm_on_hud(title, say=say), report, log,
                  recipes_path=base / "memory" / "pilot_recipes.json"))
