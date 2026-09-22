"""
presence/director.py — where JARVIS decides what his face and body do.

THE ONE IDEA IN THIS FILE
    Three sources of behaviour, and the first is always available.

        reflex     what the machine state alone implies. THINKING means a face
                   that is thinking, whether or not any model said so. Free,
                   instant, never wrong, never interesting.

        affect     an inner state in six continuous numbers — valence, arousal,
                   attention, confidence, urgency, register. It does not name a
                   face; a face is DERIVED from it, along with the gaze, the
                   posture, the speed of gestures and the stillness of the idle.
                   It is a state rather than an event, so it decays toward a
                   baseline instead of expiring. See `presence/affect.py`.

        intent     what JARVIS named outright, in words. Short, exact, highest
                   priority, and the only one of the three that expires at once.

    Most turns produce neither of the last two — a model that describes its own
    face every sentence is a model spending tokens on grimaces. So the default
    path through this file is still the reflex path, and the design question was
    making that path good enough to ship alone.

WHY AFFECT RATHER THAN MORE EXPRESSIONS
    Two JARVIS equally "amused" do not behave alike if one is attentive and sure
    of himself and the other distracted and hesitant. With a single enum those
    two are the same face, and every nuance between them has to be hand-written.
    With six numbers, behaviour is a calculation — and adding one is arithmetic,
    not another branch.

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

from .affect import (
    Affect,
    SocialMode,
    expression_for,
    gaze_for,
    gaze_hold_for,
    intensity_for,
    posture_for,
    stillness_for,
    tempo_for,
)
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

#: L'etat interieur que chaque etat machine implique, quand JARVIS n'en a
#: exprime aucun.
#:
#: Il ne sert PAS a choisir le visage — la table ci-dessus le fait, et elle est
#: reglee. Il sert aux parametres continus : vitesse des gestes, immobilite du
#: repos, duree de tenue du regard. Sans lui, un JARVIS qui reflechit et un
#: JARVIS qui dort auraient le meme repos, ce qui est la chose la plus visible
#: qu'on puisse rater entre deux decisions.
_REFLEX_AFFECT: dict[str, Affect] = {
    "LISTENING":  Affect(valence=0.10, arousal=0.30, attention=0.95, confidence=0.75),
    "THINKING":   Affect(valence=0.00, arousal=0.40, attention=0.35, confidence=0.45),
    "SPEAKING":   Affect(valence=0.15, arousal=0.45, attention=0.85, confidence=0.80),
    "SLEEPING":   Affect(valence=-0.10, arousal=0.03, attention=0.05, confidence=0.60),
    "ACTIVE":     Affect(),
    "WAKING":     Affect(valence=0.20, arousal=0.75, attention=0.90, confidence=0.55),
    "ERROR":      Affect(valence=-0.50, arousal=0.65, attention=0.90, confidence=0.40, urgency=0.60),
    "OFFLINE":    Affect(valence=-0.25, arousal=0.08, attention=0.20, confidence=0.50),
    "CONNECTING": Affect(valence=0.00, arousal=0.35, attention=0.40, confidence=0.45),
    "CONFIRM":    Affect(valence=-0.05, arousal=0.50, attention=0.98, confidence=0.90,
                         urgency=0.55, social_mode=SocialMode.FORMAL),
}

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
    """Trois couches de comportement, dont une est toujours disponible.

        reflexe   ce que l'etat machine implique. THINKING donne un visage qui
                  reflechit, qu'un modele l'ait dit ou non. Gratuit, instantane,
                  jamais faux, jamais interessant.

        affect    l'etat interieur, en six nombres continus. Il ne nomme pas un
                  visage : il en fait DERIVER un, plus le regard, la posture, la
                  vitesse des gestes et l'amplitude du repos. C'est un etat, pas
                  un evenement : il DECROIT vers une base au lieu d'expirer.

        intention ce que JARVIS a nomme explicitement, en mots. Court, precis,
                  prioritaire, et le seul des trois a expirer d'un coup.

    L'ordre est celui-la et il compte. L'affect ecrase le reflexe parce qu'un
    etat interieur en sait plus qu'un etat machine. L'intention ecrase l'affect
    parce que nommer un visage est une decision, et qu'une decision doit pouvoir
    contredire une tendance — sinon JARVIS ne peut pas etre serieux au milieu
    d'une bonne humeur, ce qui arrive tout le temps.

    Ce que les trois ne decident jamais : QUAND envoyer une performance. Le
    directeur repond a « que devrait faire le corps » ; la cadence appartient a
    l'appelant. Un ordonnanceur ici rendrait la seule partie pure et testable du
    corps dependante d'une horloge qu'elle possederait.
    """

    def __init__(self, cat: Catalogue | None = None) -> None:
        self._cat = cat
        self._intent: Directive | None = None
        self._intent_at: float = 0.0
        self._affect: Affect | None = None
        self._affect_at: float = 0.0

    # ── ce que JARVIS exprime ────────────────────────────────────────────────

    def set_intent(self, directive: Directive, *, now: float | None = None) -> None:
        """Enregistre ce que JARVIS a choisi. Remplace ce qui etait en vigueur.

        Une directive qui porte un affect le publie AUSSI comme etat : c'est la
        forme normale — JARVIS decrit ce qu'il ressent, le visage en decoule, et
        l'etat survit a la phrase pendant que le visage nomme, lui, expire.
        """
        stamp = time.monotonic() if now is None else now
        self._intent = directive
        self._intent_at = stamp
        if isinstance(getattr(directive, "affect", None), Affect):
            self._affect = directive.affect
            self._affect_at = stamp

    def set_affect(self, affect: Affect, *, now: float | None = None) -> None:
        """Publie un etat interieur sans nommer de visage."""
        self._affect = affect
        self._affect_at = time.monotonic() if now is None else now

    def clear_intent(self) -> None:
        self._intent = None
        self._intent_at = 0.0

    def clear_affect(self) -> None:
        self._affect = None
        self._affect_at = 0.0

    def affect_now(self, now: float | None = None) -> Affect | None:
        """L'etat interieur tel qu'il est a cet instant, decroissance comprise.

        Expose parce que c'est la premiere chose qu'on veut lire quand un visage
        surprend, et parce que le labo l'affiche.
        """
        if self._affect is None:
            return None
        now = time.monotonic() if now is None else now
        return self._affect.decayed(now - self._affect_at)

    # ── ce que le corps devrait faire ────────────────────────────────────────

    def resolve(
        self,
        state: str,
        *,
        speech_level: float = 0.0,
        now: float | None = None,
    ) -> Performance:
        """Reflexe, puis affect, puis intention. Ne leve jamais, ne rend jamais None.

        C'est sur le chemin de chaque changement d'etat, et un corps qui cesse
        de se mettre a jour est pire qu'un corps simple.
        """
        cat = self._cat or catalogue()
        now = time.monotonic() if now is None else now
        word = str(state).upper()

        # ── 1. le reflexe ────────────────────────────────────────────────────
        expression, intensity, gesture, gaze, posture, hold = _REFLEX.get(word, _DEFAULT_REFLEX)
        affect = _REFLEX_AFFECT.get(word, Affect())
        reason = f"reflex:{word}"
        derived = False

        # ── 2. l'affect, s'il y en a un ──────────────────────────────────────
        live = self.affect_now(now)
        if live is not None:
            affect = live
            expression = expression_for(affect)
            intensity = intensity_for(affect)
            gaze = gaze_for(affect)
            posture = posture_for(affect)
            reason = (f"affect:v{affect.valence:+.2f} a{affect.arousal:.2f} "
                      f"att{affect.attention:.2f} conf{affect.confidence:.2f} "
                      f"urg{affect.urgency:.2f}")
            derived = True

        # ── 3. l'intention nommee ────────────────────────────────────────────
        intent = self._intent
        requested: Gesture | None = None
        if intent is not None and (now - self._intent_at) <= INTENT_TTL_S:
            # Une intention qui ne portait QUE de l'affect a deja tout dit a
            # l'etape 2 ; la reecraser avec ses defauts (neutral, 0.5, idle)
            # effacerait precisement ce qu'elle exprimait.
            if not (derived and getattr(intent, "affect", None) is not None):
                expression = intent.expression
                intensity = _clamp(intent.intensity)
                gaze = intent.gaze
                posture = intent.posture or _POSTURE_OF.get(expression, posture)
                reason = intent.reason or f"intent:{expression.value}"
            elif intent.reason:
                reason = intent.reason
            # Le geste vient toujours de l'intention : l'affect faconne, il ne
            # designe pas. « amuse » ne veut pas dire « hausse un sourcil ».
            gesture = intent.gesture

        # ── 4. ce qui prime sur tout ─────────────────────────────────────────
        if word == "SLEEPING":
            # Un etat qui dort l'emporte sur toute intention et tout affect :
            # une humeur prise avant la mise en veille ne doit pas garder les
            # yeux ouverts.
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
            tempo=tempo_for(affect),
            stillness=stillness_for(affect),
            gaze_hold_s=gaze_hold_for(affect),
            affect=affect,
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
    r"```(?:jarvis-presence|presence|json)?\s*(\{.*\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

def _objects(text: str):
    """Les objets JSON de premier niveau du texte, dans l'ordre.

    POURQUOI PAS UNE EXPRESSION REGULIERE
        Parce qu'une expression reguliere ne sait pas compter les accolades, et
        que la forme affect est imbriquee :

            {"emotion": {"valence": -0.7, "arousal": 0.3},
             "confidence": 0.5, "reason": "mauvaise nouvelle"}

        Un motif qui cherche "valence" trouve l'objet INTERIEUR — il est plus
        court, il correspond, et il est syntaxiquement valide. La directive est
        alors lue avec sa valence et son activation correctes, et sans sa
        confiance ni sa raison. Rien n'echoue, rien n'est signale, et la moitie
        de ce que JARVIS a exprime est perdue.

        C'est exactement ce qui se passait. Compter les accolades est la seule
        reponse, et les guillemets doivent etre suivis au passage : une accolade
        dans une chaine ("reason": "il a tape {") n'est pas une accolade.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start:index + 1])
                start = -1
    return spans


def _number(raw: dict, *keys, default=None):
    """Le premier de `keys` present et numerique.

    Plusieurs noms par valeur parce que les modeles ecrivent indifferemment
    `social_mode` et `socialMode`, `emotionIntensity` et `intensity`. Refuser
    une directive pour une histoire de casse serait refuser la moitie d'entre
    elles.
    """
    for key in keys:
        if key in raw and isinstance(raw[key], (int, float)):
            return float(raw[key])
    return default


def _affect_from(raw: dict) -> Affect | None:
    """L'etat interieur, quand la directive en decrit un.

    Accepte les deux dispositions que les modeles produisent :

        {"emotion": {"valence": 0.4, "arousal": 0.3}, "attention": 0.9}
        {"valence": 0.4, "arousal": 0.3, "attention": 0.9}

    Rend None si rien de reconnaissable ne s'y trouve — pas une exception, et
    pas un affect de defaut : un affect invente serait un etat interieur que
    JARVIS n'a jamais eu, applique pendant vingt secondes.
    """
    emotion = raw.get("emotion")
    inner = emotion if isinstance(emotion, dict) else {}

    valence = _number(inner, "valence")
    if valence is None:
        valence = _number(raw, "valence")
    arousal = _number(inner, "arousal")
    if arousal is None:
        arousal = _number(raw, "arousal")

    attention = _number(raw, "attention")
    confidence = _number(raw, "confidence")
    urgency = _number(raw, "urgency")

    mode_raw = raw.get("socialMode", raw.get("social_mode"))
    mode = None
    if mode_raw is not None:
        try:
            mode = SocialMode(str(mode_raw).strip().lower())
        except ValueError:
            mode = None

    if all(v is None for v in (valence, arousal, attention, confidence, urgency)) and mode is None:
        return None

    base = Affect()
    return Affect(
        valence=base.valence if valence is None else valence,
        arousal=base.arousal if arousal is None else arousal,
        attention=base.attention if attention is None else attention,
        confidence=base.confidence if confidence is None else confidence,
        urgency=base.urgency if urgency is None else urgency,
        social_mode=mode or base.social_mode,
    )


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
    affect = _affect_from(raw)

    # `emotion` peut etre un simple mot : {"emotion": "amused"}. C'est la forme
    # la plus courante quand un modele improvise, et elle doit etre resolue
    # AVANT le garde ci-dessous — sinon la directive est jetee pour cause de cle
    # differente, ce qui est exactement le genre de rejet que ce parseur existe
    # pour eviter.
    if expression is None and isinstance(raw.get("emotion"), str):
        try:
            expression = Expression(raw["emotion"].strip().lower())
        except ValueError:
            expression = None

    if expression is None and gesture is Gesture.IDLE and affect is None:
        # Ni expression, ni geste, ni etat reconnus : ce n'etait pas une directive.
        return None

    posture_raw = raw.get("posture")
    posture: Posture | None = None
    if posture_raw is not None:
        try:
            posture = Posture(str(posture_raw).strip().lower())
        except ValueError:
            posture = None

    intensity = _number(raw, "intensity", "emotionIntensity", "emotion_intensity",
                        default=0.5)

    return Directive(
        expression=expression or Expression.NEUTRAL,
        intensity=_clamp(intensity),
        gesture=gesture,
        gaze=word("gaze", Gaze, Gaze.USER),
        posture=posture,
        reason=str(raw.get("reason", ""))[:200],
        affect=affect,
    )


def parse(text: str) -> Directive | None:
    """Pull a directive out of whatever JARVIS said, or return None.

    The fenced block is tried first because it is the explicit form; bare
    objects are a fallback for a model that forgot the fence, which happens.
    Anything unrecognisable yields None — the reflex path — because a model that
    answers in prose or invents a word should cost the user a plainer face and
    never an error.
    """
    if not text:
        return None

    candidates: list[str] = []
    for match in _FENCE.finditer(text):
        candidates.extend(_objects(match.group(1)) or [match.group(1)])
    candidates.extend(_objects(text))

    for fragment in candidates:
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
    """The system-prompt paragraph describing this body and how to drive it.

    Generated from the live catalogue rather than written by hand, for one
    reason: a hand-written list goes stale the moment a clip is installed, and a
    model offered a gesture that is not installed will pick it — the catalogue
    then silently downgrades, and the body looks less capable precisely because
    someone added a capability.

    Both forms are documented, and the affect form is presented FIRST. That is
    deliberate: a model shown a list of twelve expressions picks one of twelve
    expressions, and the whole point of the affect form is that it does not have
    to. Describing a state is also easier for a model than naming a face — it is
    much closer to what it already worked out in order to write the sentence.
    """
    cat = cat or catalogue()
    gestures = ", ".join(g.value for g in cat.vocabulary)
    expressions = ", ".join(e.value for e in Expression)
    gazes = ", ".join(g.value for g in Gaze)
    modes = ", ".join(m.value for m in SocialMode)

    return f"""TON CORPS

Tu as un visage et un corps. Tu choisis ce qu'ils font — personne ne le choisit
pour toi. Quand ta reaction compte autant que ta reponse, ajoute en fin de
message un bloc, dans l'une de ces deux formes.

FORME 1 — decrire ton etat (preferee)
Tu decris ce que tu ressens ; ton visage, ton regard, ta posture et le rythme de
tes gestes en decoulent tout seuls.

```jarvis-presence
{{"emotion": {{"valence": 0.45, "arousal": 0.25}},
 "attention": 0.91, "confidence": 0.76, "urgency": 0.12,
 "socialMode": "professional",
 "gesture": "tilt_head",
 "reason": "il a relance la meme commande"}}
```

  valence      -1.0 desagreable    ->   +1.0 agreable
  arousal       0.0 calme          ->    1.0 active
  attention     0.0 ailleurs       ->    1.0 sur l'utilisateur
  confidence    0.0 hesitant       ->    1.0 assure
  urgency       0.0 rien ne presse ->    1.0 il faut agir
  socialMode   {modes}

FORME 2 — nommer un visage
Quand tu veux exactement celui-la et aucun autre.

```jarvis-presence
{{"expression": "amused", "intensity": 0.35, "gesture": "tilt_head",
 "gaze": "user", "reason": "..."}}
```

  expression   {expressions}
  gaze         {gazes}

GESTES (les deux formes)
  {gestures}

Un etat faconne un mouvement, il ne le designe pas : le geste se declare, dans
l'une comme dans l'autre forme.

Le bloc est facultatif et doit le rester. Sans lui, ton corps suit ton etat
machine — ecoute, reflexion, parole — ce qui est correct la plupart du temps.
Ne l'ajoute que quand un visage neutre serait faux. Reste sobre : l'ironie
legere te va mieux que la joie, et tu ne montres jamais de colere a
l'utilisateur.
"""
