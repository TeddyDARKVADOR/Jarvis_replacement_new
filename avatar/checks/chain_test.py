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


# ── le temps, sans changement d'etat ─────────────────────────────────────────
#
# Le chemin ci-dessus est celui d'UNE decision. Celui-ci est celui des secondes
# qui suivent : une Performance n'etait poussee qu'aux changements d'etat et a
# l'arrivee d'une intention, donc l'expiration (25 s) et la decroissance de
# l'affect ne se voyaient jamais tant que l'etat ne bougeait pas. Mesure avant
# correction : `warn`, puis une minute d'ecoute, et le visage restait inquiet
# la minute entiere.

TIMED_NODE = r"""
import { AvatarEngine } from '%(engine)s';
import { NullBody } from '%(body)s';
let raw = '';
process.stdin.on('data', (c) => { raw += c; });
process.stdin.on('end', () => {
  const { pushes, until, probes } = JSON.parse(raw);
  const body = new NullBody({ parts: ['head', 'torso', 'arms', 'legs'], visemes: 'oculus',
                              manifest: { rig: { motion: 'face' } } });
  const e = new AvatarEngine(body, { seed: 11 });
  const out = { probes: {}, plays: [] };
  let next = 0;
  for (let t = 0; t < until; t += 1 / 60) {
    while (next < pushes.length && pushes[next].t <= t) {
      const d = e.perform(pushes[next].perf);
      out.plays.push({ t: pushes[next].t, played: d.gesture_played, gesture: d.gesture });
      next += 1;
    }
    e.update(1 / 60);
    for (const p of probes) {
      if (out.probes[p] === undefined && t >= p) {
        out.probes[p] = { brow: body.morphs.browInnerUp || 0, frown: body.morphs.mouthFrownLeft || 0 };
      }
    }
  }
  process.stdout.write(JSON.stringify(out));
});
"""


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _TimedWidget:
    """Le widget sans Qt : `set_snapshot`, `reresolve` et `_push` sont les vrais."""

    def __init__(self) -> None:
        self._director = Director(catalogue(force=True))
        self._loaded = True
        self._animated = True
        self._last_state = ""
        self._last_gesture = ""
        self._last_speech_at = 0.0
        self._last_speech = -1.0
        self._last_payload = None
        self._last_push_at = 0.0
        self._last_listen_at = 0.0
        self._last_listen = -1.0
        self._listening = False
        self.scripts: list[tuple[float, str]] = []

    def _run(self, script: str) -> None:
        self.scripts.append((avatar_view.time.monotonic(), script))

    def __getattr__(self, name):  # noqa: ANN001
        # Toutes les autres methodes sont celles du vrai widget.
        method = getattr(avatar_view.JarvisAvatarWidget, name)
        return method.__get__(self)


_PERFORM = "window.JARVIS&&window.JARVIS.perform("


def _performances(widget: _TimedWidget, start: float) -> list[dict]:
    return [{"t": at - start, "perf": json.loads(script[len(_PERFORM):-1])}
            for at, script in widget.scripts if script.startswith(_PERFORM)]


def timed_section() -> int:
    import presence.director as director_mod
    from client_desktop.state import AssistantState, LinkState, Snapshot

    clock = _Clock()
    saved = (avatar_view.time.monotonic, director_mod.time.monotonic)
    avatar_view.time.monotonic = clock
    director_mod.time.monotonic = clock
    problems: list[str] = []
    try:
        results = {}
        for label, directive in (("warn", {"intent": "warn"}),
                                 ("affect seul", {"valence": 0.75, "arousal": 0.6})):
            widget = _TimedWidget()
            start = clock.now
            speaking = Snapshot(link=LinkState.CONNECTED, assistant=AssistantState.SPEAKING)
            listening = Snapshot(link=LinkState.CONNECTED, assistant=AssistantState.LISTENING)
            widget.set_snapshot(speaking)
            widget.set_intent_json({"directive": directive, "age": 0.0})
            for i in range(60 * 60 * 3):                # trois minutes, a 60 i/s
                clock.now = start + i / 60
                widget.set_snapshot(speaking if i < 60 * 4 else listening)
            results[label] = _performances(widget, start)

        # ── warn : l'expiration se voit, sans geste ni tempete de poussees ──
        pushes = results["warn"]
        expired = [p for p in pushes if p["t"] > 24 and p["perf"].get("intent") is None]
        if not expired:
            problems.append("warn : aucune poussee n'a montre l'expiration de l'intention")
        elif expired[0]["t"] > 25.0 + avatar_view.RERESOLVE_S + 0.1:
            problems.append(f"warn : expiration montree a {expired[0]['t']:.1f} s")
        # Pas de tempete : hors changement d'etat, jamais deux poussees plus
        # rapprochees que la cadence. Et le visage se POSE : une fois l'etat
        # revenu a sa base, plus rien ne part.
        quiet = [p["t"] for p in pushes if p["t"] > 4.5]
        tight = [round(b - a, 2) for a, b in zip(quiet, quiet[1:])
                 if b - a < avatar_view.RERESOLVE_S - 1e-6]
        if tight:
            problems.append(f"warn : poussees rapprochees de {tight} s")
        if any(t > 150 for t in quiet):
            problems.append(f"warn : encore des poussees a {max(quiet):.0f} s — le visage ne se pose pas")
        # Retomber, c'est palir : le visage de l'avertissement, puis le neutre.
        # Mesure avant `fading_expression` : concerned -> serious -> thinking
        # (yeux leves) pendant seize secondes -> neutral.
        path = []
        for p in pushes:
            if p["t"] > 0 and (not path or path[-1] != p["perf"]["expression"]):
                path.append(p["perf"]["expression"])
        if path != ["concerned", "neutral"]:
            problems.append(f"warn : en retombant, le visage passe par {' -> '.join(path)}")

        js = BASE / "avatar" / "js"
        script = TIMED_NODE % {"engine": (js / "engine.js").as_uri(),
                               "body": (js / "body_null.js").as_uri()}
        run = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                             input=json.dumps({"pushes": pushes, "until": 180, "probes": [10, 50]}),
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        if run.returncode != 0:
            print(run.stderr[-2000:])
            return 1
        engine = json.loads(run.stdout)
        during, after = engine["probes"]["10"], engine["probes"]["50"]
        if during["brow"] < 0.2:
            problems.append(f"warn : pas d'inquietude pendant l'intention ({during['brow']:.2f})")
        if after["brow"] > during["brow"] * 0.5:
            problems.append(f"warn : 50 s apres, sourcils encore a {after['brow']:.2f} "
                            f"(pendant : {during['brow']:.2f})")
        # Apres les 4 premieres secondes, l'etat change une fois (-> LISTENING)
        # et c'est tout : aucune autre poussee n'a le droit de jouer un geste.
        late = [p for p in engine["plays"]
                if p["t"] > 4.5 and p["played"] != "deja en cours"]
        if late:
            problems.append(f"warn : un geste joue sans decision ni changement d'etat : {late}")

        # ── affect seul : il decroit par paliers doux, jamais ne remonte ────
        pushes2 = results["affect seul"]
        levels = [p["perf"]["intensity"] for p in pushes2 if p["t"] > 4.5]
        rises = [round(b - a, 3) for a, b in zip(levels, levels[1:]) if b - a > 0.005]
        if len(levels) < 3:
            problems.append(f"affect : {len(levels)} poussee(s) en 55 s — la decroissance ne se voit pas")
        if rises:
            problems.append(f"affect : l'intensite remonte ({rises})")

        mark = "OK  " if not problems else "FAUX"
        when = f"{expired[0]['t']:.1f} s" if expired else "jamais"
        per_min = sum(1 for p in pushes if p["t"] <= 60)
        print(f"  [{mark}] le temps : warn expire a {when} ; {' -> '.join(path)} ; "
              f"{per_min} poussees la 1re minute, derniere a {max(quiet) if quiet else 0:.0f} s ; "
              f"sourcils {during['brow']:.2f} -> {after['brow']:.2f} ; "
              f"affect seul {' '.join(f'{v:.2f}' for v in levels[:7])}")
        for problem in problems:
            print(f"         {problem}")
        return len(problems)
    finally:
        avatar_view.time.monotonic, director_mod.time.monotonic = saved


# ── la voix de l'utilisateur, du micro jusqu'au hochement ────────────────────
#
# `Snapshot.mic_level` -> `set_snapshot` -> `window.JARVIS.listen()` -> moteur
# -> la tete. Chaque script que le vrai widget envoie a la page est rejoue dans
# le vrai moteur, a son instant : on ne teste pas que `listen` existe, on
# compte les hochements qu'il produit, et ou.

LISTEN_NODE = r"""
import { AvatarEngine } from '%(engine)s';
import { NullBody } from '%(body)s';
let raw = '';
process.stdin.on('data', (c) => { raw += c; });
process.stdin.on('end', () => {
  const { scripts, until } = JSON.parse(raw);
  const body = new NullBody({ parts: ['head', 'torso', 'arms', 'legs'], visemes: 'oculus',
                              manifest: { rig: { motion: 'face' } } });
  const e = new AvatarEngine(body, { seed: 12 });
  const window = { JARVIS: {
    perform: (p) => e.perform(p), speak: (l) => e.speak(l), listen: (l) => e.listen(l) } };
  let next = 0;
  const nods = [];
  let seen = 0;
  for (let t = 0; t < until; t += 1 / 60) {
    while (next < scripts.length && scripts[next][0] <= t) {
      new Function('window', scripts[next][1])(window);
      next += 1;
    }
    e.update(1 / 60);
    if (e.conversation.stats.backchannels > seen) { seen = e.conversation.stats.backchannels; nods.push(t); }
  }
  process.stdout.write(JSON.stringify({ nods, listens: e.metrics.listens }));
});
"""


def listen_section() -> int:
    import math

    import presence.director as director_mod
    from client_desktop.state import AssistantState, LinkState, Snapshot

    clock = _Clock()
    saved = (avatar_view.time.monotonic, director_mod.time.monotonic)
    avatar_view.time.monotonic = clock
    director_mod.time.monotonic = clock
    problems: list[str] = []
    try:
        widget = _TimedWidget()
        start = clock.now
        # 0-14 s : l'utilisateur parle, avec quatre pauses. 14-22 s : JARVIS
        # repond, et sa voix fuit dans le micro. 22-30 s : l'utilisateur, sans
        # pause (une longue phrase).
        pauses = [(3.0, 3.6), (6.0, 6.7), (9.1, 9.7), (12.0, 12.6)]
        for i in range(60 * 30):
            t = i / 60
            clock.now = start + t
            if t < 14 or t >= 22:
                speaking_user = not any(a <= t < b for a, b in pauses) or t >= 22
                mic = 0.02 + (0.35 * (0.5 + 0.5 * abs(math.sin(t * 13))) if speaking_user else 0)
                snap = Snapshot(link=LinkState.CONNECTED, assistant=AssistantState.LISTENING,
                                mic_level=mic)
            else:
                voice = 0.5 * abs(math.sin(t * 11))
                snap = Snapshot(link=LinkState.CONNECTED, assistant=AssistantState.SPEAKING,
                                mic_level=voice * 0.6, speaker_level=voice)
            widget.set_snapshot(snap)
        scripts = [[at - start, sc] for at, sc in widget.scripts]
        while_speaking = [t for t, sc in scripts if 14.05 < t < 22 and "listen(" in sc and "listen(0)" not in sc]
        if while_speaking:
            problems.append(f"le micro est parvenu au visage pendant que JARVIS parlait ({len(while_speaking)} fois)")
        if not any("listen(0)" in sc for t, sc in scripts if 13.9 < t < 14.2):
            problems.append("aucun listen(0) quand l'utilisateur perd la parole")

        js = BASE / "avatar" / "js"
        script = LISTEN_NODE % {"engine": (js / "engine.js").as_uri(),
                                "body": (js / "body_null.js").as_uri()}
        run = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                             input=json.dumps({"scripts": scripts, "until": 30}),
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        if run.returncode != 0:
            print(run.stderr[-2000:])
            return 1
        out = json.loads(run.stdout)
        nods = out["nods"]
        in_pause = [t for t in nods if any(a + 0.2 <= t <= a + 0.7 for a, _ in pauses)]
        elsewhere = [round(t, 1) for t in nods if t not in in_pause]
        if not nods:
            problems.append("aucun hochement d'ecoute : la voix de l'utilisateur n'arrive pas a la tete")
        if elsewhere:
            problems.append(f"hochements hors d'une pause de l'utilisateur, a {elsewhere} s")
        mark = "OK  " if not problems else "FAUX"
        print(f"  [{mark}] l'ecoute : {out['listens']} niveaux de micro recus, "
              f"{len(nods)} hochements, {len(in_pause)} dans une pause de l'utilisateur, "
              f"{len(while_speaking)} niveau envoye pendant qu'il parlait")
        for problem in problems:
            print(f"         {problem}")
        return len(problems)
    finally:
        avatar_view.time.monotonic, director_mod.time.monotonic = saved


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
        ("think + gaze user", "face", {"intent": "think", "gaze": "user"},
         {"gaze": "user", "gaze_source": "explicit", "expression": "thinking"}),
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
        # Un regard DECIDE sur l'utilisateur doit l'etre sur les deux axes. Ce
        # controle ne regardait que x, et `think` + `gaze: user` passait avec
        # des yeux leves a +0.17 : le visage `thinking` les emportait.
        if (perf["gaze"] == "user" and perf.get("gaze_source") in ("explicit", "intent")
                and abs(out["eyes"]["y"]) > 0.1):
            problems.append(f"regard user decide, yeux a y={out['eyes']['y']:.2f}")
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

    print()
    bad += timed_section()
    bad += listen_section()

    print(f"\n  {len(cases)} chaines completes + le temps + l'ecoute, {bad} probleme(s) "
          f"(cote Python {python_ms:.0f} ms pour tout le fil)")
    if not bad:
        print("  De l'appel d'outil au modele, chaque intention arrive et joue ce qu'elle doit.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
