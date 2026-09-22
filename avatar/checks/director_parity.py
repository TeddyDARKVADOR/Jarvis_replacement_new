"""Le directeur du labo decide-t-il EXACTEMENT comme celui du panneau ?

    python avatar/checks/director_parity.py        (demande Node >= 18)

POURQUOI CE CONTROLE
    Le labo ne fait tourner aucun Python : il resout avec `avatar/js/director.js`,
    un miroir de `presence/director.py`. Un miroir qu'on ne confronte pas diverge,
    et un labo qui diverge montre un comportement que le produit n'a pas — ce
    qui est arrive trois fois avant ce fichier (repli, posture, colere).

    `presence/selftest.py` compare les TABLES. Ceci compare les DECISIONS : des
    centaines de directives — les seize intentions, leurs raffinements, les trois
    formes, du bruit — contre chaque etat machine, a deux instants (intention
    vivante, intention expiree), sur deux corps (tete seule, corps complet). Les
    deux cotes doivent produire le meme JSON, champ par champ.

    Les nombres sont compares au millieme : le fil arrondit au millieme, et
    `round()` de Python n'arrondit pas comme `Math.round`. Tout le reste —
    visage, geste, rabattement, regard et sa provenance, accent, identite du
    geste, formes presentes — doit etre identique.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from presence.catalog import Catalogue  # noqa: E402
from presence.director import Director, parse  # noqa: E402
from presence.model import Gesture, Intent, RigPart, Expression  # noqa: E402

STATES = ["ACTIVE", "LISTENING", "THINKING", "SPEAKING", "SLEEPING", "WAKING",
          "ERROR", "OFFLINE", "CONNECTING", "CONFIRM", "RECONNECTING"]

DIRECTIVES: list[dict | str | None] = [None]
for intent in Intent:
    DIRECTIVES.append({"intent": intent.value})
DIRECTIVES += [
    {"intent": "investigate", "gaze": "user"},
    {"intent": "investigate", "gaze": "monitor"},
    {"intent": "warn", "confidence": 0.2},
    {"intent": "greet", "socialMode": "formal"},
    {"intent": "apologise", "gesture": "shake_head"},
    {"intent": "agree", "head": "tilt_head"},
    {"intent": "think", "emotion": {"valence": 0.6}},
    {"intent": "explain", "valence": -0.4, "urgency": 0.9, "gaze": "screen"},
    {"intent": "agree", "expression": "proud"},
    {"intent": "warn", "expression": "serious", "intensity": 0.9},
    {"intent": "greet", "posture": "relaxed"},
    {"intent": "think", "emotion": "confused"},
]
for expression in Expression:
    DIRECTIVES.append({"expression": expression.value, "intensity": 0.8, "gesture": "nod"})
DIRECTIVES += [
    {"expression": "angry", "intensity": 1.0},
    {"expression": "happy", "intensity": 0.3, "posture": "formal", "gaze": "down"},
    {"emotion": "amused"},
    {"emotion": {"valence": 0.45, "arousal": 0.25}, "attention": 0.91, "confidence": 0.76},
    {"valence": -0.7, "arousal": 0.8, "urgency": 0.9, "social_mode": "casual"},
    {"attention": 0.1, "arousal": 0.05},
    {"gesture": "facepalm"},
    {"gesture": "wave", "reason": "bonjour"},
    {"invented": "thing"},
    "de la prose sans aucune directive",
    {},
]

TIMES = [0.1, 30.0]


def catalogue(motion: str) -> Catalogue:
    if motion == "face":
        return Catalogue(parts=frozenset({RigPart.HEAD}), installed=frozenset(), unknown=(),
                         model="x", procedural=False,
                         frozen=frozenset({RigPart.TORSO, RigPart.ARMS, RigPart.LEGS}),
                         motion="face")
    return Catalogue(parts=frozenset(RigPart), installed=frozenset(Gesture), unknown=(),
                     model="x", procedural=False, motion="full")


def python_side(cases):
    out = []
    for case in cases:
        director = Director(catalogue(case["motion"]))
        text = case["directive"]
        if text is not None:
            directive = parse(text if isinstance(text, str) else json.dumps(text))
            if directive is not None:
                director.set_intent(directive, now=0.0)
        out.append(director.resolve(case["state"], speech_level=0.3, now=case["t"]).as_json())
    return out


NODE = r"""
import { Director, parseDirective } from '%(director)s';
import { Catalogue, GESTURES } from '%(catalog)s';
let raw = '';
process.stdin.on('data', (c) => { raw += c; });
process.stdin.on('end', () => {
  const cases = JSON.parse(raw);
  const out = cases.map((c) => {
    const cat = c.motion === 'face'
      ? new Catalogue(['head', 'torso', 'arms', 'legs'], 'face', [])
      : new Catalogue(['head', 'torso', 'arms', 'legs'], 'full', GESTURES);
    const director = new Director(cat);
    if (c.directive !== null) {
      const d = parseDirective(c.directive);
      if (d) director.setIntent(d, 0);
    }
    return director.resolve(c.state, { speechLevel: 0.3, now: c.t });
  });
  process.stdout.write(JSON.stringify(out));
});
"""


def js_side(cases):
    node = shutil.which("node")
    if not node:
        print("  Node introuvable : ce controle a besoin de node >= 18.", flush=True)
        raise SystemExit(2)
    js = BASE / "avatar" / "js"
    script = NODE % {"director": (js / "director.js").as_uri(),
                     "catalog": (js / "catalog.js").as_uri()}
    run = subprocess.run([node, "--input-type=module", "-e", script],
                         input=json.dumps(cases), capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    if run.returncode != 0:
        print(run.stderr[-2000:])
        raise SystemExit(1)
    return json.loads(run.stdout)


def differences(py: dict, js: dict) -> list[str]:
    problems = []
    for key in sorted(set(py) | set(js)):
        a, b = py.get(key), js.get(key)
        if key == "blendshapes":
            names = set(a or {}) | set(b or {})
            for name in sorted(names):
                va, vb = (a or {}).get(name, 0.0), (b or {}).get(name, 0.0)
                if abs(va - vb) > 1.5e-3:
                    problems.append(f"blendshapes.{name}: py={va} js={vb}")
        elif key == "affect":
            for axis in sorted(set(a or {}) | set(b or {})):
                va, vb = (a or {}).get(axis), (b or {}).get(axis)
                if isinstance(va, float) and isinstance(vb, (int, float)):
                    if abs(va - vb) > 1.5e-3:
                        problems.append(f"affect.{axis}: py={va} js={vb}")
                elif va != vb:
                    problems.append(f"affect.{axis}: py={va!r} js={vb!r}")
        elif isinstance(a, float) or isinstance(b, float):
            # `hold_s` et `gaze_hold_s` partent au centieme, le reste au
            # millieme : l'ecart toleré est d'un pas d'arrondi du champ.
            step = 1.1e-2 if key in ("hold_s", "gaze_hold_s") else 1.5e-3
            if a is None or b is None or abs(float(a) - float(b)) > step:
                problems.append(f"{key}: py={a!r} js={b!r}")
        elif key == "reason" and a and b and a.startswith("affect:"):
            continue   # formatage des flottants : lu par un humain, pas par le moteur
        elif a != b:
            problems.append(f"{key}: py={a!r} js={b!r}")
    return problems


def main() -> int:
    cases = [{"motion": motion, "state": state, "directive": directive, "t": t}
             for motion in ("face", "full")
             for state in STATES
             for directive in DIRECTIVES
             for t in TIMES]
    py = python_side(cases)
    js = js_side(cases)
    assert len(py) == len(js)

    bad = 0
    for case, a, b in zip(cases, py, js):
        problems = differences(a, b)
        if problems:
            bad += 1
            if bad <= 8:
                print(f"  [FAUX] {case['motion']} {case['state']} t={case['t']} "
                      f"{json.dumps(case['directive'])[:60]}")
                for problem in problems[:5]:
                    print(f"         {problem}")
    fields = sum(len(a) for a in py)
    print(f"\n  {len(cases)} decisions ({fields} champs) : "
          f"{len(cases) - bad} identiques, {bad} differentes", flush=True)
    if bad:
        return 1
    print("  Le labo decide exactement comme le panneau.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
