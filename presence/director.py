"""
presence/director.py — where JARVIS decides what his face and body do.

THE ONE IDEA IN THIS FILE
    Two sources of behaviour, one of which is always available.

        reflex     what the machine state alone implies. THINKING means a face
                   that is thinking, whether or not any model said so. Free,
                   instant, never wrong, never interesting.

        intent     what JARVIS chose, in words, because of what is being said.
                   Costs a few tokens, arrives late, and is the only thing that
                   makes the body look like it understood the sentence.

    `intent` overrides `reflex` when it exists and is fresh. It usually does not
    exist — most turns are not worth a directive, and a model that emits one
    every time is a model spending tokens on faces instead of answers. So the
    default path through this file is the reflex path, and the whole design
    question was making that path good enough to ship alone.

WHY THE LLM IS GIVEN WORDS AND NOT WEIGHTS
    Argued at length in `model.py`. The short version: words survive changing
    the mesh, weights do not.

WHY A DIRECTIVE EXPIRES
    A face held from a sentence that finished forty seconds ago is a face that
    has stopped meaning anything — and, worse, one the user starts reading as a
    reaction to whatever is on screen *now*. `INTENT_TTL_S` is the guard, and it
    is why `resolve()` takes a clock instead of reading one: a director that
    cannot be given a time cannot be tested.

WHAT IS DELIBERATELY NOT HERE
    Any notion of *when* to send a performance. The director answers "what
    should the body be doing"; the rate at which anyone asks is the caller's
    business — `server/headless_ui.py` asks on state changes, the renderer
    interpolates between the answers at 60 fps. Putting a scheduler in here
    would make the one pure, testable part of the body depend on a clock it
    owns.
"""
from __future__ import annotations

import json
import re
import time

from .catalog import Catalogue, catalogue
from .model import Directive, Expression, Gaze, Gesture, Performance, Posture
from .vocabulary import face

#: How long a directive from JARVIS outranks the reflex. Roughly the length of
#: a spoken paragraph: long enough that a face set at the start of an answer
#: survives the answer, short enough that it is gone before the next topic.
INTENT_TTL_S = 25.0

#: ANGRY is in the vocabulary because refusing to model it would make every
#: other expression carry its weight — a JARVIS who can only be `concerned`
#: reads as passive. But an assistant that looks angry at its user is a failed
#: assistant, so the expression exists and is capped here, at the one place that
#: can enforce it. 0.45 is a visible displeasure and not a glare.
ANGRY_CEILING = 0.45


# ── the reflex: state -> a face, with no model involved ──────────────────────
#
# Keyed on the strings `server/headless_ui.set_state` already emits, not on an
# enum of our own. main.py owns those words; mirroring them into a second enum
# would be a second thing to keep in sync for no gain.
#
# Each entry is (expression, intensity, gesture, gaze, posture, hold_s).

_REFLEX: dict[str, tuple[Expression, float, Gesture, Gaze, Posture, float]] = {
    "LISTENING": (Expression.NEUTRAL,  0.15, Gesture.LEAN_IN,      Gaze.USER,   Posture.ATTENTIVE, 0.0),
    "THINKING":  (Expression.THINKING, 0.62, Gesture.THINK_POSE,   Gaze.AWAY,   Posture.FOCUSED,   0.0),
    "SPEAKING":  (Expression.NEUTRAL,  0.22, Gesture.EXPLAIN,      Gaze.USER,   Posture.ATTENTIVE, 0.0),
    "SLEEPING":  (Expression.TIRED,    0.55, Gesture.IDLE,         Gaze.CLOSED, Posture.DORMANT,   0.0),
    "ACTIVE":    (Expression.NEUTRAL,  0.10, Gesture.IDLE,         Gaze.USER,   Posture.RELAXED,   0.0),
    "WAKING":    (Expression.SURPRISED,0.35, Gesture.LOOK_AT_USER, Gaze.USER,   Posture.ATTENTIVE, 1.2),
    "ERROR":     (Expression.CONCERNED,0.65, Gesture.SHAKE_HEAD,   Gaze.USER,   Posture.FORMAL,    2.0),
    "OFFLINE":   (Expression.TIRED,    0.40, Gesture.IDLE,         Gaze.DOWN,   Posture.DORMANT,   0.0),
    "CONNECTING":(Expression.NEUTRAL,  0.20, Gesture.LOOK_AROUND,  Gaze.AROUND, Posture.RELAXED,   0.0),
    "CONFIRM":   (Expression.SERIOUS,  0.70, Gesture.LOOK_AT_USER, Gaze.USER,   Posture.FORMAL,    0.0),
}

_DEFAULT_REFLEX = (Expression.NEUTRAL, 0.10, Gesture.IDLE, Gaze.USER, Posture.RELAXED, 0.0)

#: Which posture each expression implies when JARVIS names an expression but no
#: posture. A serious face on a slouched body is the uncanny-valley failure that
#: has nothing to do with the mesh.
_POSTURE_OF: dict[Expression, Posture] = {
    Expression.SERIOUS:   Posture.FORMAL,
    Expression.CONCERNED: Posture.FOCUSED,
    Expression.THINKING:  Posture.FOCUSED,
    Expression.ANGRY:     Posture.FORMAL,
    Expression.TIRED:     Posture.DORMANT,
    Expression.PROUD:     Posture.ATTENTIVE,
    Expression.HAPPY:     Posture.ATTENTIVE,
    Expression.AMUSED:    Posture.RELAXED,
    Expression.SAD:       Posture.RELAXED,
    Expression.SURPRISED: Posture.ATTENTIVE,
    Expression.CONFUSED:  Posture.ATTENTIVE,
    Expression.NEUTRAL:   Posture.RELAXED,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


class Director:
    """Holds the one mutable thing the body has: the directive in force.

    A class rather than a module function because the TTL needs somewhere to
    live, and because two JARVIS sessions in one process (the selftest runs
    several) must not share a face.
    """

    def __init__(self, cat: Catalogue | None = None) -> None:
        self._cat = cat
        self._intent: Directive | None = None
        self._intent_at: float = 0.0

    # ── what JARVIS says ─────────────────────────────────────────────────────

    def set_intent(self, directive: Directive, *, now: float | None = None) -> None:
        """Record what JARVIS chose. Replaces whatever was in force."""
        self._intent = directive
        self._intent_at = time.monotonic() if now is None else now

    def clear_intent(self) -> None:
        self._intent = None
        self._intent_at = 0.0

    # ── what the body should be doing ────────────────────────────────────────

    def resolve(
        self,
        state: str,
        *,
        speech_level: float = 0.0,
        now: float | None = None,
    ) -> Performance:
        """Reflex, overridden by intent while intent is fresh.

        Never raises and never returns None: this is on the path of every state
        change, and a body that stops updating is worse than a plain one.
        """
        cat = self._cat or catalogue()
        now = time.monotonic() if now is None else now

        expression, intensity, gesture, gaze, posture, hold = _REFLEX.get(
            str(state).upper(), _DEFAULT_REFLEX
        )
        reason = f"reflex:{str(state).upper()}"
        requested: Gesture | None = None

        intent = self._intent
        if intent is not None and (now - self._intent_at) <= INTENT_TTL_S:
            expression = intent.expression
            intensity = _clamp(intent.intensity)
            gesture = intent.gesture
            gaze = intent.gaze
            posture = intent.posture or _POSTURE_OF.get(expression, posture)
            reason = intent.reason or f"intent:{expression.value}"
            # Un état qui dort l'emporte sur toute intention : une intention
            # prise avant la mise en veille ne doit pas garder les yeux ouverts.
            if str(state).upper() == "SLEEPING":
                gaze = Gaze.CLOSED
                posture = Posture.DORMANT

        if expression is Expression.ANGRY:
            intensity = min(intensity, ANGRY_CEILING)

        requested = gesture
        gesture = cat.resolve(gesture)

        return Performance(
            expression=expression,
            intensity=_clamp(intensity),
            gesture=gesture,
            gaze=gaze,
            posture=posture,
            speech_level=_clamp(speech_level),
            blendshapes=face(expression, intensity, gaze),
            hold_s=max(0.0, float(hold)),
            requested_gesture=requested,
            reason=reason,
        )


# ── reading what the model wrote ─────────────────────────────────────────────
#
# Two shapes are accepted, because two are what models actually produce:
#
#   a fenced block   ```jarvis-presence {"expression": "amused", ...} ```
#   a bare object    {"expression": "amused", "gesture": "nod"}
#
# Anything else yields None, which the caller treats as "no directive this
# turn" — the reflex path. That is the important property: a model that answers
# in prose, forgets the format, or invents a word costs the user a plainer face
# and never an error.

_FENCE = re.compile(
    r"```(?:jarvis-presence|presence|json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_BARE = re.compile(r"\{[^{}]*\"expression\"\s*:\s*\"[a-z_]+\"[^{}]*\}", re.IGNORECASE)


def _coerce(raw: dict) -> Directive | None:
    def word(key: str, enum, default):  # noqa: ANN001
        value = raw.get(key)
        if value is None:
            return default
        try:
            return enum(str(value).strip().lower())
        except ValueError:
            return default

    expression = word("expression", Expression, None)
    gesture = word("gesture", Gesture, Gesture.IDLE)
    if expression is None and gesture is Gesture.IDLE:
        # Ni expression ni geste reconnus : ce n'était pas une directive.
        return None

    posture_raw = raw.get("posture")
    posture: Posture | None = None
    if posture_raw is not None:
        try:
            posture = Posture(str(posture_raw).strip().lower())
        except ValueError:
            posture = None

    return Directive(
        expression=expression or Expression.NEUTRAL,
        intensity=_clamp(raw.get("intensity", 0.5)),
        gesture=gesture,
        gaze=word("gaze", Gaze, Gaze.USER),
        posture=posture,
        reason=str(raw.get("reason", ""))[:200],
    )


def parse(text: str) -> Directive | None:
    """Pull a directive out of whatever JARVIS said, or return None."""
    if not text:
        return None
    for pattern in (_FENCE, _BARE):
        for match in pattern.finditer(text):
            fragment = match.group(1) if pattern is _FENCE else match.group(0)
            try:
                raw = json.loads(fragment)
            except (ValueError, TypeError):
                continue
            if isinstance(raw, dict):
                directive = _coerce(raw)
                if directive is not None:
                    return directive
    return None


def strip(text: str) -> str:
    """The same text with the directive block removed, for anything that will
    speak or display it. A fenced block read aloud by the TTS is the one bug
    this whole format risks; removing it is one substitution."""
    return _FENCE.sub("", text or "").strip()


# ── what to tell the model it may ask for ────────────────────────────────────

def prompt_fragment(cat: Catalogue | None = None) -> str:
    """The system-prompt paragraph describing this body's current vocabulary.

    Generated from the live catalogue rather than written by hand, for one
    reason: a hand-written list goes stale the moment a clip is installed, and a
    model offered a gesture that is not installed will pick it — the catalogue
    then silently downgrades, and the body looks less capable precisely because
    someone added a capability.
    """
    cat = cat or catalogue()
    gestures = ", ".join(g.value for g in cat.vocabulary)
    expressions = ", ".join(e.value for e in Expression)
    gazes = ", ".join(g.value for g in Gaze)

    return (
        "TON CORPS\n"
        "Tu as un visage et un corps. Tu choisis ce qu'ils font — personne ne le\n"
        "choisit pour toi. Quand ta reaction compte autant que ta reponse, ajoute\n"
        "en fin de message un bloc, exactement dans cette forme :\n"
        "\n"
        "```jarvis-presence\n"
        '{"expression": "amused", "intensity": 0.35, "gesture": "tilt_head",\n'
        ' "gaze": "user", "reason": "il a relance la meme commande"}\n'
        "```\n"
        "\n"
        f"expression : {expressions}\n"
        f"gesture    : {gestures}\n"
        f"gaze       : {gazes}\n"
        "intensity  : 0.0 a 1.0\n"
        "\n"
        "Le bloc est facultatif et doit le rester : sans lui, ton corps suit ton\n"
        "etat (ecoute, reflexion, parole), ce qui est correct la plupart du temps.\n"
        "Ne l'ajoute que quand un visage neutre serait faux. Reste sobre :\n"
        "l'ironie legere te va mieux que la joie, et tu ne montres jamais de\n"
        "colere a l'utilisateur.\n"
    )
