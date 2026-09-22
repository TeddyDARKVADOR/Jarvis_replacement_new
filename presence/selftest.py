"""
presence/selftest.py — proves the body before JARVIS, a browser or a GPU exist.

    python -m presence.selftest

Runs with no Gemini key, no microphone, no phone, no display, no WebEngine and
no network. Same shape as `context/selftest.py` and `server/selftest.py`
deliberately: one convention for "is this layer sound" across the project.

What it checks:

  1. The package imports nothing from the core — the V2 contract, enforced by
     the parser rather than by anyone remembering it.
  2. Every authored shape uses a real ARKit name, and every expression is
     distinguishable from every other at full intensity.
  3. Intensity is continuous and monotonic, and brows really do lead mouths.
  4. The fallback chain terminates, from every gesture, on every body.
  5. A bodiless install still performs, and a manifest full of garbage does not
     raise.
  6. The reflex covers every state the HUD can emit.
  7. Intent outranks reflex, expires, and never keeps a sleeping JARVIS's eyes
     open.
  8. Directive parsing survives prose, truncation and invented words.
  9. The affect space is consistent: every anchor reprojects to itself, the
     derivation is total, the register caps expression, and the state decays
     toward a baseline instead of expiring.
 10. The three layers rank correctly — affect beats reflex, a named face
     beats affect — and an affect-only directive is not erased by its own
     unset defaults.
 11. The tables duplicated into JavaScript still match the Python ones —
     parsed out of the .js files, the way context/selftest.py reads the Kotlin
     heartbeat.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from presence import catalog as catalog_mod            # noqa: E402
from presence.catalog import Catalogue                 # noqa: E402
from presence.affect import (                           # noqa: E402
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
from presence.director import (                        # noqa: E402
    ANGRY_CEILING,
    INTENT_TTL_S,
    Director,
    parse,
    prompt_fragment,
    strip,
)
from presence.model import (                           # noqa: E402
    FALLBACK_CHAIN,
    GESTURE_REQUIRES,
    PROCEDURAL_GESTURES,
    Directive,
    Expression,
    Gaze,
    Gesture,
    Posture,
    RigPart,
)
from presence.vocabulary import ARKIT_52, VISEMES, face, viseme  # noqa: E402

AVATAR_DIR = BASE_DIR / "avatar"
_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        try:
            detail = fn() or "ok"
            _results.append((name, True, str(detail)))
        except AssertionError as exc:
            _results.append((name, False, str(exc) or "assertion"))
        except Exception as exc:
            _results.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return wrap


# ── 1. isolation ─────────────────────────────────────────────────────────────

FORBIDDEN = {"main", "ui", "core", "dashboard", "actions", "server", "client_desktop"}


@check("isolation")
def _isolation():
    """The V2 contract: this package may be deleted without consequence."""
    offenders: list[str] = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.name == "selftest.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in FORBIDDEN:
                    offenders.append(f"{path.name} -> {root}")
    assert not offenders, "imports interdits : " + ", ".join(offenders)
    return f"{len(list(Path(__file__).parent.glob('*.py'))) - 1} fichiers, 0 import du coeur"


# ── 2. the faces ─────────────────────────────────────────────────────────────

@check("noms ARKit")
def _arkit_names():
    assert len(ARKIT_52) == 52, f"{len(ARKIT_52)} formes au lieu de 52"
    assert len(set(ARKIT_52)) == 52, "doublon dans ARKIT_52"
    known = set(ARKIT_52)

    bad: list[str] = []
    for expression in Expression:
        for shape in face(expression, 1.0):
            if shape not in known:
                bad.append(f"{expression.value}:{shape}")
    for gaze in Gaze:
        for shape in face(Expression.NEUTRAL, 0.0, gaze):
            if shape not in known:
                bad.append(f"gaze {gaze.value}:{shape}")
    for name, shape_set in VISEMES.items():
        for shape in shape_set:
            if shape not in known:
                bad.append(f"viseme {name}:{shape}")
    assert not bad, "formes inconnues : " + ", ".join(bad)
    return "52 formes, toutes les expressions/regards/visemes valides"


@check("visages distincts")
def _distinct():
    """Twelve expressions that a person can tell apart.

    Compared as vectors: two faces whose weights differ by less than 0.25 in
    total are two faces nobody can name the difference between, and would make
    one of the twelve words unusable by JARVIS.
    """
    vectors = {e: face(e, 1.0) for e in Expression}
    worst = (None, None, 99.0)
    for a in Expression:
        for b in Expression:
            if a.value >= b.value:
                continue
            keys = set(vectors[a]) | set(vectors[b])
            distance = sum(abs(vectors[a].get(k, 0) - vectors[b].get(k, 0)) for k in keys)
            if distance < worst[2]:
                worst = (a, b, distance)
    assert worst[2] >= 0.25, (
        f"{worst[0].value} et {worst[1].value} se ressemblent trop ({worst[2]:.2f})"
    )
    return f"paire la plus proche : {worst[0].value}/{worst[1].value} a {worst[2]:.2f}"


@check("intensite continue")
def _intensity():
    """Every weight rises with intensity and nothing exceeds 1.0."""
    for expression in Expression:
        if expression is Expression.NEUTRAL:
            continue
        previous: dict[str, float] = {}
        for step in range(0, 11):
            current = face(expression, step / 10.0)
            for shape, weight in current.items():
                assert weight <= 1.0, f"{expression.value}/{shape} = {weight} > 1"
                assert weight >= previous.get(shape, 0.0) - 1e-6, (
                    f"{expression.value}/{shape} redescend a {step / 10.0}"
                )
            previous = current
        assert face(expression, 0.0) == {}, f"{expression.value} bouge a intensite 0"
    return "12 expressions x 11 paliers, monotones et bornees"


@check("les sourcils devancent la bouche")
def _curve():
    """The one piece of craft in vocabulary.py, asserted.

    At low intensity a face must be mostly brow. If this ever inverts, every
    subtle expression becomes a faded grin and the intensity dial stops being
    worth having.
    """
    low = face(Expression.HAPPY, 0.25)
    brow = sum(w for s, w in low.items() if s.startswith(("brow", "eye", "cheek")))
    mouth = sum(w for s, w in low.items() if s.startswith(("mouth", "jaw")))
    assert brow > mouth, f"a 0.25 : sourcils/yeux {brow:.2f} <= bouche {mouth:.2f}"

    high = face(Expression.HAPPY, 1.0)
    mouth_high = sum(w for s, w in high.items() if s.startswith(("mouth", "jaw")))
    assert mouth_high > mouth * 2, "la bouche ne rattrape pas a pleine intensite"
    return f"a 0.25 : haut {brow:.2f} vs bas {mouth:.2f} — le haut mene"


@check("visemes")
def _visemes():
    silent = viseme("sil", 1.0)
    assert silent, "le silence ne ferme pas la bouche"
    assert viseme("AA", 1.0)["jawOpen"] > viseme("I", 1.0).get("jawOpen", 0), (
        "AA n'ouvre pas plus la bouche que I"
    )
    # Un viseme invente doit donner le silence, jamais une exception : lipsync.js
    # peut recevoir n'importe quoi d'une source externe.
    assert viseme("ZZZ", 1.0) == silent, "un viseme inconnu ne retombe pas sur sil"
    return f"{len(VISEMES)} visemes, repli sur sil"


# ── 3. the catalogue ─────────────────────────────────────────────────────────

def _cat(parts: set[RigPart], installed: set[Gesture], procedural: bool = False) -> Catalogue:
    return Catalogue(
        parts=frozenset(parts), installed=frozenset(installed),
        unknown=(), model="" if procedural else "x.glb", procedural=procedural,
    )


@check("le repli termine")
def _fallback_terminates():
    """Every gesture resolves, on four different bodies, without looping."""
    bodies = {
        "procedural": _cat({RigPart.HEAD, RigPart.TORSO}, set(), procedural=True),
        "buste nu": _cat({RigPart.HEAD}, set()),
        "humanoide sans clips": _cat(
            {RigPart.HEAD, RigPart.TORSO, RigPart.ARMS, RigPart.LEGS}, set()),
        "humanoide equipe": _cat(
            {RigPart.HEAD, RigPart.TORSO, RigPart.ARMS, RigPart.LEGS}, set(Gesture)),
    }
    for label, cat in bodies.items():
        for gesture in Gesture:
            resolved = cat.resolve(gesture)
            assert cat.can(resolved), f"{label}: {gesture.value} -> {resolved.value} injouable"
    assert bodies["humanoide equipe"].resolve(Gesture.FACEPALM) is Gesture.FACEPALM
    assert bodies["buste nu"].resolve(Gesture.FACEPALM) is Gesture.SHAKE_HEAD, (
        "un buste devrait encore savoir dire non"
    )
    assert bodies["procedural"].resolve(Gesture.WAVE) is Gesture.NOD
    return f"{len(Gesture)} gestes x {len(bodies)} corps, tous jouables"


@check("chaines completes")
def _chains_declared():
    missing = [g.value for g in Gesture if g not in FALLBACK_CHAIN]
    assert not missing, "sans chaine de repli : " + ", ".join(missing)
    unmapped = [g.value for g in Gesture if g not in GESTURE_REQUIRES]
    assert not unmapped, "sans partie de rig : " + ", ".join(unmapped)
    return f"{len(FALLBACK_CHAIN)} chaines, {len(GESTURE_REQUIRES)} exigences"


@check("corps nu expressif")
def _procedural_vocabulary():
    """With nothing installed, JARVIS must still have something to say."""
    cat = _cat({RigPart.HEAD, RigPart.TORSO}, set(), procedural=True)
    vocabulary = cat.vocabulary
    assert Gesture.NOD in vocabulary and Gesture.SHAKE_HEAD in vocabulary
    assert Gesture.WAVE not in vocabulary, "un corps sans bras ne devrait pas saluer"
    assert len(vocabulary) >= 10, f"seulement {len(vocabulary)} gestes sans aucun asset"
    return f"{len(vocabulary)} gestes disponibles sans un seul fichier"


@check("manifeste illisible")
def _manifest_garbage():
    """A broken manifest costs a plainer body, never a sentence."""
    import tempfile

    original = catalog_mod.MANIFEST
    try:
        for content in ('{"gestures": []}', "pas du json", '{"model": null}', ""):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as handle:
                handle.write(content)
                catalog_mod.MANIFEST = Path(handle.name)
            resolved = catalog_mod.catalogue(force=True)
            assert resolved.resolve(Gesture.FACEPALM) is not None
            Path(handle.name).unlink(missing_ok=True)
        catalog_mod.MANIFEST = BASE_DIR / "nexistepas" / "manifest.json"
        assert catalog_mod.catalogue(force=True).procedural is True
    finally:
        catalog_mod.MANIFEST = original
        catalog_mod.catalogue(force=True)
    return "4 manifestes casses + un absent, aucune exception"


@check("manifeste livre")
def _shipped_manifest():
    raw = json.loads((AVATAR_DIR / "manifest.json").read_text(encoding="utf-8"))
    cat = catalog_mod.catalogue(force=True)
    unknown = cat.unknown
    assert not unknown, "gestes inconnus dans le manifeste : " + ", ".join(unknown)
    for name in raw.get("gestures", {}):
        Gesture(name)   # leve si le nom n'est pas du vocabulaire
    return (f"modele={raw['model']['file'] or 'procedural'}, "
            f"{len(cat.vocabulary)} gestes offerts a JARVIS")


# ── 4. the director ──────────────────────────────────────────────────────────

@check("le reflexe couvre le HUD")
def _reflex_coverage():
    """Every word `server/headless_ui.set_state` can emit must have a face.

    Read out of main.py rather than listed here: a state added there and not
    here is a state JARVIS meets with a blank face, and nobody would notice.
    """
    director = Director(_cat({RigPart.HEAD, RigPart.TORSO}, set(), procedural=True))
    words = set()
    source = (BASE_DIR / "main.py").read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r"set_state\(\s*[\"']([A-Za-z_]+)[\"']", source):
        words.add(match.group(1).upper())

    from presence.director import _REFLEX
    uncovered = sorted(w for w in words if w not in _REFLEX)
    for word in words | set(_REFLEX):
        performance = director.resolve(word)
        assert performance is not None
    detail = f"{len(_REFLEX)} etats reflexes, {len(words)} emis par main.py"
    if uncovered:
        detail += f" — sans reflexe dedie (repli neutre) : {', '.join(uncovered)}"
    return detail


@check("l'intention prime puis expire")
def _intent():
    director = Director(_cat(
        {RigPart.HEAD, RigPart.TORSO, RigPart.ARMS}, set(Gesture)))

    reflex = director.resolve("THINKING", now=100.0)
    assert reflex.expression is Expression.THINKING

    director.set_intent(
        Directive(expression=Expression.AMUSED, intensity=0.4,
                  gesture=Gesture.TILT_HEAD, gaze=Gaze.USER, reason="test"),
        now=100.0,
    )
    held = director.resolve("THINKING", now=100.0 + INTENT_TTL_S - 1)
    assert held.expression is Expression.AMUSED, "l'intention n'a pas prime"
    assert held.gesture is Gesture.TILT_HEAD
    assert held.posture is Posture.RELAXED, "amused devrait detendre la posture"

    expired = director.resolve("THINKING", now=100.0 + INTENT_TTL_S + 1)
    assert expired.expression is Expression.THINKING, "l'intention n'a pas expire"
    return f"prime {INTENT_TTL_S:.0f} s puis rend la main au reflexe"


@check("le sommeil ferme les yeux")
def _sleep_wins():
    """An intent taken before sleep must not leave JARVIS staring."""
    director = Director(_cat({RigPart.HEAD}, set()))
    director.set_intent(
        Directive(expression=Expression.HAPPY, intensity=1.0, gaze=Gaze.USER), now=0.0)
    performance = director.resolve("SLEEPING", now=1.0)
    assert performance.gaze is Gaze.CLOSED, "yeux ouverts en veille"
    assert performance.posture is Posture.DORMANT
    assert performance.blendshapes.get("eyeBlinkLeft", 0) >= 0.99
    return "l'etat SLEEPING l'emporte sur toute intention"


@check("la colere est plafonnee")
def _angry_capped():
    director = Director(_cat({RigPart.HEAD}, set()))
    director.set_intent(Directive(expression=Expression.ANGRY, intensity=1.0), now=0.0)
    performance = director.resolve("SPEAKING", now=0.5)
    assert performance.intensity <= ANGRY_CEILING + 1e-9, (
        f"colere a {performance.intensity}, plafond {ANGRY_CEILING}"
    )
    return f"plafonnee a {ANGRY_CEILING}"


@check("le geste demande est trace")
def _requested_kept():
    director = Director(_cat({RigPart.HEAD}, set()))
    director.set_intent(Directive(expression=Expression.AMUSED, gesture=Gesture.FACEPALM),
                        now=0.0)
    performance = director.resolve("SPEAKING", now=0.1)
    assert performance.gesture is Gesture.SHAKE_HEAD
    assert performance.requested_gesture is Gesture.FACEPALM
    assert performance.as_json()["requested_gesture"] == "facepalm", (
        "la substitution n'apparait pas sur le fil"
    )
    return "facepalm -> shake_head, la demande reste lisible"


@check("lecture des directives")
def _parsing():
    fenced = (
        "Voila le resultat.\n\n"
        '```jarvis-presence\n{"expression": "amused", "intensity": 0.35, '
        '"gesture": "tilt_head", "reason": "meme commande"}\n```'
    )
    directive = parse(fenced)
    assert directive is not None and directive.expression is Expression.AMUSED
    assert abs(directive.intensity - 0.35) < 1e-6
    assert strip(fenced) == "Voila le resultat.", "le bloc n'est pas retire du texte parle"

    bare = parse('reponse {"expression":"concerned","gesture":"nod"} fin')
    assert bare is not None and bare.expression is Expression.CONCERNED

    # Un mot invente ne doit pas faire echouer la directive entiere : on garde
    # ce qui est comprehensible, on ignore le reste.
    invented = parse('{"expression":"ecstatic","gesture":"nod","intensity":9}')
    assert invented is not None, "un geste valide aurait du suffire"
    assert invented.expression is Expression.NEUTRAL
    assert invented.intensity == 1.0, "l'intensite n'est pas bornee"

    for junk in ("", "aucune directive ici", "```json\n{}\n```", "{{{", None):
        assert parse(junk) is None, f"a cru voir une directive dans {junk!r}"
    return "bloc, objet nu, mots inventes, prose, vide"


@check("le prompt decrit le corps reel")
def _prompt():
    cat = _cat({RigPart.HEAD, RigPart.TORSO}, set(), procedural=True)
    text = prompt_fragment(cat)
    assert "wave" not in text, "propose de saluer a un corps sans bras"
    assert "nod" in text and "tilt_head" in text
    for expression in Expression:
        assert expression.value in text, f"{expression.value} absent du prompt"
    return f"{len(text)} caracteres, {len(cat.vocabulary)} gestes annonces"


@check("chaque ancre se retrouve elle-meme")
def _anchors_consistent():
    """The twelve anchors must be mutually consistent, not merely plausible.

    Each anchor is a point that is supposed to MEAN one expression. Project it
    back and you must get that expression — otherwise two anchors overlap, one
    of the twelve is unreachable, and JARVIS has a word he can never cash.
    That is invisible by inspection and obvious here.
    """
    from presence.affect import _ANCHORS

    wrong = []
    for expression, (valence, arousal, confidence, urgency) in _ANCHORS.items():
        affect = Affect(valence=valence, arousal=arousal,
                        confidence=confidence, urgency=urgency)
        got = expression_for(affect)
        if got is not expression:
            wrong.append(f"{expression.value} -> {got.value}")
    assert not wrong, "ancres qui se recouvrent : " + ", ".join(wrong)
    assert len(_ANCHORS) == len(Expression), (
        f"{len(_ANCHORS)} ancres pour {len(Expression)} expressions")
    return f"{len(_ANCHORS)} ancres, toutes atteignables"


@check("la derivation est totale")
def _derivation_total():
    """Nothing in the affect space may raise, or produce an invalid value.

    JARVIS writes these numbers. He will eventually write 1.5, or -3, or a
    string — and the answer to that has to be a plainer face, never a traceback
    on the path of a spoken sentence.
    """
    steps = [-1.0, -0.5, 0.0, 0.5, 1.0]
    levels = [0.0, 0.25, 0.5, 0.75, 1.0]
    count = 0
    for valence in steps:
        for arousal in levels:
            for attention in levels:
                for confidence in levels:
                    for urgency in (0.0, 0.5, 1.0):
                        affect = Affect(valence=valence, arousal=arousal,
                                        attention=attention, confidence=confidence,
                                        urgency=urgency)
                        assert isinstance(expression_for(affect), Expression)
                        assert isinstance(gaze_for(affect), Gaze)
                        assert isinstance(posture_for(affect), Posture)
                        assert 0.0 <= intensity_for(affect) <= 1.0
                        assert 0.5 <= tempo_for(affect) <= 1.9
                        assert 0.0 <= stillness_for(affect) <= 1.0
                        count += 1

    # Les valeurs aberrantes sont ramenees, pas refusees.
    wild = Affect(valence=9, arousal=-4, attention="x", confidence=None, urgency=7)  # type: ignore[arg-type]
    assert wild.valence == 1.0 and wild.arousal == 0.0 and wild.urgency == 1.0
    assert wild.attention == 0.0 and wild.confidence == 0.0
    return f"{count} points de l'espace, plus les valeurs aberrantes"


@check("le registre plafonne l'expression")
def _social_ceiling():
    """A smile at 0.9 during a delete confirmation is not expressive, it is wrong."""
    from presence.affect import _CEILING

    delighted = dict(valence=0.95, arousal=0.95)
    intensities = {
        mode: intensity_for(Affect(social_mode=mode, **delighted))
        for mode in SocialMode
    }
    for mode, value in intensities.items():
        assert value <= _CEILING[mode] + 1e-9, f"{mode.value} depasse son plafond"
    assert intensities[SocialMode.FORMAL] < intensities[SocialMode.PROFESSIONAL] \
        < intensities[SocialMode.CASUAL] <= intensities[SocialMode.INTIMATE], (
        "les registres ne sont pas ordonnes")
    return " · ".join(f"{m.value[:4]} {v:.2f}" for m, v in intensities.items())


@check("l'affect retombe au lieu de s'eteindre")
def _decay():
    """An inner state that snapped back to neutral would read as a reboot."""
    from presence.affect import BASELINE, DECAY_HALF_LIFE_S

    upset = Affect(valence=-0.9, arousal=0.9, attention=0.2, confidence=0.2, urgency=0.9)

    half = upset.decayed(DECAY_HALF_LIFE_S)
    midpoint = BASELINE["valence"] + (upset.valence - BASELINE["valence"]) * 0.5
    assert abs(half.valence - midpoint) < 1e-6, "la demi-vie n'est pas respectee"

    # L'urgence retombe plus vite que le reste : une urgence qui traine n'en
    # etait pas une.
    assert half.urgency < upset.urgency * 0.5 + 1e-9, "l'urgence ne retombe pas plus vite"

    far = upset.decayed(DECAY_HALF_LIFE_S * 10)
    for key, target in BASELINE.items():
        assert abs(getattr(far, key) - target) < 0.02, f"{key} ne rejoint pas la base"
    assert far.social_mode is upset.social_mode, "le registre a decru — c'est une decision"
    assert upset.decayed(0) is upset, "une decroissance nulle alloue inutilement"
    return f"demi-vie {DECAY_HALF_LIFE_S:.0f} s, urgence deux fois plus vite"


@check("affect > reflexe, intention > affect")
def _layer_order():
    """The three layers, in the order that makes JARVIS able to contradict himself.

    Without the last step he could not be serious in the middle of a good mood —
    which is most of what "an irreversible action just came up" looks like.
    """
    director = Director(_cat({RigPart.HEAD, RigPart.TORSO, RigPart.ARMS}, set(Gesture)))

    reflex = director.resolve("THINKING", now=100.0)
    assert reflex.expression is Expression.THINKING
    assert reflex.affect is not None, "meme le reflexe doit porter un affect nominal"

    director.set_affect(Affect(valence=0.7, arousal=0.65, attention=0.9,
                               confidence=0.85, social_mode=SocialMode.CASUAL),
                        now=100.0)
    derived = director.resolve("THINKING", now=100.0)
    assert derived.expression is not Expression.THINKING, "l'affect n'a pas prime"
    assert derived.expression in (Expression.HAPPY, Expression.PROUD, Expression.AMUSED)
    assert derived.reason.startswith("affect:"), derived.reason

    director.set_intent(Directive(expression=Expression.SERIOUS, intensity=0.8,
                                  gesture=Gesture.NOD, reason="action irreversible"),
                        now=100.0)
    named = director.resolve("THINKING", now=100.5)
    assert named.expression is Expression.SERIOUS, "l'intention n'a pas prime sur l'affect"
    assert named.reason == "action irreversible"

    # L'intention expire, l'affect reste (en decroissance).
    after = director.resolve("THINKING", now=100.0 + INTENT_TTL_S + 1)
    assert after.expression is not Expression.SERIOUS, "l'intention n'a pas expire"
    assert after.affect is not None
    return "reflexe -> affect -> intention, et l'intention seule expire"


@check("une intention d'affect n'est pas ecrasee par ses defauts")
def _affect_intent_survives():
    """The trap this guards is subtle and total.

    A directive that carried ONLY an affect still has `expression=NEUTRAL` and
    `intensity=0.5` sitting in its unset fields. Applied blindly after the
    affect derivation, those defaults erase exactly what the directive was
    expressing — and the symptom is "the affect form does nothing", with no
    error anywhere.
    """
    director = Director(_cat({RigPart.HEAD, RigPart.TORSO}, set()))
    directive = parse('{"emotion": {"valence": -0.7, "arousal": 0.3}, '
                      '"confidence": 0.5, "reason": "mauvaise nouvelle"}')
    assert directive is not None and directive.affect is not None
    assert directive.expression is Expression.NEUTRAL, "le defaut devrait etre neutral"

    director.set_intent(directive, now=0.0)
    performance = director.resolve("SPEAKING", now=0.5)
    assert performance.expression is Expression.SAD, (
        f"la forme affect a ete ecrasee : {performance.expression.value}")
    assert performance.reason == "mauvaise nouvelle"
    return "la raison est gardee, le visage vient bien de l'etat"


@check("l'etat faconne le repos")
def _continuous_params():
    """tempo, stillness and gaze_hold have to actually differ, or they are decoration."""
    calm = Affect(valence=0.1, arousal=0.10, attention=0.85, confidence=0.9)
    urgent = Affect(valence=-0.3, arousal=0.85, attention=0.95, confidence=0.6, urgency=0.9)

    assert tempo_for(urgent) > tempo_for(calm) * 1.3, "l'urgence n'accelere pas les gestes"
    assert stillness_for(calm) > stillness_for(urgent) + 0.25, (
        "le calme n'est pas plus immobile que l'agitation")

    distracted = Affect(attention=0.1)
    attentive = Affect(attention=0.98)
    assert gaze_hold_for(attentive) > gaze_hold_for(distracted) * 2, (
        "l'attention ne tient pas le regard plus longtemps")
    assert gaze_for(attentive) is Gaze.USER
    assert gaze_for(distracted) is not Gaze.USER

    # L'urgence ramene le regard, meme distrait : c'est ce que fait quelqu'un
    # qui vient de comprendre que ca compte.
    assert gaze_for(Affect(attention=0.1, urgency=0.8)) is Gaze.USER
    return (f"tempo {tempo_for(calm):.2f}->{tempo_for(urgent):.2f}, "
            f"immobilite {stillness_for(calm):.2f}->{stillness_for(urgent):.2f}")


@check("lecture de la forme affect")
def _parse_affect():
    full = parse('```jarvis-presence\n'
                 '{"emotion": {"valence": 0.45, "arousal": 0.25}, "attention": 0.91,\n'
                 ' "confidence": 0.76, "urgency": 0.12, "socialMode": "professional",\n'
                 ' "gesture": "tilt_head", "reason": "meme commande"}\n```')
    assert full is not None and full.affect is not None
    assert abs(full.affect.valence - 0.45) < 1e-6
    assert full.affect.social_mode is SocialMode.PROFESSIONAL
    assert full.gesture is Gesture.TILT_HEAD

    flat = parse('{"valence": -0.6, "arousal": 0.8, "urgency": 0.9}')
    assert flat is not None and flat.affect is not None
    assert abs(flat.affect.urgency - 0.9) < 1e-6

    word = parse('{"emotion": "amused", "intensity": 0.35}')
    assert word is not None and word.expression is Expression.AMUSED
    assert word.affect is None, "un mot seul ne doit pas inventer un etat"

    snake = parse('{"valence": 0.2, "social_mode": "formal"}')
    assert snake is not None and snake.affect.social_mode is SocialMode.FORMAL

    # Rien de reconnaissable : pas d'affect invente.
    for junk in ('{"emotion": "ecstatic"}', '{"mood": 0.4}', "{}", "prose"):
        directive = parse(junk)
        assert directive is None or directive.affect is None, (
            f"{junk!r} a produit un etat interieur invente")
    return "bloc, forme plate, mot seul, snake_case, et rien d'invente"


@check("le prompt enseigne les deux formes")
def _prompt_both_forms():
    cat = _cat({RigPart.HEAD, RigPart.TORSO}, set(), procedural=True)
    text = prompt_fragment(cat)
    for needed in ("valence", "arousal", "attention", "confidence", "urgency",
                   "socialMode", "expression", "gaze"):
        assert needed in text, f"{needed} absent du prompt"
    for mode in SocialMode:
        assert mode.value in text, f"registre {mode.value} absent"
    assert "wave" not in text, "propose de saluer a un corps sans bras"
    assert text.index("FORME 1") < text.index("FORME 2"), (
        "la forme affect doit etre presentee en premier")
    return f"{len(text)} caracteres, les deux formes, {len(cat.vocabulary)} gestes"

# ── 5. the two languages ─────────────────────────────────────────────────────

@check("ARKit identique en JS")
def _js_arkit():
    """`avatar/js/arkit.js` duplicates the 52. Parsed, not trusted."""
    source = (AVATAR_DIR / "js" / "arkit.js").read_text(encoding="utf-8")
    match = re.search(r"export const ARKIT_NAMES = \[(.*?)\];", source, re.DOTALL)
    assert match, "ARKIT_NAMES introuvable dans arkit.js"
    names = tuple(re.findall(r"'([A-Za-z]+)'", match.group(1)))
    assert names == ARKIT_52, (
        f"divergence JS/Python : {len(names)} vs {len(ARKIT_52)} noms, "
        f"ecart {set(names) ^ set(ARKIT_52)}"
    )
    return "52 noms, ordre compris, identiques"


@check("visemes identiques en JS")
def _js_visemes():
    source = (AVATAR_DIR / "js" / "visemes.js").read_text(encoding="utf-8")
    body = source[source.index("export const VISEMES"):]
    parsed: dict[str, dict[str, float]] = {}
    for name, block in re.findall(r"(\w+):\s*\{([^}]*)\}", body):
        shapes = {
            shape: float(weight)
            for shape, weight in re.findall(r"(\w+):\s*([0-9.]+)", block)
        }
        if shapes:
            parsed[name] = shapes
    assert set(parsed) == set(VISEMES), (
        f"visemes differents : JS {sorted(parsed)} vs Python {sorted(VISEMES)}"
    )
    for name, shapes in VISEMES.items():
        assert parsed[name] == shapes, f"le viseme {name} differe entre JS et Python"
    return f"{len(parsed)} visemes, poids identiques au centieme pres"


@check("le vocabulaire JS existe en Python")
def _js_gestures():
    """The two halves of the procedural repertoire, checked against each other.

    `model.PROCEDURAL_GESTURES` is a claim about a JavaScript file. A gesture
    claimed there and missing here is a gesture JARVIS is offered, picks, and
    performs as nothing. One implemented here and missing there is an animation
    nothing will ever ask for. Both are invisible without this check, which is
    why the claim is parsed rather than trusted.
    """
    source = (AVATAR_DIR / "js" / "gestures.js").read_text(encoding="utf-8")

    block = source[source.index("const PROCEDURAL = {"):source.index("/** Sustained offsets")]
    implemented = set(re.findall(r"^  (\w+):", block, re.MULTILINE))
    claimed = set(PROCEDURAL_GESTURES)

    unknown = sorted(n for n in implemented if n not in {g.value for g in Gesture})
    assert not unknown, "gestes JS inconnus de model.py : " + ", ".join(unknown)
    assert implemented == claimed, (
        "PROCEDURAL_GESTURES ment : "
        f"promis sans code {sorted(claimed - implemented)}, "
        f"code sans promesse {sorted(implemented - claimed)}"
    )

    postures_block = source[source.index("const POSTURES"):source.index("/** A small head turn")]
    postures = set(re.findall(r"^  (\w+):", postures_block, re.MULTILINE))
    assert postures == {p.value for p in Posture}, (
        f"postures JS {sorted(postures)} vs Python {sorted(p.value for p in Posture)}"
    )

    gaze_block = source[source.index("const GAZE_HEAD"):source.index("/** Ease in and out")]
    gazes = set(re.findall(r"^  (\w+):", gaze_block, re.MULTILINE))
    assert gazes == {g.value for g in Gaze}, (
        f"regards JS {sorted(gazes)} vs Python {sorted(g.value for g in Gaze)}"
    )
    return f"{len(implemented)} gestes, {len(postures)} postures, {len(gazes)} regards — accordes"


@check("affect identique en JS")
def _js_affect():
    """`avatar/js/affect.js` is generated from `affect.py`. Tables verified here.

    What this CANNOT do is run the JavaScript, so it checks the tables — anchors,
    weights, ceilings, baseline, and all 27 thresholds — and nothing about the
    shape of the functions.

    That gap was closed separately and deliberately: the two derivations were run
    side by side over 20 000 points of the affect space, 140 000 comparisons, and
    agreed exactly (`scratchpad/bench_affect_parity.py`). Getting there is why
    neither side rounds any more — rounding is a presentation concern, and having
    it inside the calculation made exact comparison impossible.
    """
    from presence.affect import BASELINE, DECAY_HALF_LIFE_S, TUNING, _ANCHORS, _CEILING, _WEIGHTS

    source = (AVATAR_DIR / "js" / "affect.js").read_text(encoding="utf-8")

    def table(name, end_marker):
        block = source[source.index(f"export const {name} = {{"):source.index(end_marker)]
        return {k: float(v) for k, v in re.findall(r"^  (\w+): (-?[0-9.]+),", block, re.MULTILINE)}

    ceiling = table("CEILING", "/** Vers quoi l'affect retombe")
    assert ceiling == {m.value: v for m, v in _CEILING.items()}, (
        f"plafonds differents : {ceiling}")

    baseline = table("BASELINE", "/** Demi-vie")
    assert baseline == {k: float(v) for k, v in BASELINE.items()}, (
        f"base differente : {baseline}")

    half = float(re.search(r"DECAY_HALF_LIFE_S = ([0-9.]+)", source).group(1))
    assert half == DECAY_HALF_LIFE_S, f"demi-vie {half} contre {DECAY_HALF_LIFE_S}"

    tuning = table("TUNING", "/**\n * Les douze ancres")
    assert tuning == {k: float(v) for k, v in TUNING.items()}, (
        "seuils differents : "
        + ", ".join(sorted(set(tuning) ^ set(TUNING))
                    or [k for k in TUNING if tuning.get(k) != TUNING[k]]))

    anchors_block = source[source.index("export const ANCHORS = {"):source.index("/** Ce que chaque axe pese")]
    anchors = {
        name: tuple(float(v) for v in values.split(","))
        for name, values in re.findall(r"^  (\w+): \[([^\]]+)\],", anchors_block, re.MULTILINE)
    }
    expected_anchors = {e.value: tuple(a) for e, a in _ANCHORS.items()}
    assert anchors == expected_anchors, (
        "ancres differentes : "
        + ", ".join(k for k in expected_anchors if anchors.get(k) != expected_anchors[k]))

    weights = tuple(float(v) for v in
                    re.search(r"WEIGHTS = \[([^\]]+)\]", source).group(1).split(","))
    assert weights == tuple(_WEIGHTS), f"poids {weights} contre {_WEIGHTS}"

    # L'arrondi ne doit jamais revenir dans la derivation : c'est ce qui rendait
    # les deux implementations incomparables.
    assert "roundTo" not in source and "Math.round" not in source, (
        "un arrondi est revenu dans affect.js — les deux derivations ne seront "
        "plus comparables a l'identique")

    return (f"{len(anchors)} ancres, {len(tuning)} seuils, {len(ceiling)} registres, "
            "aucun arrondi")


@check("le JS ne derive nulle part ailleurs")
def _no_stray_rounding():
    """Python's own derivation must stay unrounded too, for the same reason."""
    source = (Path(__file__).parent / "affect.py").read_text(encoding="utf-8")
    body = source[source.index("def intensity_for("):]
    assert "round(" not in body, (
        "un arrondi est revenu dans les derivations de affect.py")
    # Le fil, lui, arrondit — c'est le bon endroit.
    model = (Path(__file__).parent / "model.py").read_text(encoding="utf-8")
    assert '"tempo":        round(' in model, (
        "Performance.as_json n'arrondit plus : le fil devient bavard")
    return "derivations en pleine precision, arrondi au bord du fil seulement"

@check("le contrat de l'adaptateur tient")
def _adapter_contract():
    """Les trois corps repondent aux memes questions, dans le meme vocabulaire.

    C'EST LA COUTURE QUI REND LE MODELE INTERCHANGEABLE
        Le moteur comportemental demande « sais-tu regarder ? », jamais « as-tu
        un os LeftEye ? ». Un corps qui repondrait a des questions differentes
        obligerait l'appelant a savoir lequel il a — et ce jour-la, changer de
        modele cesse d'etre une ligne dans le manifeste.

        Le test lit les trois implementations et exige le meme jeu de cles.
        Ajouter une capacite est alors une modification de trois fichiers que le
        test impose, au lieu d'une divergence que personne ne voit.

    Ce que la traduction ARKit -> os fait reellement a l'execution est prouve
    separement, sur un rig construit en memoire : `scratchpad/bench_adapter.py`,
    douze verifications, parce que ce test-ci ne peut pas executer de
    JavaScript.
    """
    #: Les seules questions que le moteur a le droit de poser a un corps.
    expected = {"expression", "gaze", "gazeBy", "lipsync", "gesture", "posture"}

    bodies = {
        "body_gltf.js": "GltfBody",
        "body_vrm.js": "VrmBody",
        "body_procedural.js": "ProceduralBody",
    }
    found = {}
    for filename in bodies:
        source = (AVATAR_DIR / "js" / filename).read_text(encoding="utf-8")
        assert "capabilities()" in source, f"{filename} ne declare aucune capacite"
        block = source[source.index("  capabilities() {"):]
        block = block[:block.index("\n  }")]
        found[filename] = set(re.findall(r"^      (\w+):", block, re.MULTILINE))

    for filename, keys in found.items():
        assert keys == expected, (
            f"{filename} ({bodies[filename]}) repond a "
            f"{sorted(keys)} au lieu de {sorted(expected)}")

    # Le moteur ne doit jamais nommer un modele en particulier.
    engine = ["rig.js", "gestures.js", "idle.js", "lipsync.js", "main.js"]
    leaks = []
    for filename in engine:
        source = (AVATAR_DIR / "js" / filename).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.split("\n")
            if not line.lstrip().startswith(("*", "//", "/*"))
        )
        for token in ("Wolf3D", "readyplayer", "mixamorig", "VRMC_vrm", "Fcl_"):
            if token.lower() in code.lower():
                leaks.append(f"{filename}: {token}")
    assert not leaks, (
        "le moteur nomme un modele en particulier : " + ", ".join(leaks))

    return (f"{len(bodies)} corps, {len(expected)} capacites identiques, "
            f"{len(engine)} fichiers moteur sans nom de modele")


@check("le regard survit a un modele sans formes")
def _gaze_fallback_declared():
    """Ready Player Me pilote ses yeux par des os, pas par blendshapes.

    Sans traduction dans l'adaptateur, le regard de JARVIS ne bougerait que la
    tete sur ces modeles — pas d'erreur, pas de message, juste des yeux qui
    fixent droit devant. Ce controle verifie que le repli existe et que les
    formes gardent la priorite quand elles sont la : elles sont plus fines, et
    l'auteur du modele les a reglees lui-meme.
    """
    source = (AVATAR_DIR / "js" / "body_gltf.js").read_text(encoding="utf-8")

    assert "_setupGaze" in source and "_applyBoneGaze" in source, (
        "le repli du regard sur les os a disparu de l'adaptateur")
    assert "this.gazeByBone = !hasMorphs && hasBones" in source, (
        "les formes ne gagnent plus sur les os")
    # Les huit formes de regard doivent toutes etre interceptables.
    block = source[source.index("this.gaze = {"):]
    block = block[:block.index("};")]
    intercepted = set(re.findall(r"(eyeLook\w+):", block))
    expected = {f"eyeLook{d}{s}" for d in ("In", "Out", "Up", "Down")
                for s in ("Left", "Right")}
    assert intercepted == expected, (
        f"formes de regard manquantes : {sorted(expected - intercepted)}")
    # Le signe doit rester reglable : l'axe depend de l'orientation du rig.
    assert "rig.gaze" in source or "gazeSignY" in source, (
        "les signes du regard ne sont plus reglables depuis le manifeste")

    return f"{len(intercepted)} formes interceptables, formes prioritaires sur les os"

@check("le moteur 3D est local")
def _vendored():
    """A face that needs a CDN is a face missing on a bad network day."""
    required = [
        AVATAR_DIR / "vendor" / "three.module.min.js",
        AVATAR_DIR / "vendor" / "loaders" / "GLTFLoader.js",
        AVATAR_DIR / "vendor" / "utils" / "BufferGeometryUtils.js",
        AVATAR_DIR / "vendor" / "environments" / "RoomEnvironment.js",
        # Les trois compressions que les assets du commerce utilisent reellement.
        # Sans elles, un .glb achete echoue au chargement avec un message qui se
        # lit comme "fichier manquant".
        AVATAR_DIR / "vendor" / "loaders" / "KTX2Loader.js",
        AVATAR_DIR / "vendor" / "libs" / "basis" / "basis_transcoder.wasm",
        AVATAR_DIR / "vendor" / "loaders" / "DRACOLoader.js",
        AVATAR_DIR / "vendor" / "libs" / "draco" / "gltf" / "draco_decoder.wasm",
        AVATAR_DIR / "vendor" / "libs" / "meshopt_decoder.module.js",
        AVATAR_DIR / "vendor" / "three-vrm.module.min.js",
    ]
    missing = [str(p.relative_to(BASE_DIR)) for p in required if not p.is_file()]
    assert not missing, "moteur absent : " + ", ".join(missing)

    html = (AVATAR_DIR / "index.html").read_text(encoding="utf-8")
    assert "cdn.jsdelivr" not in html and "unpkg" not in html, (
        "index.html charge encore depuis un CDN"
    )
    assert './vendor/three.module.min.js' in html, "l'import map ne pointe pas sur le local"
    total = sum(p.stat().st_size for p in required)
    return f"{len(required)} fichiers, {total // 1024} Ko, aucun appel reseau"


@check("expressions identiques en JS")
def _js_expressions():
    """`avatar/js/expressions.js` is generated from `vocabulary.py`. Verified.

    The lab resolves expressions locally, with no Python running — that is the
    whole point of the lab. A drifted copy would mean tuning a face in the lab
    and shipping a different one.
    """
    from presence.vocabulary import _CURVE, _GAZE, _SHAPES

    source = (AVATAR_DIR / "js" / "expressions.js").read_text(encoding="utf-8")

    def table(start_marker, end_marker):
        block = source[source.index(start_marker):source.index(end_marker)]
        parsed = {}
        for name in re.findall(r"^  (\w+): \{\}", block, re.MULTILINE):
            parsed[name] = {}
        for name, body in re.findall(r"^  (\w+): \{\n(.*?)\n  \}", block,
                                     re.DOTALL | re.MULTILINE):
            parsed[name] = {
                shape: float(weight)
                for shape, weight in re.findall(r"(\w+): ([0-9.]+)", body)
            }
        return parsed

    shapes = table("const SHAPES = {", "/** The eyeLook* coefficients")
    gazes = table("const GAZE = {", "export const EXPRESSIONS")

    expected_shapes = {e.value: s for e, s in _SHAPES.items()}
    expected_gazes = {g.value: s for g, s in _GAZE.items()}

    assert set(shapes) == set(expected_shapes), (
        f"expressions JS {sorted(shapes)} vs Python {sorted(expected_shapes)}")
    for name, weights in expected_shapes.items():
        assert shapes[name] == weights, (
            f"l'expression {name} differe : JS {shapes[name]} vs Python {weights}")
    assert set(gazes) == set(expected_gazes), "regards differents entre JS et Python"
    for name, weights in expected_gazes.items():
        assert gazes[name] == weights, f"le regard {name} differe entre JS et Python"

    curve_block = source[source.index("const CURVE = {"):source.index("function family")]
    curve = {k: float(v) for k, v in re.findall(r"(\w+): ([0-9.]+)", curve_block)}
    assert curve == {k: float(v) for k, v in _CURVE.items()}, "la courbe d'intensite differe"

    return f"{len(shapes)} expressions, {len(gazes)} regards, {len(curve)} familles"


@check("le labo connait le meme vocabulaire")
def _lab_vocabulary():
    """Three tables the lab mirrors from Python, all checked.

    The lab has to decide, with no Python running, whether a gesture is playable
    on the body that is loaded — otherwise it shows a requested gesture as if it
    had played, which is exactly the silence its "execution" line exists to
    break. Doing that means mirroring the gesture list, the procedural
    repertoire and the rig requirements. Three more duplications, so three more
    checks.
    """
    source = (AVATAR_DIR / "js" / "lab.js").read_text(encoding="utf-8")

    def array(marker, end):
        block = source[source.index(marker):source.index(end)]
        return re.findall(r"'([a-z_]+)'", block)

    known = {g.value for g in Gesture}

    listed = array("const GESTURES = [", "/** Ce qu'un corps joue sans aucun clip")
    unknown = [n for n in listed if n not in known]
    assert not unknown, "gestes inconnus dans le labo : " + ", ".join(unknown)
    missing = sorted(known - set(listed))
    assert not missing, "gestes absents du labo : " + ", ".join(missing)

    procedural = set(array("const PROCEDURAL_GESTURES = new Set([",
                           "/** Ce que chaque geste demande au rig"))
    assert procedural == set(PROCEDURAL_GESTURES), (
        "repertoire procedural different : "
        f"labo seul {sorted(procedural - set(PROCEDURAL_GESTURES))}, "
        f"python seul {sorted(set(PROCEDURAL_GESTURES) - procedural)}")

    needs_block = source[source.index("const GESTURE_NEEDS = {"):
                         source.index("const FRAME_FRACTIONS")]
    needs = dict(re.findall(r"(\w+): '(\w+)'", needs_block))
    expected_needs = {g.value: part.value for g, part in GESTURE_REQUIRES.items()}
    assert needs == expected_needs, (
        "exigences de rig differentes : "
        + ", ".join(k for k in expected_needs if needs.get(k) != expected_needs[k]))

    return (f"{len(listed)} gestes, {len(procedural)} procedurals, "
            f"{len(needs)} exigences — accordes")


@check("noms : la meme regle des deux cotes")
def _normalisation():
    """The matcher IS the product, so it is tested on what actually breaks it.

    Each row below is a real convention from a real exporter. If Python and
    JavaScript ever disagree on one, a model resolves differently at install
    time and at run time: the inspector promises a blendshape the renderer
    cannot find, and the face is quietly flatter than the report claimed.
    """
    from presence.inspect import normalise

    cases = {
        "browDown_L": "browdownleft",
        "browDownLeft": "browdownleft",
        "brow_down_left": "browdownleft",
        "Brow Down Left": "browdownleft",
        "Wolf3D_Head.browInnerUp": "browinnerup",
        "mixamorig:LeftArm": "leftarm",
        "eyeBlink_R": "eyeblinkright",
        "eyeBlinkRight": "eyeblinkright",
        "mouthSmile.L": "mouthsmileleft",
        "jawOpen": "jawopen",
        "mouthRollLower": "mouthrolllower",
        "mouthLeft": "mouthleft",
        "tongueOut": "tongueout",
    }
    for raw, expected in cases.items():
        got = normalise(raw)
        assert got == expected, f"normalise({raw!r}) = {got!r}, attendu {expected!r}"

    source = (AVATAR_DIR / "js" / "body_gltf.js").read_text(encoding="utf-8")
    assert "SIDE_SEPARATED" in source and "SIDE_WORD" in source, (
        "les regles de cote ont disparu de body_gltf.js")
    assert source.index("SIDE_SEPARATED.exec") < source.index("SIDE_WORD.exec"), (
        "le JS teste le mot avant le separateur : l'ordre est inverse")
    assert "[^a-z0-9]" in source, "le JS ne retire plus les separateurs"
    return f"{len(cases)} conventions d'export, Python et JS accordes"


@check("le modele installe est pilotable")
def _installed_model():
    """Whatever is installed right now, read the way the renderer will read it."""
    from presence.inspect import report

    manifest = json.loads((AVATAR_DIR / "manifest.json").read_text(encoding="utf-8"))
    name = (manifest.get("model") or {}).get("file")
    if not name:
        return "aucun modele — corps procedural (python -m presence.install_model --demo)"

    path = AVATAR_DIR / "models" / name
    if not path.is_file():
        return f"skipped ({name} absent : modeles non versionnes, voir .gitignore)"

    data = report(path, (manifest.get("model") or {}).get("morphAliases"))
    vrm = data.get("vrm_facts") or {}
    if vrm:
        return (f"{name} : VRM, {len(vrm['expressions'])} expressions, "
                f"membres {', '.join(data['parts'])}")

    matched = len(data["arkit_matched"])
    assert matched >= 20, (
        f"{name} : seulement {matched}/52 formes ARKit atteignables — "
        "le visage serait presque fige")
    return (f"{name} : {matched}/52 ARKit, membres {', '.join(data['parts'])}, "
            f"{len(data['animations'])} animations")

# ── report ───────────────────────────────────────────────────────────────────

def main() -> int:
    width = max(len(n) for n, _, _ in _results)
    print(f"\n  presence/ selftest — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    for name, ok, detail in _results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n  {len(_results) - len(failed)}/{len(_results)} passed"
          + (f", FAILED: {', '.join(failed)}" if failed else "") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
