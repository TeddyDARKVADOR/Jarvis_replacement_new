"""
cognition/affect.py — le moteur émotionnel. Pur : pas d'I/O, pas de modèle.

CE QUE C'EST, ET CE QUE CE N'EST PAS
    Six nombres, modifiés par des événements selon des règles écrites ici, qui
    retombent vers une ligne de base avec le temps, et qui ont des conséquences
    sur ce que JARVIS fait. Ce sont des variables CALCULÉES. Rien ici n'est, ni
    ne prouve, un ressenti (docs/cognition/RECHERCHE.md, §1.3–1.5).

    Séparé de presence/affect.py, qui pilote le VISAGE de l'avatar : celui-là
    exprime, celui-ci décide.

POURQUOI DES RÈGLES ET PAS UN MODÈLE
    Chain-of-Emotion (Croissant et al. 2024) fait générer l'émotion par un LLM.
    C'est nuancé mais ni déterministe ni testable. Et au banc d'essai sur ce PC
    (2026-10-13), Qwen3 1.7B sans réflexion a noté valence -1 un remerciement
    après un succès. Ici, l'appraisal (au sens d'EMA, Marsella & Gratch 2009 :
    pertinence pour les buts, désirabilité) est faite par des règles sur des
    événements STRUCTURÉS : un outil a réussi ou échoué, l'utilisateur remercie
    ou corrige. Les règles sont grossières, mais on sait exactement ce qu'elles
    font, et le selftest le vérifie.

L'INVARIANT DE SÉCURITÉ (codé, testé)
    Les conséquences d'un état passent par directives(), qui ne peut renvoyer
    que des éléments de ALLOWED_DIRECTIVES. Aucun d'eux n'élargit une
    permission, ne lève une confirmation ou n'autorise une action : un état
    extrême peut seulement rendre JARVIS plus prudent, plus bref ou plus
    enclin à demander.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Var:
    name: str
    lo: float
    hi: float
    baseline: float
    half_life_s: float
    meaning: str


VARS: dict[str, Var] = {v.name: v for v in (
    Var("valence",     -1.0, 1.0, 0.10, 30 * 60,
        "bilan récent : succès et remerciements (+), échecs et corrections (−)"),
    Var("arousal",      0.0, 1.0, 0.30, 5 * 60,
        "activation : densité d'événements marquants"),
    Var("assurance",    0.0, 1.0, 0.70, 2 * 3600,
        "taux de réussite récent de ses propres actions"),
    Var("frustration",  0.0, 1.0, 0.00, 20 * 60,
        "échecs qui s'accumulent sans succès entre eux"),
    Var("curiosity",    0.0, 1.0, 0.30, 24 * 3600,
        "questions restées sans réponse (rappel vide, recherche infructueuse)"),
    Var("warmth",       0.0, 1.0, 0.50, 14 * 24 * 3600,
        "historique relationnel : remerciements, retours"),
)}


@dataclass
class AffectState:
    values: dict[str, float] = field(default_factory=lambda: {k: v.baseline for k, v in VARS.items()})
    at: float = 0.0                      # horodatage (s) des valeurs
    last_failed_tool: str | None = None  # pour reconnaître un échec répété

    def copy(self) -> "AffectState":
        return AffectState(dict(self.values), self.at, self.last_failed_tool)


def clamp(name: str, x: float) -> float:
    v = VARS[name]
    return max(v.lo, min(v.hi, x))


def decayed(state: AffectState, now: float) -> AffectState:
    """Retour exponentiel vers la ligne de base. Fonction pure du temps : un
    redémarrage n'est pas une remise à zéro, c'est du temps qui passe."""
    dt = max(0.0, now - state.at)
    out = state.copy()
    for name, v in VARS.items():
        x = state.values.get(name, v.baseline)
        out.values[name] = v.baseline + (x - v.baseline) * math.pow(0.5, dt / v.half_life_s)
    out.at = max(now, state.at)
    return out


# ── Appraisal : événement -> changements ─────────────────────────────────────

# Outils sans enjeu : animer l'avatar n'est ni un succès ni un échec, et
# consulter son propre état non plus (sinon demander « comment tu vas ? »
# apaiserait la frustration qu'on est en train de mesurer).
COSMETIC_TOOLS = {"set_presence", "close_camera", "self_state"}
RECALL_TOOLS = {"recall_memory", "recall_episodes"}

_THANKS = re.compile(r"\b(merci|thanks?|thank you|parfait|genial|super|bravo|bien joue|nickel|"
                     r"top|excellent|great|perfect|awesome|nice)\b")
_CORRECTION = re.compile(r"(\bnon\b.{0,25}\b(pas (ca|ce|celui|celle)|c'?est pas|je (t'?ai )?(dis|dit|voulais))|"
                         r"\btu (t'?es|te) trompe|\bc'?est (faux|pas (ca|ce que))|\bpas du tout\b|"
                         r"\bthat'?s (wrong|not (it|what))|\bwrong\b|\bno,? (not|i said|i meant)\b|"
                         r"\bje t'?ai (pas|jamais) dit\b|\bmauvais\b|\bt'?as rien compris\b)")


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def classify_user_turn(text: str) -> list[str]:
    """Indices lexicaux, FR et EN. Grossier par construction ; documenté comme tel."""
    t = _fold(text)
    tags = []
    if _CORRECTION.search(t):
        tags.append("user_correction")
    if _THANKS.search(t) and "user_correction" not in tags:
        tags.append("user_thanks")
    return tags


def tool_failed(result: str) -> bool:
    text = str(result or "")
    head = text[:120].lower()
    return (text.startswith(("Tool '", "Unknown tool")) or " failed" in head
            or head.startswith(("error", "erreur", "échec", "search failed")))


def recall_empty(result: str) -> bool:
    head = str(result or "")[:80].lower()
    return head.startswith(("nothing stored", "aucun souvenir", "i have not stored", "no results"))


# (événement) -> [(variable, delta)] ; "frustration*" = facteur multiplicatif.
RULES: dict[str, list[tuple[str, float]]] = {
    # Réussir enfin ce qui échouait apaise ; réussir autre chose, à peine.
    "tool_recovered":  [("valence", +0.10), ("assurance", +0.06), ("frustration*", 0.3)],
    "tool_ok":         [("valence", +0.05), ("assurance", +0.04), ("frustration*", 0.8)],
    "tool_error":      [("valence", -0.15), ("arousal", +0.15), ("assurance", -0.10),
                        ("frustration", +0.25)],
    "tool_error_again": [("frustration", +0.15), ("assurance", -0.05)],
    "recall_empty":    [("curiosity", +0.15), ("assurance", -0.03)],
    "user_thanks":     [("valence", +0.25), ("warmth", +0.04), ("arousal", +0.05)],
    "user_correction": [("valence", -0.20), ("assurance", -0.12), ("frustration", +0.15),
                        ("arousal", +0.10)],
    "session_start":   [("arousal", +0.10), ("warmth", +0.01)],
}


def events_for_tool(name: str, result: str, state: AffectState) -> list[str]:
    if name in COSMETIC_TOOLS:
        return []
    if tool_failed(result):
        ev = ["tool_error"]
        if state.last_failed_tool == name:
            ev.append("tool_error_again")
        return ev
    if name in RECALL_TOOLS and recall_empty(result):
        return ["recall_empty"]
    return ["tool_recovered" if state.last_failed_tool == name else "tool_ok"]


def apply(state: AffectState, events: list[str], now: float,
          tool: str | None = None) -> tuple[AffectState, list[tuple[str, float, float, str]]]:
    """Décroît jusqu'à `now`, puis applique les règles. Renvoie le nouvel état
    et le journal [(variable, avant, après, règle)] — chaque changement a une
    cause nommée, c'est ce qui rend l'état rapportable (self_state)."""
    s = decayed(state, now)
    log = []
    for ev in events:
        for var, delta in RULES.get(ev, []):
            if var.endswith("*"):
                name = var[:-1]
                before = s.values[name]
                after = clamp(name, before * delta)
            else:
                name = var
                before = s.values[name]
                after = clamp(name, before + delta)
            if after != before:
                s.values[name] = after
                log.append((name, before, after, ev))
        if ev == "tool_error":
            s.last_failed_tool = tool
        elif ev == "tool_recovered":
            s.last_failed_tool = None
    return s, log


# ── Conséquences ─────────────────────────────────────────────────────────────

ALLOWED_DIRECTIVES = {
    "change_strategy": ("Your recent attempts failed repeatedly. Do not retry the same way: "
                        "change approach, or ask the user how to proceed."),
    "caution":         ("Your recent success rate is low. Say how sure you are, and confirm "
                        "with the user before anything hard to undo."),
    "brief":           "Several things are happening at once: keep answers short.",
    "follow_up":       ("Something you looked for was not found. If natural, ask the user one "
                        "short question to fill the gap."),
    "warm":            "Rapport is good: a slightly warmer tone is fine, still professional.",
}

THRESHOLDS = {
    "change_strategy": ("frustration", ">=", 0.70),
    "caution":         ("assurance",   "<",  0.40),
    "brief":           ("arousal",     ">=", 0.75),
    "follow_up":       ("curiosity",   ">=", 0.60),
    "warm":            ("warmth",      ">=", 0.70),
}


def directives(state: AffectState) -> list[str]:
    """Les clés de ALLOWED_DIRECTIVES actives pour cet état, par ordre fixe."""
    out = []
    for key, (var, op, x) in THRESHOLDS.items():
        v = state.values[var]
        if (op == ">=" and v >= x) or (op == "<" and v < x):
            out.append(key)
    return out


def _word(name: str, v: float) -> str:
    if name == "valence":
        return "positive" if v > 0.3 else "negative" if v < -0.2 else "neutral"
    return "high" if v >= 0.7 else "low" if v < 0.35 else "medium"


def describe(state: AffectState) -> str:
    """Une ligne pour le prompt : valeurs ET mots, pour que le rapport puisse
    être comparé aux données (introspection fonctionnelle, phase 6)."""
    parts = [f"{n} {state.values[n]:+.2f}" if n == "valence" else f"{n} {state.values[n]:.2f}"
             for n in VARS]
    return "; ".join(parts)
