"""
pilot/agent.py — la boucle : regarder, choisir, agir, vérifier.

    agent = Agent(brain, confirm=…, log=…)
    result = agent.run("envoie à Paul sur Teams : je serai en retard")

Chaque étape : observation (a11y.observe) → requête au modèle (brain) → UNE
action → l'écran suivant dira si elle a marché. Fin : done, fail, need_info,
STOP demandé, ou MAX_STEPS.

LA GARDE — dans le code, pas seulement dans la consigne du modèle :
  - cliquer sur un élément dont le nom ressemble à envoyer, publier, supprimer,
    payer, valider, accepter… ; taper avec submit ; ou Entrée juste après avoir
    écrit dans un champ de rédaction (message, courriel, commentaire — pas une
    recherche) : JARVIS demande d'abord à l'utilisateur, avec le texte exact.
    Une confirmation déjà donnée par l'outil confirm couvre l'action suivante.
  - jamais de texte dans un champ de mot de passe ;
  - jamais de remplacement du contenu d'un document ou d'un champ déjà rempli
    (une fausse manœuvre l'a montré le 2026-10-16 : un SetValue dans un onglet
    existant du Bloc-notes) : on écrit au curseur.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from pilot import brain as B
from pilot.model import Observation, from_norm

MAX_STEPS = 30
SETTLE_S = 0.8
SENSITIVE = re.compile(
    r"(send|envoy|submit|soumet|valid|confirm|pay|paie|payer|achet|buy|order|commande|supprim|delete|remove|"
    r"effac|publi|post|partag|share|transf|réserv|reserv|book|sign|accept|rejoin|join|install|désinstall|uninstall)",
    re.I)
COMPOSE = re.compile(r"(message|compose|chat|répon|repon|reply|mail|courriel|comment|écri|ecri|type a|tapez|rédig|"
                     r"redig|body|corps|note|post)", re.I)
SEARCH = re.compile(r"(search|recherch|cherch|find|filtr|adresse|address|url)", re.I)
PASSWORD = re.compile(r"(password|mot de passe|passcode|code pin|\bpin\b|otp|cvv|cvc)", re.I)


@dataclass
class Result:
    outcome: str                  # done | fail | need_info | stopped | error | max_steps
    message: str
    steps: list[str] = field(default_factory=list)
    app: str = ""


class Agent:
    def __init__(self, brain, confirm: Callable[[str], bool], log: Callable[[str], None] = print,
                 observe=None, act=None, inputs=None, discover=None, recipes=None, max_steps: int = MAX_STEPS,
                 sleep: Callable[[float], None] = time.sleep):
        from pilot import a11y, discover as disc, inputs as inp
        self.brain, self.confirm, self.log = brain, confirm, log
        self.observe = observe or a11y.observe
        self.a11y_act = act or a11y.act
        self.focus_window = a11y.focus_window
        self.inputs, self.discover = inputs or inp, discover or disc
        self.recipes, self.max_steps, self.sleep = recipes, max_steps, sleep
        self._stop = threading.Event()
        self.history: list[str] = []
        self.actions: list[str] = []       # pour les recettes : les actions, jamais le texte tapé
        self.status = ""
        self._approved_until = -1          # étape jusqu'à laquelle une confirmation couvre l'action sensible
        self._typed: tuple[str, str] | None = None   # (nom du champ, texte) de la dernière saisie
        self._refused = False                         # un « non » vaut pour toute la tâche : on ne redemande pas

    def stop(self) -> None:
        self._stop.set()

    # ── la boucle ──
    def run(self, task: str) -> Result:
        hints = self.recipes.hints(task) if self.recipes else []
        app = ""
        for n in range(1, self.max_steps + 1):
            if self._stop.is_set():
                return Result("stopped", "Arrêté à ta demande.", self.history, app)
            obs = self._observe()
            if obs.locked:
                return Result("fail", "L'écran est verrouillé : déverrouille-le et redemande-moi.", self.history, app)
            app = obs.window.app or app
            self.status = f"étape {n} — {obs.window.title[:60]}"
            body = B.build_request(task, self.history, obs.render(), obs.image, hints)
            try:
                name, args = B.parse(self.brain.step(body))
            except Exception as e:
                return Result("error", f"Le modèle ne répond pas : {e}", self.history, app)
            if self._stop.is_set():
                return Result("stopped", "Arrêté à ta demande.", self.history, app)
            if name in ("done", "fail", "need_info"):
                msg = str(args.get("summary") or args.get("reason") or args.get("question") or "")
                if name == "done" and self.recipes:
                    self.recipes.add(task, app, self.actions)
                return Result(name, msg, self.history, app)
            try:
                outcome = self.execute(n, name, args, obs)
            except Exception as e:                       # une action ratée se raconte au modèle
                outcome = f"échec : {type(e).__name__}: {e}"[:300]
            line = f"{n}: {name}({_short(args)}) → {outcome}"
            self.history.append(line)
            self.actions.append(f"{name}({_short({k: v for k, v in args.items() if k != 'text'})})")
            self.log(f"SYS: pilote — {line}")
        return Result("max_steps", f"J'ai atteint {self.max_steps} étapes sans finir.", self.history, app)

    def _observe(self) -> Observation:
        obs = self.observe()
        # Le HUD de JARVIS au premier plan (l'utilisateur vient de lui parler) : la fenêtre de travail est dessous.
        if obs.window.pid == os.getpid() and obs.windows:
            if self.focus_window(obs.windows[0]):
                self.sleep(0.4)
                obs = self.observe()
        return obs

    # ── la garde ──
    def sensitive(self, name: str, args: dict, obs: Observation) -> str | None:
        """La description de l'action si elle est irréversible ou visible des autres ; None sinon. Pur."""
        title = obs.window.title or obs.window.app or "cette fenêtre"
        if name == "click":
            el = obs.element(args.get("element")) if args.get("element") is not None else None
            if el is None and args.get("x") is not None and args.get("y") is not None:
                el = _hit(obs, float(args["x"]), float(args["y"]))
            label = f"{el.name} {el.value}" if el else ""
            if el and SENSITIVE.search(label):
                extra = f" (texte : « {self._typed[1][:300]} »)" if self._typed else ""
                return f"Cliquer sur « {el.name[:60]} » dans « {title[:60]} »{extra}"
        if name == "type_text" and args.get("submit"):
            return f"Écrire puis valider dans « {title[:60]} » : « {str(args.get('text'))[:300]} »"
        if name == "press_keys" and "enter" in str(args.get("keys", "")).lower().replace("return", "enter"):
            if self._typed and COMPOSE.search(self._typed[0]) and not SEARCH.search(self._typed[0]):
                return f"Envoyer dans « {title[:60]} » : « {self._typed[1][:300]} »"
        return None

    def execute(self, n: int, name: str, args: dict, obs: Observation) -> str:
        what = self.sensitive(name, args, obs)
        if what and n > self._approved_until:
            self.log(f"SYS: pilote — confirmation demandée : {what}")
            if self._refused or not self.confirm(what):
                self._refused = True
                return ("REFUSED by the user (or no answer): it was NOT done. Do not ask again; finish now with "
                        "done or fail, saying what was not done")
        if name == "confirm":
            ok = not self._refused and self.confirm(str(args.get("summary") or ""))
            if ok:
                self._approved_until = n + 2
                return "yes"
            self._refused = True
            return ("no — the user REFUSED: do not do it and do not ask again; finish now with done or fail, "
                    "saying what was not done")
        result = self._do(name, args, obs)
        if name in ("click", "press_keys", "type_text", "open", "focus_window", "scroll"):
            self.sleep(SETTLE_S)
        return result

    # ── les actions ──
    def _do(self, name: str, args: dict, obs: Observation) -> str:
        I, D = self.inputs, self.discover
        if name == "open":
            out = D.open_target(str(args.get("target") or ""))
            self.sleep(2.0)
            return out
        if name == "find_apps":
            return json.dumps(D.apps(str(args.get("query") or "")), ensure_ascii=False)
        if name == "url_schemes":
            return json.dumps(D.schemes(str(args.get("query") or "")), ensure_ascii=False)
        if name == "visited_sites":
            return json.dumps(D.sites(str(args.get("query") or "")), ensure_ascii=False)[:1500]
        if name == "focus_window":
            return "fenêtre au premier plan" if self.focus_window(str(args.get("title") or "")) else "fenêtre introuvable"
        if name == "wait":
            self.sleep(max(0.5, min(10.0, float(args.get("seconds") or 2))))
            return "attendu"
        if name == "click":
            el = obs.element(args.get("element")) if args.get("element") is not None else None
            double, right = bool(args.get("double")), bool(args.get("right"))
            if el is not None:
                if not double and not right:
                    via = self.a11y_act(el, "click")
                    if via:
                        return f"« {el.name[:40]} » : {via}"
                x, y = el.center
            elif args.get("x") is not None and args.get("y") is not None:
                x, y = from_norm(float(args["x"]), float(args["y"]), obs.screen)
            else:
                return "ni élément ni coordonnées"
            return I.click(x, y, "right" if right else "left", double)
        if name == "type_text":
            text = str(args.get("text") or "")
            el = obs.element(args.get("element")) if args.get("element") is not None else None
            label = f"{el.name} {el.role}" if el else ""
            if el is not None and PASSWORD.search(el.name):
                return "REFUSÉ : champ de mot de passe — demander à l'utilisateur (need_info)"
            if el is not None:
                if not (self.a11y_act(el, "focus") or self.a11y_act(el, "click")):
                    I.click(*el.center)
                if el.value.strip() and el.role == "document":
                    I.keys("ctrl+end")          # jamais de remplacement d'un document : on écrit à la fin
            else:
                focused = next((e for e in obs.elements if e.focused), None)
                label = f"{focused.name} {focused.role}" if focused else (self._typed[0] if self._typed else "")
            out = I.type_text(text)
            self._typed = (label, text)
            if args.get("submit"):
                out += " ; " + I.keys("enter")
                self._typed = None
            return out
        if name == "press_keys":
            out = I.keys(str(args.get("keys") or ""))
            if "enter" in str(args.get("keys", "")).lower():
                self._typed = None
            return out
        if name == "scroll":
            el = obs.element(args.get("element")) if args.get("element") is not None else None
            l, t, r, b = obs.window.rect
            x, y = el.center if el else ((l + r) // 2, (t + b) // 2)
            return I.scroll(x, y, str(args.get("direction") or "down"), int(args.get("amount") or 5))
        return f"action inconnue : {name}"


def _hit(obs: Observation, x: float, y: float):
    """L'élément le plus petit qui contient le point (0..1000) — pour garder aussi les clics par coordonnées."""
    px, py = from_norm(x, y, obs.screen)
    inside = [e for e in obs.elements if e.rect[0] <= px <= e.rect[2] and e.rect[1] <= py <= e.rect[3]]
    return min(inside, key=lambda e: (e.rect[2] - e.rect[0]) * (e.rect[3] - e.rect[1]), default=None)


def _short(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
