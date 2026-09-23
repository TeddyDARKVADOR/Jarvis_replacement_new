"""
presence/vocabulary.py — twelve words, fifty-two numbers. Pure, no I/O.

WHAT THIS FILE IS
    The dictionary between the only two languages in the system: the words
    JARVIS thinks in (`surprised`, `0.7`) and the coefficients a face is made
    of (`browInnerUp = 0.62`). Nothing else in the project may hold an ARKit
    name — grep for `browInnerUp` and this file should be the only Python hit.

WHY ARKit AND NOT A RIG OF OUR OWN
    Apple's 52 coefficients are the one facial vocabulary the whole industry
    already exports to. Ready Player Me ships them, Aven declares 52 of its 128,
    Inori 52 of its 165, MetaHuman maps to them, and every iPhone since the X
    produces them live. Inventing a thirteenth standard would mean re-authoring
    every asset we could otherwise download. So the intermediate language is
    ARKit, and `avatar/manifest.json` carries the per-model renaming when a mesh
    calls `browInnerUp` something else.

WHY THE SHAPES ARE STORED AT FULL INTENSITY AND SCALED ON READ
    A face at 0.3 is not a different face from the same face at 1.0 — it is the
    same face, quieter. Storing one canonical shape per expression and scaling
    it means an intensity dial that is continuous by construction, so JARVIS can
    be *slightly* amused without anyone having authored "slightly amused".

    The scaling is not linear, and that is the one piece of craft here. Real
    faces move their brows early and their mouths late: a barely-amused face is
    almost entirely a brow and an eye, with the smile arriving only as the
    feeling grows. `_CURVE` encodes that per shape family, which is the
    difference between an expression dial and a transparency slider.

WHY BLINKING AND BREATHING ARE NOT HERE
    They belong to the renderer, at 60 fps, off its own clock — the same
    decision `client_desktop/ui/core_widget.py` documents about `time.monotonic`.
    Sending a blink over a websocket would be sending 200 ms of latency to close
    an eyelid.
"""
from __future__ import annotations

from .model import Expression, Gaze

# ── the 52 ───────────────────────────────────────────────────────────────────
#
# Listed in Apple's own order. Kept complete even though most expressions touch
# a dozen of them: this tuple is what `selftest.py` checks every authored shape
# against, so a typo in a shape name is caught at test time and not as a face
# that silently refuses to move.
ARKIT_52: tuple[str, ...] = (
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
    "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
)

#: How fast each family of shapes comes in as intensity rises. An exponent
#: below 1 front-loads the movement (brows, eyes — they move first), above 1
#: holds it back (mouth, jaw — they arrive last). This is the whole reason a
#: 0.2-intensity face reads as "a flicker of something" rather than "a faded
#: grin".
_CURVE: dict[str, float] = {
    "brow":   0.65,   # les sourcils partent tôt
    "eye":    0.75,
    "cheek":  0.90,
    "nose":   1.00,
    "mouth":  1.30,   # la bouche est le dernier à s'engager
    "jaw":    1.45,
    "tongue": 1.60,
}


def _family(shape: str) -> str:
    for prefix in _CURVE:
        if shape.startswith(prefix):
            return prefix
    return "mouth"


# ── the twelve faces, each authored at full intensity ────────────────────────
#
# Authored against the ARKit reference poses, not invented: each is the
# combination Apple's own documentation and the standard FACS mapping give for
# that emotion. They are deliberately sparse — a face that sets forty
# coefficients reads as a grimace, because real faces do not.

_SHAPES: dict[Expression, dict[str, float]] = {

    Expression.NEUTRAL: {},

    # Sourire de Duchenne : la bouche SEULE fait un sourire de politicien.
    # Ce sont les yeux plissés (cheekSquint + eyeSquint) qui le rendent sincère.
    Expression.HAPPY: {
        "mouthSmileLeft": 0.85, "mouthSmileRight": 0.85,
        "cheekSquintLeft": 0.55, "cheekSquintRight": 0.55,
        "eyeSquintLeft": 0.42, "eyeSquintRight": 0.42,
        "mouthDimpleLeft": 0.30, "mouthDimpleRight": 0.30,
        "browInnerUp": 0.15,
    },

    Expression.SAD: {
        "browInnerUp": 0.80,
        "browDownLeft": 0.25, "browDownRight": 0.25,
        "mouthFrownLeft": 0.60, "mouthFrownRight": 0.60,
        "mouthShrugLower": 0.35,
        "eyeLookDownLeft": 0.30, "eyeLookDownRight": 0.30,
        "eyeBlinkLeft": 0.18, "eyeBlinkRight": 0.18,
    },

    # Très rarement utilisée — voir director.py, qui la plafonne.
    Expression.ANGRY: {
        "browDownLeft": 0.85, "browDownRight": 0.85,
        "eyeSquintLeft": 0.55, "eyeSquintRight": 0.55,
        "noseSneerLeft": 0.40, "noseSneerRight": 0.40,
        "mouthPressLeft": 0.50, "mouthPressRight": 0.50,
        "jawForward": 0.25,
    },

    Expression.SURPRISED: {
        "browInnerUp": 0.90,
        "browOuterUpLeft": 0.85, "browOuterUpRight": 0.85,
        "eyeWideLeft": 0.80, "eyeWideRight": 0.80,
        "jawOpen": 0.45,
        "mouthFunnel": 0.20,
    },

    # Distincte de SAD : les sourcils montent au centre comme dans la tristesse,
    # mais les yeux restent grands ouverts et fixés. C'est de l'attention, pas
    # du chagrin.
    Expression.CONCERNED: {
        "browInnerUp": 0.70,
        "browDownLeft": 0.35, "browDownRight": 0.35,
        "eyeWideLeft": 0.30, "eyeWideRight": 0.30,
        "mouthFrownLeft": 0.28, "mouthFrownRight": 0.28,
        "mouthPressLeft": 0.35, "mouthPressRight": 0.35,
    },

    # Asymétrique volontairement : un visage qui réfléchit n'est jamais
    # symétrique. Le sourcil gauche monte, la bouche part à droite.
    Expression.THINKING: {
        "browDownLeft": 0.45,
        "browOuterUpRight": 0.55,
        "browInnerUp": 0.25,
        "eyeSquintLeft": 0.30, "eyeSquintRight": 0.20,
        "eyeLookUpLeft": 0.35, "eyeLookUpRight": 0.35,
        "mouthLeft": 0.30,
        "mouthPucker": 0.22,
        "mouthRollLower": 0.20,
    },

    # Le registre par défaut de JARVIS. Un demi-sourire, un seul sourcil.
    # C'est l'ironie légère, pas la joie — d'où l'asymétrie marquée.
    Expression.AMUSED: {
        "mouthSmileLeft": 0.55, "mouthSmileRight": 0.28,
        "mouthDimpleLeft": 0.40,
        "browOuterUpLeft": 0.45,
        "eyeSquintLeft": 0.30, "eyeSquintRight": 0.20,
        "cheekSquintLeft": 0.35, "cheekSquintRight": 0.15,
    },

    Expression.SERIOUS: {
        "browDownLeft": 0.40, "browDownRight": 0.40,
        "eyeWideLeft": 0.15, "eyeWideRight": 0.15,
        "mouthPressLeft": 0.45, "mouthPressRight": 0.45,
        "mouthClose": 0.30,
        "jawForward": 0.12,
    },

    Expression.CONFUSED: {
        "browDownLeft": 0.55,
        "browOuterUpRight": 0.70,
        "browInnerUp": 0.30,
        "eyeSquintLeft": 0.40,
        "eyeWideRight": 0.25,
        "mouthLeft": 0.40,
        "mouthPucker": 0.30,
        "mouthShrugUpper": 0.25,
    },

    # Fier : menton légèrement relevé (jawForward), yeux mi-clos, sourire retenu.
    # Un sourire large ici donnerait HAPPY, pas PROUD.
    Expression.PROUD: {
        "mouthSmileLeft": 0.40, "mouthSmileRight": 0.40,
        "mouthPressLeft": 0.25, "mouthPressRight": 0.25,
        "eyeSquintLeft": 0.35, "eyeSquintRight": 0.35,
        "browOuterUpLeft": 0.20, "browOuterUpRight": 0.20,
        "jawForward": 0.20,
        "cheekSquintLeft": 0.30, "cheekSquintRight": 0.30,
    },

    Expression.TIRED: {
        "eyeBlinkLeft": 0.45, "eyeBlinkRight": 0.45,
        "browInnerUp": 0.35,
        "browDownLeft": 0.20, "browDownRight": 0.20,
        "mouthFrownLeft": 0.20, "mouthFrownRight": 0.20,
        "jawOpen": 0.10,
        "eyeLookDownLeft": 0.25, "eyeLookDownRight": 0.25,
    },
}


# ── gaze ─────────────────────────────────────────────────────────────────────
#
# The eyeLook* coefficients, and only those. Head rotation is a gesture and
# belongs to the animation layer — mixing them here would mean a gaze change
# could not happen during a nod, which is exactly when it happens most.
#
# ARKit's naming is anatomical, not screen-relative: `eyeLookInLeft` is the LEFT
# eye looking IN, i.e. toward the nose, i.e. toward the viewer's right. Looking
# to one side therefore always pairs one eye's IN with the other's OUT.

_GAZE: dict[Gaze, dict[str, float]] = {
    Gaze.USER: {},   # droit devant — la position de repos du rig

    Gaze.SCREEN: {   # vers le côté : l'œil gauche rentre, le droit sort
        "eyeLookInLeft": 0.55, "eyeLookOutRight": 0.55,
        "eyeLookUpLeft": 0.10, "eyeLookUpRight": 0.10,
    },

    Gaze.AWAY: {     # l'autre côté, plus haut — le regard de celui qui cherche
        "eyeLookOutLeft": 0.60, "eyeLookInRight": 0.60,
        "eyeLookUpLeft": 0.30, "eyeLookUpRight": 0.30,
    },

    Gaze.DOWN: {
        "eyeLookDownLeft": 0.65, "eyeLookDownRight": 0.65,
        "eyeBlinkLeft": 0.15, "eyeBlinkRight": 0.15,
    },

    #: Un balayage n'est pas une position : le renderer l'anime lui-même. Ce qui
    #: part ici n'est que le point de départ, et `avatar/js/rig.js` y ajoute
    #: l'oscillation. Même raison que pour le clignement.
    Gaze.AROUND: {
        "eyeLookOutLeft": 0.25, "eyeLookInRight": 0.25,
    },

    Gaze.CLOSED: {
        "eyeBlinkLeft": 1.0, "eyeBlinkRight": 1.0,
    },
}


# ── visemes ──────────────────────────────────────────────────────────────────
#
# The mouth shapes speech is made of. Fifteen in the Oculus/Preston-Blair set;
# five are enough for a face read at panel size, and five is what a loudness
# envelope can honestly drive.
#
# THE HONEST LIMIT, STATED ONCE: an amplitude envelope carries no phonemes. What
# `speech_level` can produce is a mouth that opens and closes *in time* with the
# voice, which at 240 px reads as speech and at full screen does not. Real
# visemes need the audio itself — NVIDIA Audio2Face-3D, or a phoneme aligner on
# the TTS output. `avatar/js/lipsync.js` is built to accept either: it takes a
# viseme name from outside if one is given and falls back to this envelope if
# not. The seam exists so that upgrade is a new input, not a rewrite.

VISEMES: dict[str, dict[str, float]] = {
    "sil": {"mouthClose": 0.25},
    "AA":  {"jawOpen": 0.70, "mouthLowerDownLeft": 0.35, "mouthLowerDownRight": 0.35},
    "E":   {"jawOpen": 0.32, "mouthStretchLeft": 0.45, "mouthStretchRight": 0.45,
            "mouthUpperUpLeft": 0.20, "mouthUpperUpRight": 0.20},
    "I":   {"jawOpen": 0.18, "mouthSmileLeft": 0.30, "mouthSmileRight": 0.30,
            "mouthStretchLeft": 0.25, "mouthStretchRight": 0.25},
    "O":   {"jawOpen": 0.50, "mouthFunnel": 0.60, "mouthPucker": 0.30},
    "U":   {"jawOpen": 0.20, "mouthPucker": 0.75, "mouthFunnel": 0.35},
    "M":   {"mouthClose": 0.80, "mouthPressLeft": 0.40, "mouthPressRight": 0.40},
    "F":   {"mouthLowerDownLeft": 0.30, "mouthLowerDownRight": 0.30,
            "mouthRollLower": 0.45, "mouthUpperUpLeft": 0.25, "mouthUpperUpRight": 0.25},
}


# ── the one function anything outside this file calls ────────────────────────

def _scaled(shapes: dict[str, float], intensity: float) -> dict[str, float]:
    if intensity <= 0.0:
        return {}
    out: dict[str, float] = {}
    for shape, full in shapes.items():
        weight = full * (intensity ** _CURVE[_family(shape)])
        if weight >= 0.005:          # sous ce seuil, aucun mesh ne bouge d'un pixel
            out[shape] = round(min(1.0, weight), 4)
    return out


#: The eight shapes that point the eyes. Lids (`eyeBlink*`) are not among them:
#: a sad face's heavy lid is part of the face, wherever the eyes look.
EYE_LOOK: frozenset[str] = frozenset(
    s for s in ARKIT_52 if s.startswith("eyeLook")
)


def face(expression: Expression, intensity: float, gaze: Gaze = Gaze.USER,
         *, gaze_decided: bool = False) -> dict[str, float]:
    """Resolve one expression and one gaze into ARKit weights.

    Combined with `max` rather than by adding: two shapes that both raise a brow
    must not sum to 1.7 and clip. Taking the stronger of the two is what keeps a
    downward gaze during a sad face from looking like a different, broken face.

    `gaze_decided` — someone CHOSE where the eyes go (JARVIS named it, or the
    intent's table did). The expression's own eye direction is then dropped:
    `thinking` rolls the eyes up, and with `"gaze": "user"` written next to it
    the eyes were measured at y = +0.17 — looking over the user's head, while
    the Performance said `user`. Same family as `Directive.gaze`: a decision
    carried all the way to the wire and overruled at the last step. A derived
    gaze keeps the expression's eyes, because then the eyes ARE part of the
    face (sadness looks down on its own).
    """
    intensity = max(0.0, min(1.0, float(intensity)))
    resolved = _scaled(_SHAPES.get(expression, {}), intensity)
    if gaze_decided:
        for shape in EYE_LOOK:
            resolved.pop(shape, None)

    for shape, weight in _GAZE.get(gaze, {}).items():
        resolved[shape] = round(max(resolved.get(shape, 0.0), weight), 4)

    return resolved


def viseme(name: str, openness: float) -> dict[str, float]:
    """One mouth shape at one loudness. Used by the renderer, and by the
    selftest to prove every viseme is made of real ARKit names."""
    openness = max(0.0, min(1.0, float(openness)))
    return _scaled(VISEMES.get(name, VISEMES["sil"]), openness)
