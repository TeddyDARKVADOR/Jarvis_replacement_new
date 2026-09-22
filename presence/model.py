"""
presence/model.py — the vocabulary of the body. No logic, no I/O, no imports.

WHY THE BODY HAS ITS OWN VOCABULARY
    Four things have to agree on what "surprised" means: the LLM that picks the
    word, the director that resolves it, the renderer that draws it, and the
    person reading the debug window. If each carried its own strings they would
    drift — and the drift would show up as JARVIS smiling at a disk failure.
    One vocabulary, imported by all four. Same reason `context/model.py` exists.

WHY JARVIS NEVER NAMES A BONE
    An LLM asked for `browInnerUp = 0.42` is an LLM doing rigging, badly, and
    doing it again for every sentence. It is also a decision that cannot survive
    changing the model: those weights are true of one mesh and of no other.

    So the contract is two layers deep on purpose:

        JARVIS says      surprised, 0.7, look_at_user
        vocabulary.py    -> 52 ARKit weights for THIS intensity
        the renderer     -> whatever that mesh calls those 52 shapes

    JARVIS chooses in words. Only `vocabulary.py` knows about faces, and only
    `avatar/manifest.json` knows about one particular face. Swap Ready Player Me
    for Aven, for MetaHuman, for a robot with four shape keys — the decision
    layer above is untouched, which is the only reason the body is replaceable.

WHY THE VOCABULARY IS LARGER THAN WHAT IS INSTALLED
    `Gesture` lists what JARVIS is *allowed to want*, not what this workstation
    can currently perform. A vocabulary bounded by today's asset folder would
    have to be edited — and the model re-prompted — every time a Mixamo clip is
    dropped in. So the enum is the ambition, `catalog.py` is the inventory, and
    `FALLBACK_CHAIN` is what makes the gap harmless: an uninstalled `facepalm`
    degrades to `shake_head`, then to `idle`, and JARVIS is never wrong for
    having asked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Expression(str, Enum):
    """What JARVIS's face is doing. The twelve states, and no thirteenth.

    str-valued so it serialises to JSON with no custom encoder — same reason
    `context.model.Priority` is.

    Kept deliberately small. These are not emotions, they are *readable* faces:
    a list of forty would give the LLM forty ways to pick something the user
    cannot tell apart from its neighbour.
    """
    NEUTRAL   = "neutral"      # au repos, attentif, rien de particulier
    HAPPY     = "happy"        # content — la tâche a réussi
    SAD       = "sad"          # désolé — mauvaise nouvelle à annoncer
    ANGRY     = "angry"        # contrarié — à utiliser très rarement
    SURPRISED = "surprised"    # inattendu — un résultat qui sort de l'ordinaire
    CONCERNED = "concerned"    # inquiet — un risque, une erreur, une alerte
    THINKING  = "thinking"     # en train de chercher / raisonner
    AMUSED    = "amused"       # ironie légère, le registre par défaut de JARVIS
    SERIOUS   = "serious"      # registre formel — action irréversible, sécurité
    CONFUSED  = "confused"     # la demande n'a pas été comprise
    PROUD     = "proud"        # satisfait du travail rendu
    TIRED     = "tired"        # ressources basses, session très longue


class Gesture(str, Enum):
    """What the body is doing. The vocabulary JARVIS picks from.

    Grouped by what the gesture needs from the rig, because that is what decides
    whether a given model can perform it at all:

      * REPOS / TÊTE    — head and neck only. Every rig has these, and the
                          procedural fallback body performs them too.
      * BUSTE           — needs a torso. A floating head cannot shrug.
      * BRAS            — needs arms. A bust cannot wave.
      * CORPS ENTIER    — needs legs and a root motion clip.

    `catalog.py` filters this list down to what is installed; nothing here
    promises that any of it exists.
    """

    # ── repos et tête : toujours disponibles ─────────────────────────────────
    IDLE          = "idle"            # respiration, micro-mouvements, clignements
    LOOK_AT_USER  = "look_at_user"    # se tourner vers l'utilisateur
    LOOK_AWAY     = "look_away"       # détourner le regard — réflexion, gêne
    LOOK_AROUND   = "look_around"     # balayer la pièce / les écrans
    NOD           = "nod"             # acquiescer
    SHAKE_HEAD    = "shake_head"      # nier
    TILT_HEAD     = "tilt_head"       # curiosité, doute
    LEAN_IN       = "lean_in"         # attention accrue — il écoute vraiment
    LEAN_BACK     = "lean_back"       # recul, détente
    BLINK_SLOW    = "blink_slow"      # fatigue, patience
    SIGH          = "sigh"            # lassitude — épaules et souffle

    # ── buste ────────────────────────────────────────────────────────────────
    SHRUG         = "shrug"           # « je ne sais pas »
    TURN          = "turn"            # se tourner vers un écran, une source
    BOW           = "bow"             # déférence, accusé de réception formel
    STRETCH       = "stretch"         # s'étirer — utilisé au réveil de session

    # ── bras ─────────────────────────────────────────────────────────────────
    WAVE          = "wave"            # saluer
    POINT         = "point"           # désigner un écran, un résultat
    PRESENT       = "present"         # « voilà » — paume ouverte vers le contenu
    EXPLAIN       = "explain"         # gestes d'appui pendant une explication
    THINK_POSE    = "think"           # main au menton
    THUMBS_UP     = "thumbs_up"       # validation
    CROSS_ARMS    = "cross_arms"      # attente, scepticisme
    FACEPALM      = "facepalm"        # l'utilisateur a refait la même erreur
    SALUTE        = "salute"          # accusé de réception militaire — MARK LIII
    TYPE          = "type"            # il travaille sur autre chose
    COUNT_OFF     = "count_off"       # énumérer sur les doigts

    # ── corps entier ─────────────────────────────────────────────────────────
    STAND         = "stand"           # se lever
    SIT           = "sit"             # s'asseoir
    WALK          = "walk"            # se déplacer dans la scène
    STEP_ASIDE    = "step_aside"      # dégager le champ — il masque quelque chose


class Gaze(str, Enum):
    """Where the eyes go. Separate from `Gesture` on purpose.

    The eyes are the fastest thing on a face and the first thing a human reads.
    A gesture lasts a second or two; a gaze holds until something changes it. If
    they shared one field, every glance would cost a full gesture slot and
    JARVIS could not nod *while* watching the user — which is most of what
    attentive listening looks like.
    """
    USER    = "user"      # l'utilisateur — la position par défaut
    SCREEN  = "screen"    # ce dont il parle : un résultat, une fenêtre
    AWAY    = "away"      # dans le vide — réflexion
    DOWN    = "down"      # vers le bas — gêne, concentration
    AROUND  = "around"    # balayage — veille, attente
    CLOSED  = "closed"    # yeux fermés — veille profonde, SLEEPING


class Posture(str, Enum):
    """The body's resting attitude, which outlives any single gesture.

    This is the slow channel. A posture change is how the user reads that the
    session itself has changed register — awake, working, standing by — without
    JARVIS having to say so.
    """
    ATTENTIVE = "attentive"   # droit, tourné vers l'utilisateur
    RELAXED   = "relaxed"     # au repos, disponible
    FOCUSED   = "focused"     # penché en avant, concentré
    FORMAL    = "formal"      # très droit — registre sérieux
    DORMANT   = "dormant"     # affaissé, immobile — SLEEPING


#: Where a gesture degrades when it is not installed. Walked until something in
#: the catalogue is reached; `IDLE` terminates every chain and is always
#: performable, including by the procedural body that ships with no assets.
#:
#: The chains are not arbitrary — each step preserves the *intent* and drops
#: only the means. `FACEPALM` -> `SHAKE_HEAD` keeps "no"; `FACEPALM` -> `WAVE`
#: would keep nothing.
FALLBACK_CHAIN: dict[Gesture, tuple[Gesture, ...]] = {
    Gesture.FACEPALM:     (Gesture.SHAKE_HEAD, Gesture.LOOK_AWAY),
    Gesture.THUMBS_UP:    (Gesture.NOD,),
    Gesture.SALUTE:       (Gesture.NOD, Gesture.BOW),
    Gesture.COUNT_OFF:    (Gesture.EXPLAIN, Gesture.PRESENT),
    Gesture.EXPLAIN:      (Gesture.PRESENT, Gesture.NOD),
    Gesture.PRESENT:      (Gesture.POINT, Gesture.LOOK_AT_USER),
    Gesture.POINT:        (Gesture.TURN, Gesture.LOOK_AWAY),
    Gesture.THINK_POSE:   (Gesture.TILT_HEAD, Gesture.LOOK_AWAY),
    Gesture.CROSS_ARMS:   (Gesture.LEAN_BACK,),
    Gesture.TYPE:         (Gesture.LOOK_AWAY,),
    Gesture.SHRUG:        (Gesture.TILT_HEAD,),
    Gesture.BOW:          (Gesture.NOD,),
    Gesture.STRETCH:      (Gesture.LEAN_BACK,),
    Gesture.SIGH:         (Gesture.LEAN_BACK, Gesture.BLINK_SLOW),
    Gesture.WAVE:         (Gesture.NOD,),
    Gesture.WALK:         (Gesture.TURN,),
    Gesture.STEP_ASIDE:   (Gesture.TURN,),
    Gesture.STAND:        (Gesture.LEAN_BACK,),
    Gesture.SIT:          (Gesture.LEAN_BACK,),
    Gesture.TURN:         (Gesture.LOOK_AWAY,),
    Gesture.LOOK_AROUND:  (Gesture.LOOK_AWAY,),
    Gesture.LEAN_IN:      (Gesture.LOOK_AT_USER,),
    Gesture.LEAN_BACK:    (Gesture.IDLE,),
    Gesture.BLINK_SLOW:   (Gesture.IDLE,),
    Gesture.TILT_HEAD:    (Gesture.LOOK_AT_USER,),
    Gesture.SHAKE_HEAD:   (Gesture.LOOK_AT_USER,),
    Gesture.NOD:          (Gesture.LOOK_AT_USER,),
    Gesture.LOOK_AWAY:    (Gesture.IDLE,),
    Gesture.LOOK_AT_USER: (Gesture.IDLE,),
    Gesture.IDLE:         (),
}

#: What `avatar/js/gestures.js` can perform with arithmetic alone — no clip, no
#: download, on any body that has the bones the gesture needs.
#:
#: This is the reason a freshly cloned checkout is not mute. It is also the one
#: list here that is a *statement about other code*: `presence/selftest.py`
#: parses the JavaScript and fails if the two disagree, because a gesture
#: claimed here and missing there is a gesture that silently does nothing, and
#: a gesture implemented there and missing here is an animation nothing will
#: ever ask for.
PROCEDURAL_GESTURES: frozenset[str] = frozenset({
    "idle", "look_at_user", "look_away", "look_around", "nod", "shake_head",
    "tilt_head", "blink_slow", "lean_in", "lean_back", "sigh", "shrug",
    "turn", "bow", "stretch", "think",
})


#: What a gesture needs from the rig. Read by `catalog.py` when deciding whether
#: a model that declares no arms can be offered `WAVE` at all.
class RigPart(str, Enum):
    HEAD  = "head"
    TORSO = "torso"
    ARMS  = "arms"
    LEGS  = "legs"


GESTURE_REQUIRES: dict[Gesture, RigPart] = {
    **{g: RigPart.HEAD for g in (
        Gesture.IDLE, Gesture.LOOK_AT_USER, Gesture.LOOK_AWAY, Gesture.LOOK_AROUND,
        Gesture.NOD, Gesture.SHAKE_HEAD, Gesture.TILT_HEAD, Gesture.BLINK_SLOW,
    )},
    **{g: RigPart.TORSO for g in (
        Gesture.LEAN_IN, Gesture.LEAN_BACK, Gesture.SIGH, Gesture.SHRUG,
        Gesture.TURN, Gesture.BOW, Gesture.STRETCH,
    )},
    **{g: RigPart.ARMS for g in (
        Gesture.WAVE, Gesture.POINT, Gesture.PRESENT, Gesture.EXPLAIN,
        Gesture.THINK_POSE, Gesture.THUMBS_UP, Gesture.CROSS_ARMS,
        Gesture.FACEPALM, Gesture.SALUTE, Gesture.TYPE, Gesture.COUNT_OFF,
    )},
    **{g: RigPart.LEGS for g in (
        Gesture.STAND, Gesture.SIT, Gesture.WALK, Gesture.STEP_ASIDE,
    )},
}


@dataclass(frozen=True)
class Directive:
    """What JARVIS asked for, before anything checked whether it is possible.

    This is the LLM's side of the contract and nothing else: five fields, all
    optional, all in words. It is kept apart from `Performance` so that a
    malformed, hallucinated or half-empty directive is a *value* the director
    can reason about and log, not an exception thrown at parse time.

    `reason` is not decoration. It is the one field that makes a wrong face
    debuggable: without it, "why did it look concerned" has no answer that does
    not involve re-running the conversation.
    """
    expression: Expression = Expression.NEUTRAL
    intensity: float = 0.5
    gesture: Gesture = Gesture.IDLE
    gaze: Gaze = Gaze.USER
    posture: Posture | None = None
    reason: str = ""

    #: L'etat interieur, quand JARVIS l'a exprime plutot que de nommer un
    #: visage. Quand il est present il GAGNE sur les quatre champs ci-dessus,
    #: qui sont alors derives — voir `presence/affect.py`.
    #:
    #: Les deux formes coexistent parce qu'elles servent a deux choses : nommer
    #: un visage est plus sur quand JARVIS veut exactement celui-la, decrire un
    #: etat est plus riche et se prete a l'idle. Aucune n'est obligatoire.
    affect: object | None = None


@dataclass(frozen=True)
class Performance:
    """One frame of behaviour, fully resolved. What actually crosses the wire.

    Everything here is performable: the gesture is installed, the blendshapes
    are named for the mesh that is loaded, the intensity is clamped. A renderer
    receiving this never has to ask a question or make a choice — which is what
    keeps the same object valid for a three.js canvas, a WebView on a phone, and
    the 2D core that has no face at all.

    `requested_gesture` survives alongside `gesture` on purpose. When the two
    differ, the debug window can say *"asked for facepalm, played shake_head,
    facepalm not installed"* — the single most useful line there is when adding
    new clips.
    """
    expression: Expression = Expression.NEUTRAL
    intensity: float = 0.0
    gesture: Gesture = Gesture.IDLE
    gaze: Gaze = Gaze.USER
    posture: Posture = Posture.RELAXED

    #: 0..1, the voice being heard. Drives the mouth. Same number as
    #: `Snapshot.speaker_level` in the desktop client, deliberately: a second
    #: loudness measurement would drift against the first.
    speech_level: float = 0.0

    #: Resolved ARKit weights, name -> 0..1. Only the non-zero ones are carried;
    #: a renderer relaxes everything it is not told about.
    blendshapes: dict[str, float] = field(default_factory=dict)

    #: Seconds to hold before drifting back to the resting face. 0 means "until
    #: something else arrives" — used for postures and for SLEEPING.
    hold_s: float = 0.0

    # ── les parametres continus, derives de l'affect ─────────────────────────
    #
    # Ce ne sont pas des decorations : ce sont eux qui font qu'entre deux
    # decisions le personnage reste vivant et RESSEMBLE a son etat. Un JARVIS
    # calme et un JARVIS presse jouent le meme `nod` — mais pas a la meme
    # vitesse, et pas sur le meme fond d'immobilite.

    #: Vitesse des gestes, 1.0 = nominal.
    tempo: float = 1.0
    #: Immobilite du repos, 0 = agite, 1 = statue.
    stillness: float = 0.7
    #: Combien de temps le regard tient avant de deriver, en secondes.
    gaze_hold_s: float = 4.0

    #: L'etat interieur qui a produit tout ce qui precede, quand il y en avait
    #: un. Transporte pour le labo et la fenetre de debug : sans lui, "pourquoi
    #: ce visage" n'a pas de reponse qui ne demande pas de rejouer la
    #: conversation.
    affect: object | None = None

    #: What was asked for, when the catalogue could not honour it.
    requested_gesture: Gesture | None = None
    reason: str = ""

    def as_json(self) -> dict:
        """The wire form. Flat, short keys stay long — this travels once per
        decision, not once per frame, so readability beats a few bytes."""
        payload = {
            "expression":   self.expression.value,
            "intensity":    round(self.intensity, 3),
            "gesture":      self.gesture.value,
            "gaze":         self.gaze.value,
            "posture":      self.posture.value,
            "speech_level": round(self.speech_level, 3),
            "hold_s":       round(self.hold_s, 2),
            "tempo":        round(self.tempo, 3),
            "stillness":    round(self.stillness, 3),
            "gaze_hold_s":  round(self.gaze_hold_s, 2),
            "blendshapes":  {k: round(v, 3) for k, v in self.blendshapes.items()},
        }
        if self.affect is not None:
            payload["affect"] = self.affect.as_json()
        if self.requested_gesture is not None and self.requested_gesture is not self.gesture:
            payload["requested_gesture"] = self.requested_gesture.value
        if self.reason:
            payload["reason"] = self.reason[:200]
        return payload
