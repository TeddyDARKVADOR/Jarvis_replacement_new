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
    Intent,
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
    agreed exactly (`avatar/checks/affect_parity.py`). Getting there is why
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
    separement, sur un rig construit en memoire : `avatar/checks/adapter_contract.py`,
    douze verifications, parce que ce test-ci ne peut pas executer de
    JavaScript.
    """
    #: Les seules questions que le moteur a le droit de poser a un corps.
    #: `visemes` dit si la bouche a ses propres formes de parole (et lesquelles),
    #: `head` si un os tourne vraiment quand on hoche — la difference entre une
    #: capacite reelle et une capacite supposee.
    expected = {"expression", "gaze", "gazeBy", "lipsync", "visemes", "head",
                "gesture", "posture"}

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
    engine = ["rig.js", "gestures.js", "idle.js", "lipsync.js", "main.js",
              "engine.js", "performance.js", "gaze.js", "accents.js", "states.js",
              "stage.js", "director.js", "catalog.js", "recorder.js", "lab.js"]
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
    """Four tables the lab mirrors from Python, all checked — in `catalog.js`.

    The lab has to decide, with no Python running, what a gesture becomes on
    the body that is loaded. It used to decide with a rule of its own —
    "requested, or idle" — and showed `wave -> idle` where the panel nodded,
    because it had no `FALLBACK_CHAIN`. The tables now live in one module,
    `catalog.js`, shared by the lab, the director mirror and the engine, and
    the lab holds none of its own.
    """
    source = (AVATAR_DIR / "js" / "catalog.js").read_text(encoding="utf-8")
    lab = (AVATAR_DIR / "js" / "lab.js").read_text(encoding="utf-8")
    assert "const GESTURES = [" not in lab and "GESTURE_NEEDS = {" not in lab, (
        "lab.js a de nouveau ses propres tables de gestes — une seconde verite")

    def array(marker, end):
        block = source[source.index(marker):source.index(end)]
        return re.findall(r"'([a-z_]+)'", block)

    known = {g.value for g in Gesture}

    listed = array("export const GESTURES = [", "/** Ce qu'un corps joue sans aucun clip")
    unknown = [n for n in listed if n not in known]
    assert not unknown, "gestes inconnus dans le labo : " + ", ".join(unknown)
    missing = sorted(known - set(listed))
    assert not missing, "gestes absents du labo : " + ", ".join(missing)

    procedural = set(array("export const PROCEDURAL_GESTURES = new Set([",
                           "/** Ce que chaque geste demande au rig"))
    assert procedural == set(PROCEDURAL_GESTURES), (
        "repertoire procedural different : "
        f"labo seul {sorted(procedural - set(PROCEDURAL_GESTURES))}, "
        f"python seul {sorted(set(PROCEDURAL_GESTURES) - procedural)}")

    needs_block = source[source.index("export const GESTURE_NEEDS = {"):
                         source.index("/** Ou un geste se rabat")]
    needs = dict(re.findall(r"(\w+): '(\w+)'", needs_block))
    expected_needs = {g.value: part.value for g, part in GESTURE_REQUIRES.items()}
    assert needs == expected_needs, (
        "exigences de rig differentes : "
        + ", ".join(k for k in expected_needs if needs.get(k) != expected_needs[k]))

    chain_block = source[source.index("export const FALLBACK_CHAIN = {"):
                         source.index("/** Ce que chaque `rig.motion`")]
    chains = {name: tuple(re.findall(r"'(\w+)'", body))
              for name, body in re.findall(r"^  (\w+): \[([^\]]*)\],", chain_block, re.MULTILINE)}
    expected_chains = {g.value: tuple(c.value for c in chain)
                       for g, chain in FALLBACK_CHAIN.items()}
    assert chains == expected_chains, (
        "chaine de repli differente : "
        + ", ".join(k for k in expected_chains if chains.get(k) != expected_chains[k]))

    return (f"{len(listed)} gestes, {len(procedural)} procedurals, "
            f"{len(needs)} exigences, {len(chains)} chaines de repli — accordes")


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


# ── 8. le mode visage : le corps est la, on ne le bouge pas ──────────────────


@check("le mode visage retrecit le vocabulaire a la tete")
def _face_mode_vocabulary():
    """`rig.motion: "face"` doit couter les gestes du corps, et rien d'autre.

    Le point a prouver n'est pas que la liste raccourcit — c'est qu'elle
    raccourcit a la BONNE chose. Un mode visage qui retirerait `nod` ou `idle`
    laisserait JARVIS sans aucun mouvement, et un qui laisserait passer `shrug`
    ferait bouger un buste qu'on a decide de tenir tranquille.
    """
    from presence.catalog import _parse

    raw = json.loads((AVATAR_DIR / "manifest.json").read_text(encoding="utf-8"))
    raw["rig"] = dict(raw.get("rig") or {})
    raw["rig"]["parts"] = ["arms", "head", "legs", "torso"]
    gesture_dir = AVATAR_DIR / "gestures"

    raw["rig"]["motion"] = "full"
    full = _parse(raw, gesture_dir)
    raw["rig"]["motion"] = "face"
    face = _parse(raw, gesture_dir)

    assert full.motion == "full" and not full.frozen, "le mode complet fige quelque chose"
    assert face.motion == "face"
    assert face.parts == frozenset({RigPart.HEAD}), f"pilote {face.parts}"
    assert face.frozen == frozenset({RigPart.TORSO, RigPart.ARMS, RigPart.LEGS}), (
        "les membres figes ne sont pas ceux qu'on croit — et c'est cette liste "
        "que le labo affiche pour expliquer pourquoi `wave` ne joue pas")

    offered = set(face.vocabulary)
    assert Gesture.IDLE in offered and Gesture.NOD in offered, (
        "le mode visage a retire des mouvements de tete — JARVIS n'aurait plus "
        "rien du tout")
    for gone in (Gesture.WAVE, Gesture.EXPLAIN, Gesture.SHRUG, Gesture.LEAN_IN,
                 Gesture.THINK_POSE, Gesture.TURN):
        assert gone not in offered, f"{gone.value} est encore offert en mode visage"
    for kept in offered:
        assert GESTURE_REQUIRES.get(kept, RigPart.HEAD) is RigPart.HEAD, (
            f"{kept.value} demande autre chose qu'une tete")

    # Un mot inconnu vaut "full" : un manifeste ecrit avant ce champ, ou avec
    # une faute de frappe, ne doit pas perdre son corps en silence.
    raw["rig"]["motion"] = "visage"
    assert _parse(raw, gesture_dir).motion == "full", "un mot inconnu fige le corps"
    raw["rig"].pop("motion")
    assert _parse(raw, gesture_dir).motion == "full", "l'absence du champ fige le corps"

    return (f"{len(full.vocabulary)} gestes -> {len(offered)}, tous de tete ; "
            f"champ absent ou inconnu = complet")


@check("aucun geste ne devient injouable, il devient un autre")
def _face_mode_fallback():
    """Le corps fige ne doit jamais produire un `None` ni un trou.

    C'est la propriete qui rend le mode visage sans danger : JARVIS peut
    continuer a vouloir `facepalm` — le vocabulaire ne le lui propose plus, mais
    une directive ecrite a la main, un client plus vieux ou un modele qui
    improvise le demanderont quand meme. Chaque chaine doit alors terminer sur
    un mouvement de tete, en gardant l'intention.
    """
    face = _cat({RigPart.HEAD}, set())

    for gesture in Gesture:
        landed = face.resolve(gesture)
        assert face.can(landed), f"{gesture.value} atterrit sur {landed.value}, injouable"
        assert GESTURE_REQUIRES.get(landed, RigPart.HEAD) is RigPart.HEAD, (
            f"{gesture.value} -> {landed.value}, qui n'est pas un mouvement de tete")

    # Et l'intention survit a la substitution, ce qui est toute la difference
    # entre « degrade » et « perdu ».
    assert face.resolve(Gesture.FACEPALM) is Gesture.SHAKE_HEAD, "le non a disparu"
    assert face.resolve(Gesture.WAVE) is Gesture.NOD, "le salut a disparu"

    director = Director(face)
    director.set_intent(Directive(expression=Expression.AMUSED, gesture=Gesture.WAVE),
                        now=0.0)
    performance = director.resolve("ACTIVE", now=0.1)
    assert performance.gesture is Gesture.NOD
    assert performance.requested_gesture == Gesture.WAVE.value, (
        "la demande d'origine n'est plus lisible — c'est la ligne qui dit quel "
        "clip vaut le coup d'etre installe ensuite")
    return f"{len(Gesture)} gestes, tous atterrissent sur une tete, demande gardee"


@check("les deux cotes JS lisent le mode, pas seulement Python")
def _face_mode_js():
    """Le gel a deux moities en JS, et aucune ne suffit seule.

    `gestures.js` remet les canaux du buste et des hanches a zero — c'est ce qui
    tient le corps quoi qu'ecrive la posture, le regard ou le repos. `lab.js`,
    lui, doit filtrer ce qu'il annonce comme jouable : sans ca, le labo dirait
    « ✓ geste shrug » pendant que le moteur remet le buste a zero a l'image
    suivante — un labo qui montre un mouvement que le panneau ne fera pas, ce
    qui est la seule chose qu'un labo n'a pas le droit de faire.

    Lu dans la source plutot qu'execute, parce que ce fichier tourne sans
    navigateur. La mesure, elle, est dans `avatar/checks/face_first.py`.
    """
    gestures = (AVATAR_DIR / "js" / "gestures.js").read_text(encoding="utf-8")
    lab = (AVATAR_DIR / "js" / "lab.js").read_text(encoding="utf-8")

    assert "faceOnly" in gestures, "gestures.js ne connait pas le mode visage"
    assert "BODY_CHANNELS" in gestures, "aucune liste de canaux a geler"
    # `rootRy` porte le report du poids. L'oublier gelerait les jambes a moitie,
    # et c'est exactement la cle qui manquait a l'accumulateur.
    for channel in ("spineRx", "spineRy", "rootY", "rootZ", "rootRy"):
        assert channel in gestures.split("BODY_CHANNELS", 1)[1][:260], (
            f"{channel} n'est pas dans les canaux geles")

    catalog_js = (AVATAR_DIR / "js" / "catalog.js").read_text(encoding="utf-8")
    assert "face: ['head']" in catalog_js and "export function motionOf" in catalog_js, (
        "catalog.js ne restreint plus le mode visage a la tete")
    assert "motionOf(" in lab and "new Catalogue(" in lab, (
        "lab.js ne resout plus contre le catalogue du corps charge — il "
        "annoncerait jouable un geste que le moteur remet a zero")
    # Et le moteur DIT qu'il a gele un geste, au lieu de l'annoncer joue.
    assert "via: 'frozen'" in gestures, (
        "gestures.js ne rapporte plus un geste gele : le labo l'afficherait joue")

    # Et le mot du manifeste est le meme des trois cotes.
    manifest = json.loads((AVATAR_DIR / "manifest.json").read_text(encoding="utf-8"))
    motion = (manifest.get("rig") or {}).get("motion", "full")
    return f"gestures.js + lab.js accordes ; manifeste livre en \"{motion}\""


# ── 9. les intentions : pourquoi, avant comment ──────────────────────────────


@check("intentions identiques en JS")
def _js_intents():
    """`avatar/js/affect.js` porte la meme table, et le labo en depend.

    Meme mecanisme que pour les douze ancres, et pour la meme raison : une copie
    que personne ne verifie derive, et une derive ici ferait montrer au labo un
    comportement que le panneau ne jouera pas.
    """
    from presence.affect import _INTENTS

    source = (AVATAR_DIR / "js" / "affect.js").read_text(encoding="utf-8")
    block = source[source.index("export const INTENTS = {"):]
    rows = re.findall(
        r"^  (\w+)\s*: \[([^\]]+)\],", block, re.MULTILINE)
    assert rows, "table INTENTS introuvable dans affect.js"

    found = {}
    for name, raw in rows:
        parts = [p.strip() for p in raw.split(",")]
        numbers = tuple(float(p) for p in parts[:5])
        gesture = parts[5].strip("'")
        gaze = None if parts[6] == "null" else parts[6].strip("'")
        found[name] = (numbers, gesture, gaze)

    expected = {
        intent.value: (row[:5], row[5].value, row[6].value if row[6] else None)
        for intent, row in _INTENTS.items()
    }
    assert found == expected, (
        "intentions differentes : "
        + ", ".join(sorted(set(found) ^ set(expected))
                    or [k for k in expected if found.get(k) != expected[k]]))
    return f"{len(expected)} intentions, identiques"


@check("deux intentions ne produisent jamais le meme comportement")
def _intents_distinct():
    """La regle qui garde la liste courte, verifiee sur ce qui JOUE.

    Une table de seize lignes toutes differentes ne prouve rien : ce qui compte
    est que les seize se distinguent une fois jouees par le corps qu'on a. Deux
    intentions qui atterrissent sur le meme visage, le meme regard, le meme
    mouvement ET le meme accent ne sont pas deux intentions, ce sont deux noms —
    et le modele apprendra a en choisir une au hasard.

    CE QUE LA VERSION PRECEDENTE NE VOYAIT PAS
        Elle comparait le geste DEMANDE. En mode visage, le mode livre, le geste
        est rabattu avant de jouer : `wave` et `thumbs_up` deviennent tous deux
        `nod`, et `greet` et `report_success` etaient le meme comportement a la
        virgule pres (happy 0.68, user, nod). Idem `farewell`/`agree` et
        `acknowledge`/`explain` — un visage neutre n'ecrit aucune forme. Le test
        passait, les intentions etaient indiscernables.

    Il est donc joue sur les deux corps : tete seule, et corps complet avec tous
    les clips. Deux visages identiques dont l'intensite differe de moins de
    0.15 comptent comme un seul — personne ne distingue 0.37 de 0.48 sur un
    panneau de 300 pixels.
    """
    face_cat = _cat({RigPart.HEAD}, set())
    full_cat = _cat({RigPart.HEAD, RigPart.TORSO, RigPart.ARMS, RigPart.LEGS},
                    set(Gesture))

    def played(cat: Catalogue) -> dict[Intent, Performance]:
        out = {}
        for intent in Intent:
            director = Director(cat)
            director.set_intent(parse(json.dumps({"intent": intent.value})), now=0.0)
            out[intent] = director.resolve("SPEAKING", now=0.1)
        return out

    closest = None
    for label, cat in (("visage", face_cat), ("complet", full_cat)):
        rows = played(cat)
        intents = list(rows)
        for i, a in enumerate(intents):
            for b in intents[i + 1:]:
                p, q = rows[a], rows[b]
                same = (p.expression is q.expression and p.gaze is q.gaze
                        and p.gesture is q.gesture and p.accent == q.accent)
                gap = abs(p.intensity - q.intensity)
                assert not (same and gap < 0.15), (
                    f"[{label}] {a.value} et {b.value} jouent le meme comportement : "
                    f"{p.expression.value} {p.intensity:.2f}/{q.intensity:.2f} · "
                    f"{p.gaze.value} · {p.gesture.value} · accent "
                    f"{p.accent.value if p.accent else '-'} — c'est une intention "
                    f"de trop, ou il lui manque un signal que ce corps peut jouer")
                if same and (closest is None or gap < closest[0]):
                    closest = (gap, f"{a.value}/{b.value} [{label}]")

    faces = {p.expression for p in played(face_cat).values()}
    tail = (f" ; paire la plus proche {closest[1]} a {closest[0]:.2f}"
            if closest else "")
    return (f"{len(Intent)} intentions distinctes en mode visage et complet, "
            f"sur {len(faces)} visages{tail}")


@check("une nouvelle decision rejoue son geste, une ancienne jamais")
def _gesture_identity():
    """`gesture_id` : ce qui distingue une decision d'une re-resolution.

    Le moteur ne rejouait un geste que si son NOM changeait. Or en mode visage
    le reflexe SPEAKING se rabat sur `nod`, et sept intentions aussi : pendant
    la parole — c'est-a-dire quand JARVIS appelle l'outil — `agree` arrivait
    sous le meme nom que ce qui venait de jouer, et le hochement etait avale.
    """
    director = Director(_cat({RigPart.HEAD}, set()))
    speaking = director.resolve("SPEAKING", now=0.0)
    director.set_intent(parse('{"intent": "agree"}'), now=1.0)
    agree = director.resolve("SPEAKING", now=1.0)
    assert speaking.gesture is agree.gesture is Gesture.NOD, "le cas teste a change"
    assert agree.gesture_id != speaking.gesture_id, (
        "agree porte le meme identifiant que le reflexe : son hochement sera avale")

    # La meme intention, re-resolue a un changement d'etat : un seul hochement.
    again = director.resolve("LISTENING", now=3.0)
    assert again.gesture_id == agree.gesture_id, (
        "une re-resolution change l'identifiant : le geste rejouerait a chaque etat")

    # Deux intentions successives au meme geste : deux hochements.
    director.set_intent(parse('{"intent": "acknowledge"}'), now=5.0)
    second = director.resolve("SPEAKING", now=5.0)
    assert second.gesture is Gesture.NOD and second.gesture_id != agree.gesture_id, (
        "deux decisions successives partagent un identifiant")

    # Et l'intention expiree rend la main au reflexe, qui a le sien.
    expired = director.resolve("SPEAKING", now=5.0 + INTENT_TTL_S + 1)
    assert expired.gesture_id == "reflex:SPEAKING", expired.gesture_id

    assert "gesture_id" in second.as_json(), "l'identifiant ne part pas sur le fil"
    return (f"reflexe {speaking.gesture_id} -> {agree.gesture_id} -> "
            f"{second.gesture_id}, re-resolution stable")


@check("ce que JARVIS nomme a cote d'une intention gagne sur ce qu'elle derive")
def _explicit_refines_intent():
    """« L'intention pose la base, l'explicite corrige, champ par champ. »

    Ca ne tenait que pour le regard. `{"intent": "agree", "expression":
    "proud"}` jouait `amused` : le visage NOMME etait remplace par le visage
    DERIVE de l'intention, parce qu'un visage absent et un visage choisi
    avaient la meme valeur par defaut. Meme chose pour une posture explicite.
    """
    cat = _cat({RigPart.HEAD}, set())

    def play(text):
        director = Director(cat)
        director.set_intent(parse(text), now=0.0)
        return director.resolve("SPEAKING", now=0.1)

    named = play('{"intent": "agree", "expression": "proud"}')
    assert named.expression is Expression.PROUD, f"visage nomme ignore : {named.expression.value}"
    assert named.intent is Intent.AGREE and named.gesture is Gesture.NOD, "l'intention a ete perdue"
    derived = play('{"intent": "agree"}')
    assert abs(named.intensity - derived.intensity) < 1e-9, (
        "sans intensite nommee, c'est celle de l'etat qui doit rester")
    strong = play('{"intent": "warn", "expression": "serious", "intensity": 0.9}')
    assert strong.expression is Expression.SERIOUS and abs(strong.intensity - 0.9) < 1e-9
    posture = play('{"intent": "warn", "posture": "relaxed"}')
    assert posture.posture is Posture.RELAXED, f"posture nommee ignoree : {posture.posture.value}"
    # Et une intention sans rien de nomme garde son visage derive.
    assert derived.expression is Expression.AMUSED
    return "visage, intensite et posture nommes gagnent ; le reste vient de l'intention"


@check("hold_s appartient au visage reflexe, pas aux decisions")
def _hold_belongs_to_reflex():
    """WAKING tient une surprise 1.2 s ; une intention recue pendant WAKING non.

    `hold_s` etait transmis quel que soit le visage. Depuis que le moteur le lit
    (`avatar/js/performance.js`), une intention arrivee pendant le reveil
    relachait son visage au bout de 1.2 s au lieu de le tenir.
    """
    director = Director(_cat({RigPart.HEAD}, set()))
    assert director.resolve("WAKING", now=0.0).hold_s == 1.2
    director.set_intent(parse('{"intent": "warn"}'), now=0.0)
    assert director.resolve("WAKING", now=0.1).hold_s == 0.0, (
        "une intention pendant WAKING herite des 1.2 s du reflexe")

    director = Director(_cat({RigPart.HEAD}, set()))
    director.set_affect(Affect(valence=-0.5, arousal=0.7), now=0.0)
    assert director.resolve("ERROR", now=0.1).hold_s == 0.0, (
        "un visage d'affect herite du hold_s du reflexe ERROR")
    return "reflexe WAKING 1.2 s ; intention et affect : tenus jusqu'a la suite"


@check("le regard dit qui l'a choisi, et un mot inconnu ne vole pas celui de l'intention")
def _gaze_provenance():
    """`gaze_source` : explicite, intention, affect, reflexe, surete.

    Le moteur s'en sert pour une regle : un regard DECIDE n'est jamais deplace
    par un comportement de fond. Et un mot de regard invalide ne doit plus
    bloquer celui de l'intention — `investigate` perdait son ecran pour
    `"gaze": "monitor"`.
    """
    cat = _cat({RigPart.HEAD}, set())

    def source_of(text, state="SPEAKING"):
        director = Director(cat)
        director.set_intent(parse(text), now=0.0)
        return director.resolve(state, now=0.1)

    explicit = source_of('{"intent": "investigate", "gaze": "user"}')
    assert (explicit.gaze, explicit.gaze_source) == (Gaze.USER, "explicit"), explicit
    by_intent = source_of('{"intent": "investigate"}')
    assert (by_intent.gaze, by_intent.gaze_source) == (Gaze.SCREEN, "intent")
    junk = source_of('{"intent": "investigate", "gaze": "monitor"}')
    assert junk.gaze is Gaze.SCREEN, (
        f"un mot de regard inconnu a vole le regard de l'intention ({junk.gaze.value})")
    asleep = source_of('{"intent": "investigate", "gaze": "user"}', state="SLEEPING")
    assert (asleep.gaze, asleep.gaze_source) == (Gaze.CLOSED, "safety")

    director = Director(cat)
    assert director.resolve("THINKING", now=0.0).gaze_source == "reflex"
    director.set_affect(Affect(attention=0.2), now=0.0)
    assert director.resolve("THINKING", now=0.1).gaze_source == "affect"

    payload = explicit.as_json()
    assert payload["gaze_source"] == "explicit" and payload["state"] == "SPEAKING"
    return "explicit · intent · affect · reflex · safety, tous sur le fil"


@check("une intention traverse toute la chaine, jusqu'au JSON")
def _intent_chain():
    """La regle tiree de `rootRy`, rendue executable.

    Une capacite declaree doit etre calculee, accumulee, ecrite, visible ET
    verifiee — sinon on obtient une fonctionnalite morte que sa propre
    documentation decrit comme vivante. Le report du poids l'a ete pendant des
    semaines.

    Ce controle suit donc un mot, du texte brut jusqu'au JSON qui part sur le
    fil, et refuse qu'une etape le laisse tomber.
    """
    directive = parse('{"intent": "investigate", "reason": "le log"}')
    assert directive is not None, "1. lu : le parseur a jete une intention seule"
    assert directive.intent is Intent.INVESTIGATE, "1. lu : mauvais mot"

    assert directive.affect is not None, "2. calcule : aucun etat interieur"
    assert directive.gesture is Gesture.TURN, (
        f"2. calcule : geste {directive.gesture.value}, pas celui de l'intention")
    assert directive.gaze is Gaze.SCREEN, (
        "2. calcule : le regard de l'intention a ete perdu — `gaze_for` ne rend "
        "jamais `screen`, donc personne d'autre ne peut le remettre")

    director = Director(_cat({RigPart.HEAD}, set()))
    director.set_intent(directive, now=0.0)
    performance = director.resolve("ACTIVE", now=0.1)

    assert performance.expression is Expression.THINKING, (
        f"3. derive : visage {performance.expression.value}")
    assert performance.gaze is Gaze.SCREEN, "3. derive : regard perdu au directeur"
    assert performance.gesture is Gesture.LOOK_AWAY, (
        f"4. rabattu : {performance.gesture.value} au lieu d'un mouvement de tete")
    assert performance.requested_gesture is Gesture.TURN, (
        "4. rabattu : la demande d'origine n'est plus lisible")
    assert performance.intent is Intent.INVESTIGATE, (
        "5. visible : l'intention n'a pas survecu jusqu'a la Performance")

    payload = performance.as_json()
    assert payload["intent"] == "investigate", (
        "6. transmis : le mot n'est pas dans le JSON qui part sur le fil — "
        "c'est exactement la panne de `rootRy`, une etape plus loin")

    # Et il expire avec l'intention qu'il nomme.
    assert director.resolve("ACTIVE", now=INTENT_TTL_S + 1).intent is None, (
        "7. borne : le mot survit a l'intention")

    return "lu -> calcule -> derive -> rabattu -> visible -> transmis -> expire"


# ── 10. le temps d'un visage ─────────────────────────────────────────────────


@check("chaque visage a une signature temporelle, et elle vise de vraies formes")
def _facial_signatures():
    """`avatar/js/performance.js` decide QUAND chaque partie du visage part.

    Deux pannes muettes a garder fermees, et les deux se lisent ici :

      * une expression SANS entree dans la table arrive d'un bloc, et personne
        ne le remarque — c'est exactement l'etat d'avant ce fichier ;
      * un groupe mal orthographie (`mouths`, `brows`) ne correspond a aucun
        prefixe ARKit, donc son decalage ne s'applique a rien. La ligne existe,
        elle se lit bien, et elle ne fait rien.

    Le temps lui-meme se mesure dans le navigateur —
    `avatar/checks/facial_performance.py` chronometre les coefficients
    reellement ecrits. Ce controle-ci garde la table.
    """
    source = (AVATAR_DIR / "js" / "performance.js").read_text(encoding="utf-8")
    block = source[source.index("export const SIGNATURES = {"):source.index("MAX_DELAY_S =")]

    signatures: dict[str, dict[str, float]] = {}
    for name, body in re.findall(r"^  (\w+):\s*\{([^}]*)\},", block, re.MULTILINE):
        signatures[name] = {
            group: float(value)
            for group, value in re.findall(r"(\w+):\s*(-?[0-9.]+)", body)
        }
    assert signatures, "table SIGNATURES introuvable"

    missing = sorted(e.value for e in Expression if e.value not in signatures)
    assert not missing, (
        f"sans signature, donc arrivant d'un bloc : {missing}")

    ceiling = float(re.search(r"MAX_DELAY_S = ([0-9.]+)", source).group(1))

    # Les groupes reels, lus dans les formes que les expressions ecrivent
    # vraiment. Une liste en dur ici serait une deuxieme table a garder juste.
    real = set()
    for expression in Expression:
        for shape in face(expression, 0.8, Gaze.USER):
            real.add(re.match(r"[a-z]+", shape).group(0))

    for name, groups in signatures.items():
        for group, delay in groups.items():
            assert group in real, (
                f"{name}.{group} ne correspond a aucun prefixe ARKit "
                f"({sorted(real)}) — ce decalage ne s'applique a rien")
            assert 0.0 <= delay <= ceiling, (
                f"{name}.{group} = {delay}, hors de [0, {ceiling}]")

    # Ce qui fait qu'un visage SE COMPOSE est son retard le plus long, pas la
    # somme de ses retards : un groupe non nomme part a zero, donc l'ecart
    # visible est simplement max(delais). Sommer recompenserait une signature
    # qui nomme beaucoup de groupes pour rien.
    spread = {name: max(groups.values(), default=0.0)
              for name, groups in signatures.items()}

    # Une surprise qui se compose n'est pas une surprise. C'est le seul visage
    # dont le « snap » est juste, et la table doit le dire.
    composed = [n for n, v in spread.items() if n not in ("neutral", "surprised")]
    assert spread["surprised"] < min(spread[n] for n in composed), (
        f"surprised ({spread['surprised']:.2f} s) n'arrive pas plus vite que "
        f"tous les autres — c'est pourtant le seul qui doit arriver d'un bloc")

    # Et l'ironie doit trainer : sans le retard de la bouche, `amused` se lit
    # comme de la joie, ce qui est le contresens le plus courant du lot.
    assert spread["amused"] >= 0.10, (
        f"amused ne compose plus ({spread['amused']:.2f} s) — la bouche doit "
        "arriver apres les yeux, c'est ce qui le distingue de happy")

    slowest = max(spread, key=lambda n: spread[n])
    return (f"{len(signatures)} signatures, {len(real)} groupes reels, "
            f"surprised {spread['surprised']:.2f} s -> {slowest} "
            f"{spread[slowest]:.2f} s")



# ── 11. le moteur et son miroir ──────────────────────────────────────────────


@check("les accents existent des deux cotes, et visent de vraies formes")
def _js_accents():
    """`Accent` (Python) et `ACCENTS` (accents.js), `_INTENT_ACCENTS` et son miroir.

    Un accent nomme en Python et absent du JS est un salut qui ne joue rien —
    la panne exacte que cette couche existe pour corriger.
    """
    from presence.affect import _INTENT_ACCENTS
    from presence.model import Accent

    source = (AVATAR_DIR / "js" / "accents.js").read_text(encoding="utf-8")
    block = source[source.index("export const ACCENTS = {"):source.index("export class Accents")]
    names = set(re.findall(r"^  (\w+): \{", block, re.MULTILINE))
    assert names == {a.value for a in Accent}, (
        f"accents JS {sorted(names)} vs Python {sorted(a.value for a in Accent)}")
    for shape in re.findall(r"(\w+): [0-9.]+", "".join(re.findall(r"shapes: \{([^}]*)\}", block))):
        assert shape in ARKIT_52, f"accent : {shape} n'est pas une forme ARKit"

    affect_js = (AVATAR_DIR / "js" / "affect.js").read_text(encoding="utf-8")
    table = affect_js[affect_js.index("export const INTENT_ACCENTS = {"):]
    table = table[:table.index("};")]
    mirrored = dict(re.findall(r"^  (\w+): '(\w+)',", table, re.MULTILINE))
    expected = {i.value: a.value for i, a in _INTENT_ACCENTS.items()}
    assert mirrored == expected, f"accents d'intention : JS {mirrored} vs Python {expected}"
    return f"{len(names)} accents, {len(expected)} intentions accentuees, formes ARKit"


@check("le directeur du labo porte les tables du panneau")
def _js_director_tables():
    """`avatar/js/director.js` refait `resolve()` pour le labo. Tables comparees.

    Le comportement, lui, est confronte en marche par
    `avatar/checks/director_parity.py` (Node) : meme directive, meme JSON.
    """
    from presence.director import _POSTURE_OF, _REFLEX, _REFLEX_AFFECT

    source = (AVATAR_DIR / "js" / "director.js").read_text(encoding="utf-8")
    assert f"INTENT_TTL_S = {INTENT_TTL_S}" in source, "INTENT_TTL_S differe"
    assert f"ANGRY_CEILING = {ANGRY_CEILING}" in source, "ANGRY_CEILING differe"

    block = source[source.index("export const REFLEX = {"):source.index("const DEFAULT_REFLEX")]
    rows = {}
    for word, body in re.findall(r"^  (\w+):\s*\[([^\]]+)\],", block, re.MULTILINE):
        parts = [x.strip().strip("'") for x in body.split(",")]
        rows[word] = (parts[0], float(parts[1]), parts[2], parts[3], parts[4], float(parts[5]))
    expected = {w: (e.value, i, g.value, z.value, p.value, h)
                for w, (e, i, g, z, p, h) in _REFLEX.items()}
    assert rows == expected, (
        "reflexe different : " + ", ".join(k for k in expected if rows.get(k) != expected[k]))

    block = source[source.index("export const REFLEX_AFFECT = {"):source.index("/** Miroir de `_POSTURE_OF`")]
    for word, affect in _REFLEX_AFFECT.items():
        body = re.search(rf"^  {word}:\s*\{{([^}}]*)\}}", block, re.MULTILINE)
        assert body, f"REFLEX_AFFECT.{word} absent de director.js"
        given = dict(re.findall(r"(\w+): (-?[0-9.]+)", body.group(1)))
        for axis in ("valence", "arousal", "attention", "confidence", "urgency"):
            want = getattr(affect, axis)
            got = float(given.get(axis, {"valence": 0.05, "arousal": 0.22, "attention": 0.80,
                                         "confidence": 0.72, "urgency": 0.0}[axis]))
            assert abs(got - want) < 1e-9, f"REFLEX_AFFECT.{word}.{axis} : {got} vs {want}"

    block = source[source.index("export const POSTURE_OF = {"):]
    block = block[:block.index("};")]
    postures = dict(re.findall(r"(\w+): '(\w+)'", block))
    assert postures == {e.value: p.value for e, p in _POSTURE_OF.items()}, "POSTURE_OF differe"
    return f"{len(rows)} reflexes, {len(postures)} postures, TTL et plafond accordes"


@check("chaque etat machine a un comportement de fond")
def _presence_states():
    """`avatar/js/states.js` : ce que fait un visage selon ce qu'il fait.

    Un mot d'etat inconnu de cette table retombe sur `idle` — donc un JARVIS
    qui parle clignerait comme un JARVIS au repos. Et les deux relations que
    la litterature fixe doivent tenir : on cligne plus en parlant qu'en
    ecoutant, et un regard qui ecoute ne s'echappe pas plus souvent qu'un
    regard qui parle.
    """
    from presence.director import _REFLEX

    source = (AVATAR_DIR / "js" / "states.js").read_text(encoding="utf-8")
    mapping = source[source.index("export const STATE_OF = {"):source.index("/**\n * Le comportement")]
    words = set(re.findall(r"^  (\w+): '", mapping, re.MULTILINE))
    missing = sorted(set(_REFLEX) - words)
    assert not missing, f"etats machine sans comportement de fond : {missing}"

    block = source[source.index("export const BEHAVIOURS = {"):source.index("/** Duree du fondu")]
    blink = {name: float(v) for name, v in re.findall(r"^  (\w+):\s+\{ blinkPerMin: ([0-9.]+)", block, re.MULTILINE)}
    for state in ("idle", "listening", "thinking", "speaking", "reacting",
                  "unavailable", "loading"):
        assert state in blink, f"etat {state} sans parametres"
    assert blink["speaking"] > blink["idle"] > blink["listening"], (
        f"clignements {blink} : parler > repos > ecouter est ce que la mesure humaine donne")
    return f"{len(words)} mots d'etat -> {len(blink)} comportements, parler {blink['speaking']:.0f}/min"


@check("chaque visage a un rythme, et la surprise est la plus breve")
def _facial_envelopes():
    """`ENVELOPES` (performance.js) : montee, maintien, relache, par visage.

    Sans ligne, un visage prend le rythme par defaut — l'ancien lissage, qui
    fait arriver une surprise a la vitesse d'une pensee.
    """
    source = (AVATAR_DIR / "js" / "performance.js").read_text(encoding="utf-8")
    block = source[source.index("export const ENVELOPES = {"):source.index("/** Le comportement d'avant")]
    envelopes = {}
    for name, body in re.findall(r"^  (\w+):\s*\{([^}]*)\},", block, re.MULTILINE):
        envelopes[name] = {k: float(v) for k, v in re.findall(r"(\w+): ([0-9.]+)", body)}
    missing = sorted(e.value for e in Expression if e.value not in envelopes)
    assert not missing, f"visages sans rythme : {missing}"
    for name, env in envelopes.items():
        assert 0.05 <= env["attack"] <= 1.5 and 0.1 <= env["release"] <= 2.0, (
            f"{name} : attaque {env['attack']} / relache {env['release']} hors bornes")
    fastest = min(envelopes, key=lambda n: envelopes[n]["attack"])
    assert fastest == "surprised", f"le visage le plus rapide est {fastest}, pas surprised"
    assert "hold" in envelopes["surprised"], "la surprise se tient indefiniment"
    assert envelopes["thinking"]["attack"] > envelopes["happy"]["attack"], (
        "la reflexion s'installe plus vite que la joie")
    assert envelopes["amused"]["release"] > envelopes["amused"]["attack"], (
        "l'ironie repart plus vite qu'elle n'arrive")
    return (f"{len(envelopes)} rythmes ; surprised {envelopes['surprised']['attack']} s "
            f"-> tenue {envelopes['surprised']['hold']} s ; thinking {envelopes['thinking']['attack']} s")


@check("le moteur ne tire son hasard que de sa graine")
def _seeded_engine():
    """Aucun `Math.random()` dans le moteur, hors `rng.js`.

    Un seul appel suffit a rendre une seance impossible a rejouer : les
    clignements divergent, et « pourquoi ce visage a 12:42:11 » n'a plus de
    reponse. `avatar/checks/engine_test.mjs` prouve le rejeu exact ; ceci
    empeche qu'on le casse sans s'en apercevoir.
    """
    engine = ["rig.js", "gestures.js", "idle.js", "lipsync.js", "engine.js",
              "performance.js", "gaze.js", "accents.js", "states.js", "recorder.js"]
    offenders = []
    for filename in engine:
        source = (AVATAR_DIR / "js" / filename).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.split("\n")
                         if not line.lstrip().startswith(("*", "//", "/*")))
        if "Math.random" in code:
            offenders.append(filename)
    assert not offenders, "hasard hors graine : " + ", ".join(offenders)
    return f"{len(engine)} fichiers moteur, tout le hasard passe par rng.js"


# ── 12. le visage est interchangeable ────────────────────────────────────────


@check("le modele actif est bien celui que decrit son profil")
def _active_profile():
    """Empreinte, licence, provenance, mesures : le profil dit-il encore vrai ?

    Un profil decrit un fichier precis. Remplace sous le meme nom, le fichier
    rendrait fausses la calibration et les capacites annoncees — sans qu'aucune
    erreur ne le dise, seulement un visage legerement faux.
    """
    from presence import models

    manifest = json.loads((AVATAR_DIR / "manifest.json").read_text(encoding="utf-8"))
    name = (manifest.get("model") or {}).get("file")
    if not name:
        return "corps procedural — aucun profil a verifier"
    path = AVATAR_DIR / "models" / name
    if not path.is_file():
        return f"skipped ({name} absent : modeles non versionnes)"
    ok, why = models.check_profile(path)
    assert ok, f"{name} : {why}"
    assert manifest.get("version") == models.MANIFEST_VERSION, "manifeste non migre"
    assert (manifest["model"].get("profile") ==
            models.relative(models.profile_path(path))), "le manifeste ne pointe pas son profil"
    return why


@check("changer de visage ne change aucune decision, et ne perd rien")
def _model_swap():
    """Feminin -> masculin -> feminin, dans un repertoire temporaire.

    Le test de qualite de l'architecture : un nouveau visage est une
    installation, pas un chantier. Detail dans `avatar/checks/model_swap.py`.
    """
    sys.path.insert(0, str(AVATAR_DIR / "checks"))
    try:
        import model_swap
    finally:
        sys.path.pop(0)
    ok, detail = model_swap.run()
    assert ok, detail
    return detail

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
