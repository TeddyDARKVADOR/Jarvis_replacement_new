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
  9. The three tables duplicated into JavaScript still match the Python ones —
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
    source = (AVATAR_DIR / "js" / "lab.js").read_text(encoding="utf-8")
    block = source[source.index("const GESTURES = ["):source.index("const FRAME_FRACTIONS")]
    names = re.findall(r"'([a-z_]+)'", block)
    known = {g.value for g in Gesture}
    unknown = [n for n in names if n not in known]
    assert not unknown, "gestes inconnus dans le labo : " + ", ".join(unknown)
    missing = sorted(known - set(names))
    assert not missing, "gestes absents du labo : " + ", ".join(missing)
    return f"{len(names)} gestes, exactement ceux de model.py"


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
