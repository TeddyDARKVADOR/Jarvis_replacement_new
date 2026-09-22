"""
presence/affect.py — ce que JARVIS ressent, en nombres continus. Pur, sans I/O.

POURQUOI PAS SIMPLEMENT UNE ÉMOTION
    `Expression.AMUSED` est un point. Un état intérieur est un endroit dans un
    espace, et la différence n'est pas académique : deux JARVIS également
    « amusés » ne se comportent pas pareil si l'un est attentif et sûr de lui
    et l'autre distrait et hésitant. Avec une seule énumération, ces deux-là
    sont le même visage et il faut coder à la main chaque nuance qui les sépare.

    Six nombres, et tout le reste se dérive :

        valence      -1..1   désagréable → agréable
        arousal       0..1    calme → activé
        attention     0..1    ailleurs → sur l'utilisateur
        confidence    0..1    hésitant → assuré
        urgency       0..1    rien ne presse → il faut agir
        social_mode           le registre : professionnel, détendu, formel

    Le visage, le regard, la posture, la vitesse des gestes et jusqu'à
    l'amplitude du repos sortent de là. Ajouter un comportement devient un
    calcul, pas une ligne de plus dans un `if`.

LE MODÈLE CIRCOMPLEXE, ET CE QU'IL NE SUFFIT PAS À FAIRE
    Valence et arousal viennent de Russell : c'est le socle standard, et il
    place correctement la joie, la tristesse, la colère et le calme. Il ne
    distingue PAS `confused` de `thinking`, ni `proud` de `happy` — ces
    paires-là partagent un coin du plan et ne diffèrent que par l'assurance.
    D'où `confidence` comme troisième axe, et `urgency` comme quatrième : c'est
    ce qui sépare `serious` de `neutral` quand tout le reste est identique.

    Les douze expressions sont donc des *ancres* dans cet espace à quatre
    dimensions, et choisir un visage est une recherche du plus proche voisin.
    Ajouter une treizième expression, c'est ajouter une ancre — pas rouvrir la
    logique.

POURQUOI L'AFFECT DÉCROÎT AU LIEU D'EXPIRER
    Une directive expire : elle concernait une phrase, la phrase est finie.
    Un état intérieur ne s'éteint pas d'un coup, il retombe. Un JARVIS qui
    passe de préoccupé à parfaitement neutre en une image a l'air d'avoir
    redémarré ; le même qui y revient en quarante secondes a l'air de s'être
    calmé. `decayed()` est toute la différence, et c'est une fonction du temps
    qu'on lui passe, donc elle se teste.

POURQUOI RIEN N'EST ARRONDI ICI
    L'arrondi est une affaire de presentation, et le mettre dans le calcul a un
    cout precis : `round()` de Python arrondit au pair, `Math.round` de
    JavaScript arrondit vers le haut, et multiplier par 100 avant d'arrondir
    introduit sa propre erreur. Sur 20 000 points de l'espace affectif, ces
    trois details produisaient 6 300 desaccords entre les deux implementations
    — tous d'un millieme, tous invisibles, et tous suffisants pour qu'aucune
    comparaison exacte ne soit possible.

    Les valeurs sont donc rendues en pleine precision. Elles sont arrondies une
    seule fois, au bord du fil, dans `Performance.as_json` — et le labo les
    formate pour l'affichage. Les deux implementations peuvent alors etre
    comparees a l'identique, ce qui est la seule facon de voir arriver une
    vraie divergence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from .model import Expression, Gaze, Gesture, Intent, Posture


class SocialMode(str, Enum):
    """Le registre. Ce qui est permis, pas ce qui est ressenti.

    Séparé de la valence délibérément : JARVIS peut trouver quelque chose très
    drôle *et* être en réunion. Le registre plafonne l'expression sans toucher
    à l'état, ce qui est exactement ce qu'un adulte fait.
    """
    PROFESSIONAL = "professional"   # le registre par défaut de JARVIS
    CASUAL       = "casual"         # seul avec l'utilisateur, rien en jeu
    INTIMATE     = "intimate"       # tard, confiance établie, plus de latitude
    FORMAL       = "formal"         # action irréversible, sécurité, autorité


#: Ce que chaque registre autorise comme intensité maximale.
#:
#: Ce n'est pas de la censure, c'est de la justesse : un sourire à 0.9 pendant
#: une confirmation de suppression n'est pas « expressif », il est faux.
_CEILING: dict[SocialMode, float] = {
    SocialMode.FORMAL:       0.50,
    SocialMode.PROFESSIONAL: 0.68,
    SocialMode.CASUAL:       0.88,
    SocialMode.INTIMATE:     1.00,
}

#: Vers quoi l'affect retombe quand plus rien ne le nourrit. Pas zéro partout :
#: un JARVIS au repos est attentif et assuré, pas éteint.
BASELINE = dict(valence=0.05, arousal=0.22, attention=0.80, confidence=0.72, urgency=0.0)

#: Demi-vie de la décroissance, en secondes. Assez long pour qu'une réaction
#: survive à la phrase qui l'a causée, assez court pour qu'elle ne colore pas
#: le sujet suivant.
DECAY_HALF_LIFE_S = 22.0


#: Tous les seuils de derivation, en un seul endroit.
#:
#: POURQUOI UN DICTIONNAIRE ET PAS DES NOMBRES DANS LE CODE
#:     Ces memes seuils existent en JavaScript, dans `avatar/js/affect.js`, parce
#:     que le labo doit pouvoir deriver un comportement sans qu'aucun Python ne
#:     tourne — c'est toute sa raison d'etre. Un nombre ecrit en dur des deux
#:     cotes derive au premier reglage, et la derive ne se voit pas : le labo
#:     montre simplement un comportement legerement different de celui qui sera
#:     livre.
#:
#:     Rassembles ici, ils sont GENERES vers le JavaScript et le test les
#:     compare. Regler l'un regle l'autre.
TUNING: dict[str, float] = {
    # intensite
    "intensity_base":        0.12,
    "intensity_arousal":     0.58,
    "intensity_valence":     0.34,
    "intensity_urgency_min": 0.75,

    # regard
    "gaze_urgency":       0.55,   # au-dela, le regard revient sur l'utilisateur
    "gaze_attention_high": 0.62,  # au-dela, il regarde l'utilisateur
    "gaze_attention_mid":  0.35,  # entre les deux, il cherche
    "gaze_confidence_mid": 0.50,  # peu sur de lui -> ailleurs ; sur de lui -> balaie
    "gaze_arousal_low":    0.12,  # en dessous, le regard tombe

    # posture
    "posture_urgency":        0.60,
    "posture_arousal_dormant": 0.10,
    "posture_attention_dormant": 0.35,
    "posture_attention_high":  0.75,
    "posture_arousal_high":    0.30,
    "posture_confidence_mid":  0.55,
    "posture_attention_mid":   0.55,

    # rythme
    "tempo_base":     0.72,
    "tempo_arousal":  0.45,
    "tempo_urgency":  0.55,
    "tempo_min":      0.55,
    "tempo_max":      1.85,

    # immobilite
    "stillness_base":       0.25,
    "stillness_calm":       0.45,
    "stillness_confidence": 0.30,

    # tenue du regard, en secondes
    "gaze_hold_base":      1.20,
    "gaze_hold_attention": 4.50,
    "gaze_hold_urgency":   0.80,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


@dataclass(frozen=True)
class Affect:
    """L'état intérieur. Six nombres, aucun comportement."""

    valence: float = 0.05      # -1..1
    arousal: float = 0.22      # 0..1
    attention: float = 0.80    # 0..1
    confidence: float = 0.72   # 0..1
    urgency: float = 0.0       # 0..1
    social_mode: SocialMode = SocialMode.PROFESSIONAL

    def __post_init__(self) -> None:
        # Borné à la construction plutôt qu'à l'usage : un affect hors bornes
        # qui circule est un affect qui produira une intensité hors bornes
        # quelque part, loin d'ici, et sans trace de son origine.
        object.__setattr__(self, "valence", _clamp(self.valence, -1.0, 1.0))
        object.__setattr__(self, "arousal", _clamp(self.arousal))
        object.__setattr__(self, "attention", _clamp(self.attention))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "urgency", _clamp(self.urgency))

    def decayed(self, seconds: float) -> "Affect":
        """L'état après `seconds` sans rien pour l'entretenir.

        Décroissance exponentielle vers `BASELINE`, pas vers zéro : voir le
        commentaire de BASELINE. `social_mode` ne décroît pas — un registre est
        une décision, pas une humeur.
        """
        if seconds <= 0:
            return self
        k = 0.5 ** (seconds / DECAY_HALF_LIFE_S)
        return Affect(
            valence=BASELINE["valence"] + (self.valence - BASELINE["valence"]) * k,
            arousal=BASELINE["arousal"] + (self.arousal - BASELINE["arousal"]) * k,
            attention=BASELINE["attention"] + (self.attention - BASELINE["attention"]) * k,
            confidence=BASELINE["confidence"] + (self.confidence - BASELINE["confidence"]) * k,
            # L'urgence retombe deux fois plus vite : une urgence qui traîne est
            # une urgence qui n'en était pas une.
            urgency=self.urgency * (k ** 2),
            social_mode=self.social_mode,
        )

    def as_json(self) -> dict:
        return {
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "attention": round(self.attention, 3),
            "confidence": round(self.confidence, 3),
            "urgency": round(self.urgency, 3),
            "social_mode": self.social_mode.value,
        }


# ── les douze ancres ─────────────────────────────────────────────────────────
#
# Chaque expression est un point dans (valence, arousal, confidence, urgency).
# Choisir un visage = trouver l'ancre la plus proche. Ces valeurs sont des
# jugements, pas des mesures, et c'est assumé : ce qui compte est qu'elles
# soient CONSISTANTES entre elles, ce que le selftest vérifie en reprojetant
# chaque ancre et en exigeant qu'elle retrouve sa propre expression.

_ANCHORS: dict[Expression, tuple[float, float, float, float]] = {
    #                          valence arousal confid  urgency
    Expression.NEUTRAL:   (  0.00,  0.20,  0.70,  0.00),
    Expression.AMUSED:    (  0.50,  0.35,  0.82,  0.00),
    Expression.HAPPY:     (  0.85,  0.70,  0.80,  0.00),
    Expression.PROUD:     (  0.60,  0.45,  0.97,  0.00),
    Expression.SURPRISED: (  0.10,  0.92,  0.35,  0.25),
    Expression.CONCERNED: ( -0.45,  0.62,  0.45,  0.65),
    Expression.SERIOUS:   ( -0.10,  0.48,  0.88,  0.75),
    Expression.THINKING:  (  0.00,  0.38,  0.42,  0.05),
    Expression.CONFUSED:  ( -0.20,  0.45,  0.12,  0.05),
    Expression.SAD:       ( -0.72,  0.22,  0.50,  0.00),
    Expression.TIRED:     ( -0.18,  0.04,  0.48,  0.00),
    Expression.ANGRY:     ( -0.82,  0.88,  0.82,  0.55),
}

#: Ce que chaque axe pèse dans la recherche du plus proche voisin.
#:
#: La valence pèse le plus : se tromper de signe est la seule erreur qu'un
#: utilisateur remarque à coup sûr — sourire à une panne. La confiance pèse
#: moins parce qu'elle ne sépare que des paires voisines (`proud`/`happy`,
#: `confused`/`thinking`), où se tromper coûte une nuance et pas un contresens.
_WEIGHTS = (1.00, 0.85, 0.55, 0.70)


def expression_for(affect: Affect) -> Expression:
    """Le visage le plus proche de cet état. Déterministe, total."""
    point = (affect.valence, affect.arousal, affect.confidence, affect.urgency)
    best: tuple[float, Expression] | None = None
    for expression, anchor in _ANCHORS.items():
        distance = math.sqrt(sum(
            (weight * (a - b)) ** 2
            for weight, a, b in zip(_WEIGHTS, point, anchor)
        ))
        if best is None or distance < best[0]:
            best = (distance, expression)
    return best[1]


def intensity_for(affect: Affect) -> float:
    """À quel point ce visage se voit.

    L'activation domine — c'est elle qui fait bouger un visage — et la valence
    contribue par sa magnitude, pas par son signe : une tristesse profonde est
    aussi visible qu'une joie franche. Le registre plafonne le tout.
    """
    raw = (TUNING["intensity_base"]
           + affect.arousal * TUNING["intensity_arousal"]
           + abs(affect.valence) * TUNING["intensity_valence"])
    # L'urgence force un minimum : un JARVIS qui trouve la situation urgente et
    # garde un visage de marbre ne transmet pas l'urgence.
    raw = max(raw, affect.urgency * TUNING["intensity_urgency_min"])
    return min(raw, _CEILING[affect.social_mode])


def gaze_for(affect: Affect) -> Gaze:
    """Où vont les yeux. L'attention décide, l'urgence passe devant.

    L'ordre est le fond : quelque chose d'urgent ramène le regard sur
    l'utilisateur même en pleine réflexion, parce que c'est ce que fait
    quelqu'un qui vient de comprendre que ça compte.
    """
    if affect.urgency >= TUNING["gaze_urgency"]:
        return Gaze.USER
    if affect.attention >= TUNING["gaze_attention_high"]:
        return Gaze.USER
    if affect.attention >= TUNING["gaze_attention_mid"]:
        # Attention moyenne : il cherche. Peu sûr de lui, il regarde ailleurs ;
        # sûr de lui, il balaie.
        return (Gaze.AWAY if affect.confidence < TUNING["gaze_confidence_mid"]
                else Gaze.AROUND)
    if affect.arousal < TUNING["gaze_arousal_low"]:
        return Gaze.DOWN
    return Gaze.AROUND


def posture_for(affect: Affect) -> Posture:
    """Le canal lent. Ce que le corps dit avant que le visage ne parle."""
    urgent = affect.urgency >= TUNING["posture_urgency"]
    if affect.social_mode is SocialMode.FORMAL or urgent:
        return Posture.FOCUSED if urgent else Posture.FORMAL
    if (affect.arousal < TUNING["posture_arousal_dormant"]
            and affect.attention < TUNING["posture_attention_dormant"]):
        return Posture.DORMANT
    if (affect.attention >= TUNING["posture_attention_high"]
            and affect.arousal >= TUNING["posture_arousal_high"]):
        return (Posture.FOCUSED if affect.confidence < TUNING["posture_confidence_mid"]
                else Posture.ATTENTIVE)
    if affect.attention >= TUNING["posture_attention_mid"]:
        return Posture.ATTENTIVE
    return Posture.RELAXED


def tempo_for(affect: Affect) -> float:
    """Vitesse des gestes, 1.0 = nominal.

    Urgence et activation accélèrent ; la fatigue ralentit. Les bornes évitent
    les deux ridicules : un geste au ralenti qui n'arrive jamais, et un
    hochement en accéléré qui ressemble à un tic.
    """
    speed = (TUNING["tempo_base"]
             + affect.arousal * TUNING["tempo_arousal"]
             + affect.urgency * TUNING["tempo_urgency"])
    return _clamp(speed, TUNING["tempo_min"], TUNING["tempo_max"])


def stillness_for(affect: Affect) -> float:
    """À quel point le repos est immobile. 0 = agité, 1 = statue.

    C'est le paramètre qui fait le plus pour la vie du personnage entre deux
    décisions, et il vient presque entièrement de l'activation : quelqu'un de
    calme bouge peu et lentement. La confiance immobilise aussi — l'hésitation
    se voit dans les micro-mouvements, pas dans le visage.
    """
    calm = 1.0 - affect.arousal
    steady = affect.confidence
    return _clamp(TUNING["stillness_base"]
                  + calm * TUNING["stillness_calm"]
                  + steady * TUNING["stillness_confidence"])


def gaze_hold_for(affect: Affect) -> float:
    """Combien de temps le regard tient avant de dériver, en secondes.

    Une attention haute ne veut pas dire un regard fixe — un regard parfaitement
    fixe est ce que fait une caméra, pas un interlocuteur. Elle veut dire un
    regard qui REVIENT. D'où une durée, pas un verrou.
    """
    return (TUNING["gaze_hold_base"]
            + affect.attention * TUNING["gaze_hold_attention"]
            - affect.urgency * TUNING["gaze_hold_urgency"])


# ── les intentions : pourquoi, avant comment ─────────────────────────────────

#: Où chaque intention place JARVIS, et par quoi elle voudrait passer.
#:
#: `(valence, arousal, attention, confidence, urgency, geste, regard)`
#:
#: POURQUOI ÇA VIT ICI ET PAS DANS UN FICHIER À PART
#:     C'est la même chose que `_ANCHORS` : un point nommé dans l'espace
#:     affectif. La seule différence est le sens de lecture — une ancre répond
#:     « à quoi ressemble cet état », une intention « dans quel état met cette
#:     situation ». Les deux tables se vérifient par le même mécanisme de parité
#:     avec `avatar/js/affect.js`, et un fichier de plus aurait été un troisième
#:     endroit à garder synchronisé pour aucun gain.
#:
#: LE GESTE EST TOUJOURS CELUI DU CORPS COMPLET
#:     `greet` demande `wave`, y compris sur un corps dont les bras sont gelés.
#:     C'est délibéré et c'est tout l'intérêt : `FALLBACK_CHAIN` le rabat sur
#:     `nod` en mode visage, et le jour où un clip de salut est installé le même
#:     mot produit un vrai salut. L'intention ne nomme jamais le moyen, donc
#:     elle n'a pas à être réécrite quand les moyens changent.
#:
#: LE REGARD, LUI, EST PARFOIS EXPLICITE
#:     `gaze_for()` ne rend jamais `screen` : il dérive de l'attention, et
#:     l'attention ne sait pas qu'il existe un écran. Une intention qui regarde
#:     un résultat doit donc le dire. Les autres laissent `None` et se laissent
#:     dériver — un regard imposé sans raison est un regard qui ne réagit plus à
#:     l'état.
_INTENTS: dict[Intent, tuple[float, float, float, float, float, Gesture, Gaze | None]] = {
    #                     valence arousal attent  confid  urgenc  geste                  regard
    Intent.GREET:          ( 0.76,  0.64,  0.95,  0.82,  0.00, Gesture.WAVE,         Gaze.USER),
    Intent.FAREWELL:       ( 0.32,  0.24,  0.90,  0.86,  0.00, Gesture.BOW,          Gaze.USER),

    Intent.ACKNOWLEDGE:    ( 0.15,  0.30,  0.92,  0.88,  0.05, Gesture.NOD,          Gaze.USER),
    Intent.WAIT:           ( 0.10,  0.26,  0.96,  0.68,  0.00, Gesture.LEAN_IN,      Gaze.USER),

    Intent.INVESTIGATE:    ( 0.00,  0.55,  0.30,  0.55,  0.15, Gesture.TURN,         Gaze.SCREEN),
    Intent.THINK:          ( 0.00,  0.36,  0.28,  0.40,  0.05, Gesture.THINK_POSE,   Gaze.AWAY),
    Intent.EXPLAIN:        ( 0.10,  0.42,  0.88,  0.84,  0.05, Gesture.EXPLAIN,      Gaze.USER),

    Intent.AGREE:          ( 0.45,  0.36,  0.92,  0.92,  0.00, Gesture.NOD,          Gaze.USER),
    Intent.DISAGREE:       (-0.22,  0.48,  0.92,  0.86,  0.55, Gesture.SHAKE_HEAD,   Gaze.USER),
    Intent.AMUSE:          ( 0.58,  0.38,  0.86,  0.86,  0.00, Gesture.TILT_HEAD,    Gaze.USER),

    Intent.CONFIRM:        (-0.05,  0.52,  0.96,  0.90,  0.72, Gesture.LOOK_AT_USER, Gaze.USER),
    Intent.WARN:           (-0.48,  0.74,  0.95,  0.78,  0.88, Gesture.LEAN_IN,      Gaze.USER),
    Intent.REASSURE:       ( 0.38,  0.22,  0.94,  0.92,  0.00, Gesture.BLINK_SLOW,   Gaze.USER),

    Intent.REPORT_SUCCESS: ( 0.75,  0.58,  0.88,  0.94,  0.00, Gesture.THUMBS_UP,    Gaze.USER),
    Intent.REPORT_FAILURE: (-0.52,  0.58,  0.92,  0.38,  0.48, Gesture.SIGH,         Gaze.USER),
    Intent.APOLOGISE:      (-0.58,  0.32,  0.90,  0.30,  0.25, Gesture.BOW,          Gaze.DOWN),
}


def affect_for_intent(intent: Intent, base: Affect | None = None) -> Affect:
    """L'état intérieur où cette intention met JARVIS.

    `base` n'est là que pour le registre : `social_mode` est une décision de
    contexte, pas une conséquence de la situation. Saluer en registre formel et
    saluer en registre intime sont la même intention et deux comportements, et
    c'est le plafond du registre qui fait la différence — pas la table.
    """
    valence, arousal, attention, confidence, urgency, _gesture, _gaze = _INTENTS[intent]
    return Affect(
        valence=valence,
        arousal=arousal,
        attention=attention,
        confidence=confidence,
        urgency=urgency,
        social_mode=base.social_mode if base is not None else Affect().social_mode,
    )


def gesture_for_intent(intent: Intent) -> Gesture:
    """Par quoi cette intention voudrait passer, sur un corps qui peut tout.

    « Voudrait » est le mot : rien ici ne vérifie qu'il est installé. C'est
    `catalog.resolve()` qui tranche, plus tard, contre le corps réel — et qui
    garde la demande d'origine dans `requested_gesture`.
    """
    return _INTENTS[intent][5]


def gaze_for_intent(intent: Intent) -> Gaze | None:
    """Où cette intention regarde, ou `None` pour laisser l'état décider."""
    return _INTENTS[intent][6]
