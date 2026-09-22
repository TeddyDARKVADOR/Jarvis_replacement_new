"""Une intention suivie de bout en bout, a travers le VRAI code de chaque maillon.

    python avatar/checks/chain_test.py        (demande Node >= 18)

    set_presence(...)                plugins/presence.py      l'appel de fonction
      -> {"type":"avatar",...}       build_event / deliver    l'evenement
      -> trame JSON                  json.dumps               le websocket
      -> JarvisClient._handle_event  client_desktop/net.py    fraicheur, ordre
      -> JarvisStore                 client_desktop/state.py  l'enveloppe
      -> set_intent_json             ui/avatar_view.py        parse(), le MEME
      -> Director.resolve            presence/director.py     reflexe, affect, intention
      -> catalogue.resolve           presence/catalog.py      le repli
      -> Performance.as_json         le JSON pousse a la page
      -> AvatarEngine                avatar/js/engine.js      sous Node, corps sans rendu
      -> sortie effective            ce que le modele RECOIT

POURQUOI
    Chaque maillon a son test, et chacun passait pendant que la chaine etait
    coupee : le store emettait une directive nue, le widget cherchait une
    enveloppe, et aucune intention de JARVIS n'atteignait jamais le visage.
    Un test par maillon ne voit pas la couture entre deux maillons. Celui-ci
    ne regarde que les deux bouts : ce que JARVIS a appele, et ce que le corps
    a fait.

CE QUI EST VERIFIE, POUR CHAQUE CAS
    * le mot arrive dans le JSON, et le visage attendu est celui qui joue
    * le geste REELLEMENT joue (pas le geste demande), et qu'il n'est ni gele
      ni absent
    * en mode visage : rien ne bouge sous la nuque, meme quand l'intention
      demande un geste de corps (capacite impossible -> repli -> aucun faux
      mouvement)
    * les raffinements explicites gagnent : regard, tete, visage
    * l'accent, quand l'intention en a un, a joue
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop import config as config_mod  # noqa: E402
from client_desktop.net import JarvisClient  # noqa: E402
from client_desktop.state import JarvisStore  # noqa: E402
from client_desktop.ui import avatar_view  # noqa: E402
from plugins import presence as tool  # noqa: E402
from presence import Director, Intent  # noqa: E402
from presence.catalog import Catalogue, catalogue  # noqa: E402
from presence.model import Gesture, RigPart  # noqa: E402


class _Host:
    """Le cote serveur : ce que HeadlessUI.emit_event reçoit."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit_event(self, event: dict) -> None:
        self.events.append(event)


class _Widget:
    """L'etat du widget du panneau ; ses VRAIES methodes tournent dessus."""

    def __init__(self, cat: Catalogue) -> None:
        self._director = Director(cat)
        self._last_state = "SPEAKING"
        self._last_speech = 0.35
        self.pushed: list[dict] = []

    def _push(self, performance) -> None:  # noqa: ANN001
        self.pushed.append(performance.as_json())

    def set_intent(self, directive, age: float = 0.0) -> None:  # noqa: ANN001
        avatar_view.JarvisAvatarWidget.set_intent(self, directive, age)


def full_catalogue() -> Catalogue:
    return Catalogue(parts=frozenset(RigPart), installed=frozenset(), unknown=(),
                     model="x", procedural=False, motion="full")


def through_the_wire(arguments: dict, cat: Catalogue) -> dict | None:
    """Un appel d'outil, jusqu'au JSON que la page recevrait."""
    host = _Host()
    assert tool.run(arguments, player=host) == "ok"
    if not host.events:
        return None
    frame = json.dumps(host.events[-1])                 # ce qui passe sur /ws

    widget = _Widget(cat)
    store = JarvisStore()
    store.subscribe(lambda kind, payload: avatar_view.JarvisAvatarWidget.set_intent_json(
        widget, payload) if kind == "avatar" else None)
    client = JarvisClient(config_mod.Settings(host="h.ts.net", device_token="tok"), store,
                          speaker=None, on_connected=lambda: None,
                          on_disconnected=lambda _r, _f: None)
    client._handle_event(frame)
    return widget.pushed[-1] if widget.pushed else None


NODE = r"""
import { AvatarEngine } from '%(engine)s';
import { NullBody } from '%(body)s';
let raw = '';
process.stdin.on('data', (c) => { raw += c; });
process.stdin.on('end', () => {
  const out = JSON.parse(raw).map(({ perf, motion }) => {
    const body = new NullBody({ parts: ['head', 'torso', 'arms', 'legs'], visemes: 'oculus',
                                manifest: { rig: { motion } } });
    const e = new AvatarEngine(body, { seed: 7 });
    for (let i = 0; i < 20; i++) e.update(1 / 60);
    const decision = e.perform(perf);
    const peak = { body: 0, head: 0, brow: 0 };
    let eyes = { x: 0, y: 0 };
    for (let i = 0; i < 132; i++) {
      e.speak(Math.max(0, Math.sin(i * 0.15) * Math.sin(i * 0.04)) * 0.7);
      e.update(1 / 60);
      const s = e.gestures.smoothed;
      peak.body = Math.max(peak.body, Math.abs(s.spineRx), Math.abs(s.spineRy), Math.abs(s.rootY),
                           Math.abs(s.rootZ), Math.abs(s.rootRy));
      peak.head = Math.max(peak.head, Math.abs(s.headRx), Math.abs(s.headRy), Math.abs(s.headRz));
      if (i > 90) eyes = { x: e.rig.gazeCtl.out.x, y: e.rig.gazeCtl.out.y };
    }
    const bad = Object.values(body.morphs).filter((v) => !Number.isFinite(v) || v < 0 || v > 1).length;
    return { decision, peak, eyes, bad, visemes: Object.keys(body.visemes).length };
  });
  process.stdout.write(JSON.stringify(out));
});
"""


def engine_side(runs: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        print("  Node introuvable : ce controle a besoin de node >= 18.")
        raise SystemExit(2)
    js = BASE / "avatar" / "js"
    script = NODE % {"engine": (js / "engine.js").as_uri(), "body": (js / "body_null.js").as_uri()}
    run = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(runs),
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    if run.returncode != 0:
        print(run.stderr[-3000:])
        raise SystemExit(1)
    return json.loads(run.stdout)


def main() -> int:
    face_cat = catalogue(force=True)
    assert face_cat.motion == "face", "le manifeste livre n'est plus en mode visage"
    cats = {"face": face_cat, "full": full_catalogue()}

    cases: list[tuple[str, str, dict, dict]] = []
    for motion in ("face", "full"):
        for intent in Intent:
            cases.append((f"{intent.value}", motion, {"intent": intent.value}, {}))
    cases += [
        ("investigate + gaze user", "face", {"intent": "investigate", "gaze": "user"},
         {"gaze": "user", "gaze_source": "explicit"}),
        ("investigate + gaze screen", "face", {"intent": "investigate", "gaze": "screen"},
         {"gaze": "screen", "gaze_source": "explicit"}),
        ("warn + gaze user", "face", {"intent": "warn", "gaze": "user"},
         {"gaze": "user", "expression": "concerned"}),
        ("warn + gaze down", "face", {"intent": "warn", "gaze": "down"},
         {"gaze": "down", "gaze_source": "explicit"}),
        ("agree + head tilt_head", "face", {"intent": "agree", "gesture": "tilt_head"},
         {"gesture": "tilt_head"}),
        ("expression explicite serious", "face", {"expression": "serious", "intensity": 0.8},
         {"expression": "serious"}),
        ("intent + expression explicite", "face", {"intent": "agree", "expression": "proud"},
         {"expression": "proud", "intent": "agree", "gesture": "nod"}),
        ("intent + posture explicite", "face", {"intent": "warn", "posture": "relaxed"},
         {"posture": "relaxed", "expression": "concerned"}),
        ("greet, corps demande, visage seul", "face", {"intent": "greet"},
         {"requested_gesture": "wave", "gesture": "nod", "accent": "brow_flash"}),
        ("facepalm demande, visage seul", "face", {"gesture": "facepalm", "valence": -0.3},
         {"requested_gesture": "facepalm", "gesture": "shake_head"}),
        ("shrug demande, corps complet", "full", {"gesture": "shrug", "arousal": 0.5},
         {"gesture": "shrug"}),
    ]

    started = time.monotonic()
    perfs = []
    for label, motion, arguments, _ in cases:
        perf = through_the_wire(arguments, cats[motion])
        if perf is None:
            print(f"  [FAUX] {label} : rien n'est arrive au bout du fil")
            return 1
        perfs.append({"perf": perf, "motion": motion})
    python_ms = (time.monotonic() - started) * 1000
    outputs = engine_side(perfs)

    bad = 0
    for (label, motion, arguments, expect), wire, out in zip(cases, perfs, outputs):
        perf = wire["perf"]
        d = out["decision"]
        problems = []
        if "intent" in arguments and perf.get("intent") != arguments["intent"]:
            problems.append(f"intention perdue en route ({perf.get('intent')})")
        for key, value in expect.items():
            if perf.get(key) != value:
                problems.append(f"{key} = {perf.get(key)!r}, attendu {value!r}")
        if d["gesture"] != perf["gesture"]:
            problems.append(f"le moteur a recu {d['gesture']}, le fil disait {perf['gesture']}")
        if d["gesture_played"] not in ("procedural", "deja en cours"):
            problems.append(f"geste {perf['gesture']} : {d['gesture_played']}")
        if perf.get("accent") and not d["accent_played"]:
            problems.append(f"accent {perf['accent']} non joue")
        if motion == "face" and out["peak"]["body"] > 1e-3:
            problems.append(f"le corps a bouge en mode visage ({out['peak']['body']:.4f})")
        if perf["gesture"] not in ("idle", "blink_slow") and out["peak"]["head"] < 0.02:
            problems.append("la tete n'a pas bouge")
        if perf["gaze"] == "screen" and out["eyes"]["x"] < 0.3:
            problems.append(f"regard screen, yeux a {out['eyes']['x']:.2f}")
        if perf["gaze"] == "down" and out["eyes"]["y"] > -0.3:
            problems.append(f"regard down, yeux a {out['eyes']['y']:.2f}")
        if perf["gaze"] == "user" and abs(out["eyes"]["x"]) > 0.2:
            problems.append(f"regard user, yeux a {out['eyes']['x']:.2f}")
        if out["bad"]:
            problems.append(f"{out['bad']} valeurs invalides")
        if not out["visemes"]:
            problems.append("la parole n'a pas atteint la bouche")

        mark = "OK  " if not problems else "FAUX"
        fallback = (f"{perf['requested_gesture']} -> " if perf.get("requested_gesture") else "")
        print(f"  [{mark}] {motion:<4} {label[:34]:<34} {perf['expression']:<9} "
              f"{fallback}{perf['gesture']:<12} regard {perf['gaze']:<6} "
              f"tete {out['peak']['head'] * 57.3:4.1f}° corps {out['peak']['body'] * 57.3:4.1f}°"
              + (f" +{perf['accent']}" if perf.get("accent") else ""))
        for problem in problems:
            print(f"         {problem}")
            bad += 1

    print(f"\n  {len(cases)} chaines completes, {bad} probleme(s) "
          f"(cote Python {python_ms:.0f} ms pour tout le fil)")
    if not bad:
        print("  De l'appel d'outil au modele, chaque intention arrive et joue ce qu'elle doit.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
