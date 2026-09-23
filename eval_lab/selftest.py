"""
eval_lab/selftest.py — the lab is not trusted because it produces results.

    python -m eval_lab.selftest            (~30 s : it runs the legacy corpus
                                            and five fault-injection campaigns)

Same convention as context/ and server/ selftests. What it proves:

  1. The contract: nothing outside eval_lab/ imports it; the core is untouched.
  2. The mirrored vocabularies still match the code.
  3. The committed legacy corpus is exactly what a fresh import produces.
  4. EXIT CONDITION OF THE IMPORT: every legacy scenario passes, and — the
     half that makes the first half mean something — when a fault is
     injected into JARVIS, the old test and the new runner fail TOGETHER.
     A runner that passed everything would pass the first half too.
  5. Reproducibility and isolation: same scenario, same trace, even after a
     dirty one; the simulated clock is always put back.
  6. The verdicts go to the right owner: INVALID, CRASH and INFRA are never
     confused with FAIL, and a soft property never fails a trial.
  7. Resume after interruption, shards, and deduplication.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from eval_lab import scenario as sc                      # noqa: E402
from eval_lab.runner import load_jsonl, run, run_one     # noqa: E402

LAB = BASE_DIR / "eval_lab"
CORPUS = LAB / "corpus" / "legacy"
_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, str(detail)))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return wrap


def _legacy(surface=None):
    for f in sorted(CORPUS.glob("*.jsonl")):
        for s in load_jsonl(f):
            if surface is None or s["surface"] == surface:
                yield s


# ── 1. the contract ──────────────────────────────────────────────────────────

@check("nothing outside eval_lab/ imports eval_lab")
def _nobody_imports_us():
    offenders = []
    for path in BASE_DIR.rglob("*.py"):
        rel = path.relative_to(BASE_DIR)
        if rel.parts[0] in ("eval_lab", ".git", "client-android") or "__pycache__" in rel.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "eval_lab" for n in names):
                offenders.append(str(rel))
    assert not offenders, f"importent eval_lab : {offenders}"
    return "supprimer eval_lab/ ne casse rien"


@check("the core is untouched (main.py, ui.py, core/, actions/, dashboard/, memory/, plugins/)")
def _core_untouched():
    paths = ["main.py", "ui.py", "core", "actions", "dashboard", "memory", "plugins",
             "context", "server", "presence", "requirements.txt"]
    out = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=BASE_DIR,
                         capture_output=True, text=True).stdout.strip()
    assert not out, f"modifie : {out}"
    return f"{len(paths)} chemins propres"


# ── 2. vocabularies ──────────────────────────────────────────────────────────

@check("mirrored vocabularies match the code")
def _vocab():
    from eval_lab.vocab import context_enums, device_types
    for name, values in context_enums().items():
        assert tuple(getattr(sc, name)) == values, f"{name} a derive : {values}"
    assert tuple(sc.DEVICE_TYPES) == device_types(), device_types()
    return "priorites, situations, routes, canaux, activites, sonneries, types d'appareil"


# ── 3-4. the legacy corpus ───────────────────────────────────────────────────

@check("the committed legacy corpus is exactly a fresh import")
def _corpus_fresh():
    from eval_lab.legacy import import_all
    with tempfile.TemporaryDirectory() as tmp:
        counts = import_all(Path(tmp))
        for name in counts:
            fresh = (Path(tmp) / f"{name}.jsonl").read_bytes()
            kept = (CORPUS / f"{name}.jsonl").read_bytes()
            assert fresh == kept, f"{name}.jsonl a derive : relancer import-legacy et relire le diff"
    return ", ".join(f"{k} {v}" for k, v in counts.items())


@check("every legacy scenario is valid, unique, and passes")
def _legacy_pass():
    items = list(_legacy())
    ids = [s["id"] for s in items]
    assert len(ids) == len(set(ids)), "doublons dans le corpus legacy"
    face = sum(1 for s in items if s["surface"] == "face")
    assert face == 1632, f"{face} situations visage, attendu 1632"
    from eval_lab.triage import superseded
    sup = superseded()
    recs = []
    with tempfile.TemporaryDirectory() as tmp:
        run(items, Path(tmp) / "r.jsonl", resume=False, on_result=lambda s, r: recs.append(r))
    bad = [r["id"] for r in recs if r["verdict"] != "PASS" and r["id"] not in sup]
    assert not bad, f"legacy en echec : {bad[:5]}"
    for r in recs:
        if r["id"] in sup:
            props = {p.get("property") for p in r["problems"] if p["kind"] == "property"}
            assert r["verdict"] == "FAIL" and props == {sup[r["id"]]["property"]}, \
                f"{r['id']} devait echouer par {sup[r['id']]['property']} seulement : {props}"
    return f"{len(items) - len(sup)} PASS dont 1632 visage ; {len(sup)} remplace(s) par decision, " \
        "en echec par sa seule propriete"


# Each fault: a patch applied in a subprocess, then the OLD test and the NEW
# runner (on the legacy scenarios of that layer) both run under it.
_FAULTS = {
    "reunion IMPORTANT -> VOICE": (
        "import context.policy as p; from context.model import *; "
        "p._MATRIX[Situation.MEETING][Priority.IMPORTANT] = Channel.VOICE",
        "context.selftest", ("policy",)),
    "heures calmes jamais": (
        "import context.situation as s; s._in_quiet_hours = lambda *a: False",
        "context.selftest", ("situation", "policy", "sequence")),
    "la file ne rend jamais rien": (
        "import context.policy as p; p.DeferralQueue.release = lambda self, *a, **k: []",
        "context.selftest", ("sequence",)),
    "phrase d'appareil ignoree": (
        "import server.targeting as t; t.detect_hint = lambda text: None",
        "server.routing_selftest", ("routing",)),
    "regle d'origine avant capacite": (
        "import server.targeting as t\n"
        "_r = t.resolve\n"
        "def resolve(reg, *, text='', origin=None, capability='', model_hint=''):\n"
        "    if origin is not None and capability and origin.can(capability):\n"
        "        return t.Resolution(t.TargetKind.DEVICE, device=origin, rule='origin')\n"
        "    return _r(reg, text=text, origin=origin, capability=capability, model_hint=model_hint)\n"
        "t.resolve = resolve",
        "server.routing_selftest", ("routing",)),
}

_OLD = "{patch}\nimport runpy, sys\ntry:\n    runpy.run_module('{module}', run_name='__main__')\nexcept SystemExit as e:\n    sys.exit(e.code or 0)\n"
_NEW = """{patch}
import json, sys, tempfile
from pathlib import Path
from eval_lab.runner import load_jsonl, run
from eval_lab.triage import superseded
sup = superseded()   # already failing by decision: would make FAIL>0 trivially true
items = [s for f in sorted(Path('eval_lab/corpus/legacy').glob('*.jsonl')) for s in load_jsonl(f)
         if s['surface'] in {surfaces!r} and s['id'] not in sup]
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / 'r.jsonl'
    summary = run(items, out, resume=False)
print(json.dumps(summary.counts))
"""


@check("under an injected fault, old test and new runner fail together")
def _equivalence():
    lines = []
    for name, (patch, module, surfaces) in _FAULTS.items():
        old = subprocess.run([sys.executable, "-c", _OLD.format(patch=patch, module=module)],
                             cwd=BASE_DIR, capture_output=True, text=True, timeout=300)
        new = subprocess.run([sys.executable, "-c", _NEW.format(patch=patch, surfaces=surfaces)],
                             cwd=BASE_DIR, capture_output=True, text=True, timeout=300)
        assert new.returncode == 0, f"{name} : le runner a plante : {new.stderr[-300:]}"
        counts = json.loads(new.stdout.strip().splitlines()[-1])
        assert old.returncode != 0, f"{name} : l'ancien test ne voit pas la faute"
        assert counts["FAIL"] > 0, f"{name} : le nouveau runner ne voit pas la faute {counts}"
        assert counts["INFRA"] == 0 and counts["INVALID"] == 0, f"{name} : {counts}"
        lines.append(f"{name}: {counts['FAIL']} FAIL")
    return " ; ".join(lines)


# ── 5. reproducibility and isolation ─────────────────────────────────────────

_ROUTE = sc.make("routing", world={"devices": [
    {"id": "pc", "type": "pc", "caps": ["open_app"]},
    {"id": "tel", "type": "android", "caps": ["open_app"], "last_seen_ago_s": 200}],
    "turn": {"origin": "pc"}}, stimulus={"text": "ouvre sur mon telephone", "capability": "open_app"},
    expected={"kind": "unavailable"})

_SEQ = sc.make("sequence", world={"phone": {"age_s": 0, "screen_on": True}},
               events=[{"op": "deliver", "priority": "IMPORTANT"},
                       {"op": "push", "priority": "USEFUL", "payload": "x"},
                       {"op": "advance", "s": 30}, {"op": "decide", "priority": "IMPORTANT"}],
               expected={"steps.3.channel": "DEFER"})


@check("same scenario, same trace — before and after a dirty one")
def _reproducible():
    first = run_one(_ROUTE)
    run_one(_SEQ)                                 # delivers, queues, moves time
    second = run_one(_ROUTE)
    assert first["trace"] == second["trace"], "la trace a change entre deux executions"
    assert first["verdict"] == "PASS", first
    a, b = run_one(_SEQ), run_one(_SEQ)
    assert a["trace"] == b["trace"] and a["verdict"] == "PASS", (a["verdict"], a["problems"])
    return "routage stale 200 s -> unavailable, identique ; sequence identique"


@check("the simulated clock never outlives its trial")
def _clock_restored():
    import datetime as real_dt
    import time as real_time
    import context.situation as cs
    import server.devices as sd
    run_one(_ROUTE)
    run_one(_SEQ)
    assert cs.datetime is real_dt.datetime, "context.situation.datetime n'a pas ete rendu"
    assert cs.time is real_time and sd.time is real_time, "time n'a pas ete rendu"
    assert abs(real_time.time() - time.time()) < 5
    import context
    assert context._store is None and context._queue is None, "singletons contextes laisses"
    return "time, datetime et singletons rendus"


# ── 6. verdict ownership ─────────────────────────────────────────────────────

@check("impossible worlds are INVALID, never FAIL")
def _invalid():
    bad = [
        {**_ROUTE, "surface": "teleport"},
        sc.make("routing", world={"devices": [{"id": "a", "type": "pc"}], "turn": {"origin": "ghost"}},
                stimulus={"text": "x"}, expected={"kind": "clarify"}),
        sc.make("situation", world={"phone": {"age_s": -5}}, expected={"situation": "UNKNOWN"}),
        sc.make("face", stimulus={"intent": "dance", "state": "LISTENING", "speech": False,
                                  "gaze": None, "urgent": False, "interrupted": False},
                expected={"violations": {"$len": 0}}),
        # The world edited without restamp. (Editing only `expected` keeps the
        # id on purpose: the id names the situation, not the opinion about it.)
        {**_SEQ, "world": {"phone": {"age_s": 0, "screen_on": False}}},
        sc.make("policy", world={}, stimulus={"priority": "URGENTISSIME"}, expected={"channel": "DROP"}),
    ]
    verdicts = [run_one(s)["verdict"] if s["surface"] != "face"
                else run([s], Path(tempfile.mkdtemp()) / "r.jsonl", resume=False).counts
                for s in bad]
    face_counts = verdicts.pop(3)
    assert all(v == "INVALID" for v in verdicts), verdicts
    assert face_counts["INVALID"] == 1, face_counts
    return f"{len(bad)} mondes impossibles refuses"


@check("JARVIS raising is CRASH; the lab raising is INFRA")
def _crash_vs_infra():
    import server.targeting as t
    from eval_lab import surfaces
    saved = t.resolve
    try:
        def boom(*a, **k):
            raise KeyError("dans JARVIS")
        t.resolve = boom
        assert run_one(_ROUTE)["verdict"] == "CRASH"
    finally:
        t.resolve = saved
    saved_runner = surfaces.RUNNERS["routing"]
    try:
        surfaces.RUNNERS["routing"] = lambda s: {}["pas de cle"]
        assert run_one(_ROUTE)["verdict"] == "INFRA"
    finally:
        surfaces.RUNNERS["routing"] = saved_runner
    assert run_one(_ROUTE)["verdict"] == "PASS"
    return "exception dans resolve -> CRASH ; exception dans la surface -> INFRA"


@check("the owner's decisions are wired: hard where decided, INCONCLUSIVE where not")
def _decisions():
    from eval_lab.properties import DECISIONS, REGISTRY
    cited = " ".join(p.source for p in REGISTRY.values())
    uncited = [d for d in DECISIONS if d not in cited]
    assert not uncited, f"decisions sans propriete : {uncited}"
    # 4 : no sink, no delivery
    no_sink = run_one(sc.make("policy", world={"force": {"situation": "ACTIVE", "route": "NONE"}},
                              stimulus={"priority": "IMPORTANT"}, oracle="implicit"))
    assert no_sink["verdict"] == "FAIL" and "NO_DELIVERY_WITHOUT_SINK" in str(no_sink["problems"])
    # 5 : the cooldown inversion found by the fuzzer is now a failure
    inv = run_one(sc.make("policy", world={"phone": {"age_s": 0, "screen_on": True, "headset": True},
                                           "delivered": [{"priority": "IMPORTANT", "ago_s": 60}]},
                          stimulus={"priority": "USEFUL"}, oracle="implicit"))
    assert inv["verdict"] == "FAIL" and "PRIORITY_MONOTONIC" in str(inv["problems"]), inv["problems"]
    # 6 : a burst of spoken alerts is INCONCLUSIVE, never PASS, never FAIL
    burst = run_one(sc.make("sequence", world={"phone": {"age_s": 0, "screen_on": True, "headset": True},
                                                "system": {"desktop_audio": True}},
                            events=[{"op": "alerts", "alerts": ["[MONITOR_ALERT] a\nHeadline: x",
                                                                "[MONITOR_ALERT] b\nHeadline: y"]}],
                            oracle="implicit"))
    assert burst["verdict"] == "INCONCLUSIVE", (burst["verdict"], burst["problems"])
    return "4 -> FAIL sans sink ; 5 -> inversion de priorite FAIL ; 6 -> rafale INCONCLUSIVE"


@check("oracle operators mean what they say")
def _operators():
    tr = {"a": [1, 2], "b": "abc", "c": None, "steps": [{"x": 1}]}
    assert sc.matches(sc.lookup(tr, "a"), {"$len": 2})
    assert sc.matches(sc.lookup(tr, "b"), {"$contains": "bc"})
    assert sc.matches(sc.lookup(tr, "c"), None)
    assert not sc.matches(sc.lookup(tr, "missing"), None), "absent == None ?"
    assert sc.matches(sc.lookup(tr, "missing"), {"$absent": True})
    assert sc.matches(sc.lookup(tr, "steps.0.x"), 1)
    assert sc.matches(sc.lookup(tr, "steps.-1.x"), {"$in": [1, 2]})
    assert sc.diff({"expected": {"b": "abc"}, "forbidden": {"b": "abc"}}, tr)[0]["kind"] == "forbidden"
    return "$len $contains $absent $in, chemins negatifs, absent != None"


# ── 7. scale mechanics ───────────────────────────────────────────────────────

@check("an interrupted run resumes without redoing or duplicating")
def _resume():
    items = [s for s in _legacy() if s["surface"] != "face"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.jsonl"
        run(items[:30], out, resume=False)
        with out.open("a", encoding="utf-8") as fh:
            fh.write('{"id": "torn')                    # killed mid-write
        summary = run(items, out, resume=True)
        assert summary.skipped == 30, summary.line()
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[30] == '{"id": "torn', "la ligne dechiree a ete recollee a la suivante"
        ids = [json.loads(l)["id"] for i, l in enumerate(lines) if i != 30]
        assert len(ids) == len(set(ids)) == len(items), (len(ids), len(set(ids)), len(items))
    return f"30 repris, {len(items) - 30} executes, 0 doublon, ligne dechiree ignoree"


@check("shards partition the corpus, and a duplicate input runs once")
def _shards():
    items = [s for s in _legacy() if s["surface"] != "face"]
    seen = []
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(3):
            out = Path(tmp) / f"r{k}.jsonl"
            run(items, out, resume=False, shard=(k, 3))
            seen += [json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()]
        assert sorted(seen) == sorted(s["id"] for s in items), "tranches non disjointes ou incompletes"
        out = Path(tmp) / "dup.jsonl"
        summary = run(items[:5] + items[:5], out, resume=False)
        assert summary.total == 5 and summary.skipped == 5, summary.line()
    return f"3 tranches = {len(items)} scenarios exactement"


# ── 8. generation, mutation, relations, coverage ─────────────────────────────

@check("every strategy is deterministic and emits only valid scenarios")
def _generation():
    from eval_lab.generate import STRATEGIES
    lines = []
    for name, fn in STRATEGIES.items():
        a = [s["id"] for s in fn(150, 7)]
        b = [s["id"] for s in fn(150, 7)]
        c = [s["id"] for s in fn(150, 8)]
        assert a == b, f"{name} : meme graine, scenarios differents"
        if name not in ("boundary", "adversarial"):      # seed-independent by design
            assert a != c, f"{name} : la graine n'a aucun effet"
        bad = []
        for s in fn(150, 7):
            try:
                sc.validate(s)
            except sc.Invalid as e:
                bad.append(e.args[0][:1])
        assert not bad, f"{name} : {len(bad)} invalides, ex. {bad[0]}"
        lines.append(f"{name} {len(a)}")
    return ", ".join(lines)


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{prefix}.{i}")
    else:
        yield prefix, json.dumps(node, sort_keys=True)


@check("a mutation changes one knob, keeps its lineage, and gets a new id")
def _mutation():
    import random
    from eval_lab.generate import STRATEGIES
    from eval_lab.mutate import mutate
    rng = random.Random(3)
    parents = list(STRATEGIES["pairwise"](40, 1)) + list(STRATEGIES["pairwise-routing"](40, 1)) \
        + list(STRATEGIES["sequence"](40, 1)) + [s for s in _legacy("face")][:40]
    n = 0
    for p in parents:
        c = mutate(p, rng)
        if c is None:
            continue
        n += 1
        sc.validate(c)
        m = c["lineage"]["mutations"][-1]
        assert c["lineage"]["parent"] == p["id"] and c["id"] != p["id"], "lignee ou id"
        root = ".".join(m["path"].split("~")[0].split(".")[:2])
        pl, cl = dict(_leaves(sc.semantic_key(p))), dict(_leaves(sc.semantic_key(c)))
        changed = {k for k in pl.keys() | cl.keys() if pl.get(k) != cl.get(k)}
        assert changed, f"mutation sans effet : {m}"
        stray = [k for k in changed if not (k.startswith(root) or root.startswith("events")
                                            and k.startswith("events"))]
        assert not stray, f"{m['path']} a aussi touche {stray}"
    return f"{n} enfants, chacun limite a sa variable"


@check("a hard relation catches a fault no single trace can see")
def _relation_catches():
    import random
    import context.situation as cs
    from eval_lab.mutate import check_relations, mutate
    parent = sc.make("policy", world={"phone": {"age_s": 0, "screen_on": True, "headset": False}},
                     stimulus={"priority": "IMPORTANT"}, oracle="implicit")
    rng, child = random.Random(0), None
    while child is None or child["lineage"]["mutations"][-1]["path"] != "world.phone.headset":
        child = mutate(parent, rng)
    ok = check_relations(run_one(parent)["trace"], run_one(child)["trace"], child)
    assert not ok, f"relation declenchee sans faute : {ok}"
    saved = cs.derive
    try:
        def faulty(dev, *a, **k):              # the headset now changes the situation
            snap = saved(dev, *a, **k)
            if dev.headset:
                from context.model import Situation
                snap.situation = Situation.IDLE
            return snap
        cs.derive = faulty
        rels = check_relations(run_one(parent)["trace"], run_one(child)["trace"], child)
        r_child = run_one(child)
    finally:
        cs.derive = saved
    assert rels and rels[0]["property"] == "HEADSET_DOES_NOT_CHANGE_SITUATION", rels
    assert r_child["verdict"] == "PASS", "la trace seule voyait deja la faute : test mal choisi"
    return "trace isolee PASS, relation parent/enfant FAIL"


@check("novelty is zero for a duplicate and falls as a space saturates")
def _novelty():
    from eval_lab.coverage import Coverage
    from eval_lab.generate import STRATEGIES
    cov = Coverage()
    items = list(STRATEGIES["pairwise"](300, 2))
    first = [cov.add(s) for s in items[:30]]
    last = [cov.add(s) for s in items[-30:]]
    assert cov.add(items[0]) == 0.0 and cov.metrics()["duplicate_rate"] > 0
    assert sum(last) < sum(first), (sum(first), sum(last))
    return f"nouveaute 30 premiers {sum(first) / 30:.2f} -> 30 derniers {sum(last) / 30:.2f}"


# ── 9. triage, minimisation, regressions ─────────────────────────────────────

@check("failures cluster by cause, and minimisation keeps the cause")
def _triage():
    from eval_lab.generate import STRATEGIES
    from eval_lab.triage import cluster, minimise, signature
    items = list(STRATEGIES["sequence"](400, 1))
    recs = [run_one(s) for s in items]
    by_id = {s["id"]: s for s in items}
    cl = cluster(recs, by_id, known=set())
    fails = sum(1 for r in recs if r["verdict"] != "PASS")
    assert fails and sum(c["size"] for c in cl) == fails, "un echec hors cluster"
    assert len(cl) < fails, "aucun regroupement"
    c = cl[0]
    s = by_id[c["representative"]]
    m, runs = minimise(s, c["signature"])
    rec = run_one(m)
    assert signature(rec) == c["signature"], "la minimisation a change la cause"
    assert len(sc.dumps(sc.semantic_key(m))) < len(sc.dumps(sc.semantic_key(s))), "rien retire"
    assert len(m["events"]) <= len(s["events"])
    return (f"{fails} echecs -> {len(cl)} clusters ; {c['first_invariant']} : "
            f"{len(s['events'])} -> {len(m['events'])} evenements en {runs} executions")


@check("the regression corpus matches its manifest, and nothing vanished since the last commit")
def _regressions():
    from eval_lab import regressions as rg
    shown = subprocess.run(["git", "show", "HEAD:eval_lab/corpus/regressions/manifest.json"],
                           cwd=BASE_DIR, capture_output=True, text=True)
    committed = json.loads(shown.stdout) if shown.returncode == 0 else None
    problems = rg.check_manifest(committed)
    assert not problems, "; ".join(problems)
    rows = rg.check()
    bad = [r for r in rows if r["state"] == "REGRESSED" or "DIFFERENTLY" in r["state"]]
    assert not bad, bad
    return f"{len(rows)} regression(s) : " + ", ".join(f"{r['id']} {r['state']}" for r in rows) \
        + ("" if committed else " (manifeste pas encore commite)")


@check("a regression that vanishes or changes is detected")
def _regressions_guard():
    from eval_lab import regressions as rg
    man = rg.manifest()
    if not man:
        return "aucune regression encore"
    fake_committed = dict(man)
    fake_committed["REG-9999"] = "0" * 64
    first = next(iter(man))
    fake_committed[first] = "f" * 64
    problems = rg.check_manifest(fake_committed)
    assert any("REG-9999" in p for p in problems), problems
    assert any(first in p and "empreinte" in p for p in problems), problems
    return "disparition et modification signalees"


# ── 10. the LLM pipeline, offline ────────────────────────────────────────────

def _fake_text() -> str:
    j = json.dumps
    # A claim no property backs and no known cause explains: the model thinks
    # a USEFUL message deserves the voice. The table says NOTIFY_SILENT.
    active = {"time": {"local": "2026-09-23T10:00:00"},
              "phone": {"age_s": 0, "screen_on": True, "headset": True}}
    return j({"scenarios": [
        {"surface": "policy", "family": "claim", "hypothesis": "un message utile merite la voix",
         "world_json": j(active), "stimulus_json": j({"priority": "USEFUL"}), "events_json": "[]",
         "claim_json": j({"channel": "VOICE"})},
        {"surface": "policy", "family": "claim", "hypothesis": "doublon", "world_json": j(active),
         "stimulus_json": j({"priority": "USEFUL"}), "events_json": "[]",
         "claim_json": j({"channel": "VOICE"})},
        {"surface": "routing", "family": "x", "hypothesis": "origine fantome",
         "world_json": j({"devices": [{"id": "a", "type": "pc"}], "turn": {"origin": "ghost"}}),
         "stimulus_json": j({"text": "ouvre"}), "events_json": "[]", "claim_json": "{}"},
        {"surface": "policy", "family": "x", "hypothesis": "json casse", "world_json": "{pas du json",
         "stimulus_json": "{}", "events_json": "[]", "claim_json": "{}"},
        # No claim: only a HARD property can fail it. The test injects the
        # fault itself (meeting -> voice), so it never depends on a real,
        # possibly already fixed, bug of JARVIS.
        {"surface": "policy", "family": "meeting", "hypothesis": "JARVIS parle en reunion",
         # The hour keeps it distinct from the legacy "never speaks in a
         # meeting" scenario, which the intake would rightly reject as a copy.
         "world_json": j({"time": {"local": "2026-09-23T11:00:00"},
                          "phone": {"age_s": 0, "screen_on": True, "dnd": True}}),
         "stimulus_json": j({"priority": "IMPORTANT"}), "events_json": "[]", "claim_json": "{}"},
    ]})


class _Fake:
    def __init__(self, fail=0):
        self.calls, self.fail = 0, fail

    def complete(self, system, user):
        self.calls += 1
        if self.calls <= self.fail:
            return {"error": "network"}
        return {"text": _fake_text(), "usage": {"input_tokens": 100_000, "output_tokens": 20_000}}


@check("LLM output goes through the same validators, and its claims are never bugs")
def _llm_pipeline():
    from eval_lab.llm import Cassette, campaign
    from eval_lab.triage import cluster
    seeds = [s for s in _legacy() if s["surface"] != "face"]
    import context.policy as cp
    from context.model import Channel, Priority, Situation
    got = []
    saved = cp._MATRIX[Situation.MEETING][Priority.IMPORTANT]
    with tempfile.TemporaryDirectory() as tmp:
        tape = Path(tmp) / "c.jsonl"
        try:
            cp._MATRIX[Situation.MEETING][Priority.IMPORTANT] = Channel.VOICE     # injected fault
            usage, total = campaign(Cassette(tape, _Fake()), mode="adversary", batches=1, per_batch=5,
                                    seed_corpus=seeds, known=set(), max_usd=100,
                                    sink=lambda s, r: got.append((s, r)), log=lambda *_: None)
        finally:
            cp._MATRIX[Situation.MEETING][Priority.IMPORTANT] = saved
        assert (total.proposed, len(total.accepted), total.invalid, total.unparseable, total.duplicate) \
            == (5, 2, 1, 1, 1), (total.proposed, len(total.accepted), total.invalid, total.unparseable, total.duplicate)
        assert abs(usage.usd - (0.4 + 0.4)) < 1e-9, usage.usd          # 100k in @4$ + 20k out @20$
        cl = {c["first_invariant"]: c["class"] for c in cluster([r for _, r in got], {s["id"]: s for s, _ in got}, set())}
        assert cl.get("channel") == "oracle-disputed", cl            # the model's claim, contradicted
        assert cl.get("MEETING_NEVER_SPEAKS") == "jarvis-candidate", cl  # a HARD property of the project
        replay = []
        campaign(Cassette(tape, None), mode="adversary", batches=1, per_batch=5, seed_corpus=seeds,
                 known=set(), max_usd=100, sink=lambda s, r: replay.append(s["id"]), log=lambda *_: None)
        assert replay == [s["id"] for s, _ in got], "la cassette ne rejoue pas a l'identique"
    return "5 proposes -> 2 acceptes ; revendication -> oracle-disputed ; propriete -> candidat ; rejeu identique"


@check("an LLM campaign stops on budget and on repeated errors, without raising")
def _llm_limits():
    import eval_lab.llm as L
    from eval_lab.llm import campaign
    seeds = [s for s in _legacy() if s["surface"] != "face"][:10]
    usage, _ = campaign(_Fake(), mode="generate", batches=10, per_batch=5, seed_corpus=seeds, known=set(),
                        max_usd=1.0, sink=lambda s, r: None, log=lambda *_: None)
    assert usage.calls == 1 and usage.usd <= 1.0, \
        f"{usage.calls} appels, {usage.usd:.2f} $ pour un plafond de 1 $ a 0.80 $ l'appel"
    saved, L.time.sleep = L.time.sleep, lambda s: None
    try:
        fake = _Fake(fail=99)
        usage, _ = campaign(fake, mode="generate", batches=10, per_batch=5, seed_corpus=seeds, known=set(),
                            max_usd=100, sink=lambda s, r: None, log=lambda *_: None)
    finally:
        L.time.sleep = saved
    assert fake.calls == 3 and usage.failures == 3, (fake.calls, usage.failures)
    return "plafond 1 $ jamais depasse (0.80 $, 1 appel) ; arret apres 3 erreurs reseau"


# ── 11. tiers: FULL, the grader, REAL ────────────────────────────────────────

@check("FULL runs the real router on its threads, and its properties see a leak")
def _full():
    import server.routing as r
    from eval_lab.generate import router
    from eval_lab.properties import _requested
    # Lab mechanics only: scenarios naming no target, so an open JARVIS bug
    # (REG-0003, decision N1) cannot make a lab check red.
    items = [s for s in router(200, 5) if _requested(s) is None]
    recs = [run_one(s) for s in items]
    assert all(x["verdict"] == "PASS" for x in recs), [x for x in recs if x["verdict"] != "PASS"][:1]
    kinds = {("remote" if t["executed_on"] and t["executed_on"] != ["local"] else
              "local" if t["executed_on"] else "refus") for t in (x["trace"] for x in recs)}
    assert kinds == {"remote", "local", "refus"}, kinds
    orig = r.ActionRouter.run
    try:
        def leaky(self, name, parameters, ctx=None):
            return self._inner.run(name, parameters, ctx)      # routing bypassed entirely
        r.ActionRouter.run = leaky
        bad = sum(1 for s in items if run_one(s)["verdict"] == "FAIL")
    finally:
        r.ActionRouter.run = orig
    assert bad > 0, "routage court-circuite, et aucune propriete ne le voit"
    return f"{len(items)} essais : local, distant et refus tous atteints ; faute -> {bad} FAIL"


@check("the grader sees a string, never the trial, and never changes a verdict")
def _grader():
    from eval_lab.grader import grade, texts_of
    from eval_lab.properties import _requested
    items = [s for s in __import__("eval_lab.generate", fromlist=["router"]).router(120, 5)
             if _requested(s) is None]
    recs = [run_one(s) for s in items]
    before = json.dumps(recs, sort_keys=True)
    seen = []

    class Judge:
        def complete(self, system, user, schema=None):
            seen.append(user)
            return {"text": json.dumps({"verdict": "problem", "reason": "test"})}
    with tempfile.TemporaryDirectory() as tmp:
        counts = grade(Judge(), texts_of(recs), Path(tmp) / "g.jsonl")
    assert json.dumps(recs, sort_keys=True) == before, "le grader a modifie les resultats"
    assert all(isinstance(u, str) and set(json.loads(u)) == {"phrase", "occurrences"} for u in seen)
    assert counts["problem"] == len(seen) and all(r["verdict"] == "PASS" for r in recs)
    return f"{len(seen)} phrases jugees une fois chacune, resultats intacts"


@check("nothing generated can reach REAL, and no run executes it")
def _real_guard():
    from eval_lab import real
    gen = sc.make("policy", world={}, stimulus={"priority": "USEFUL"}, tier="real", source="llm",
                  oracle="implicit")
    assert run_one(gen)["verdict"] == "INVALID"
    suite = real.seed_suite()
    for s in suite:
        sc.validate(s)
    with tempfile.TemporaryDirectory() as tmp:
        summary = run(suite, Path(tmp) / "r.jsonl", resume=False, tiers=("fast", "full", "real"))
    assert summary.total == 0, "un scenario REAL a ete execute par le runner"
    return f"genere -> INVALID ; {len(suite)} scenarios humains valides, 0 execute"


@check("a 400 is reported in the API's words, and a refused output format falls back once")
def _provider_400():
    try:
        import anthropic
        import httpx2
    except ImportError:
        return "SDK absent : non verifie ici"
    from types import SimpleNamespace
    from eval_lab.llm import AnthropicProvider

    def bad(msg):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx2.Response(400, request=req, headers={"request-id": "req_test"})
        return anthropic.BadRequestError(msg, response=resp,
                                         body={"error": {"type": "invalid_request_error", "message": msg}})

    ok = SimpleNamespace(stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1},
                         content=[SimpleNamespace(type="text", text='Voici :\n```json\n{"scenarios": []}\n```')])
    p = AnthropicProvider.__new__(AnthropicProvider)
    p._anthropic, p.model, p.effort, p.structured, p.notes = anthropic, "m", "high", True, []
    calls = []

    def fake_call(system, user, schema):
        calls.append(p.structured)
        if p.structured:
            raise bad("output_config.format: structured outputs are not supported for this model")
        return ok
    p._call = fake_call
    r = p.complete("s", "u")
    assert calls == [True, False] and r["text"] == '{"scenarios": []}' and r["structured"] is False, (calls, r)
    assert p.notes and "request-id req_test" in p.notes[0], p.notes
    p2 = AnthropicProvider.__new__(AnthropicProvider)
    p2._anthropic, p2.model, p2.effort, p2.structured, p2.notes = anthropic, "m", "high", True, []

    def other(system, user, schema):
        raise bad("max_tokens: must be at most 8192")
    p2._call = other
    r2 = p2.complete("s", "u")
    assert r2["error"] == "http_400" and "max_tokens: must be at most" in r2["detail"] and p2.structured, r2
    return "format refuse -> une bascule, JSON extrait ; autre 400 -> message de l'API conserve"


@check("frontier search finds lines on router and sequence scenarios")
def _frontiers():
    import random
    from eval_lab.mutate import frontier
    r = sc.make("router", tier="full", oracle="implicit",
                world={"devices": [{"id": "pc", "type": "pc", "caps": ["open_app"]},
                                   {"id": "tel", "type": "android", "caps": []}],
                       "turn": {"origin": "tel", "text": "ouvre sur mon telephone", "ago_s": 3},
                       "local_tools": ["open_app"]},
                stimulus={"tool": "open_app", "parameters": {}})
    q = sc.make("sequence", oracle="implicit", world={"phone": {"age_s": 0, "screen_on": True}},
                events=[{"op": "decide", "priority": "USEFUL", "push_if_deferred": True},
                        {"op": "advance", "s": 10}, {"op": "decide", "priority": "IMPORTANT"}])
    fr_r, fr_q = frontier(r, random.Random(0), 40), frontier(q, random.Random(0), 40)
    assert fr_r and any("executed_on" in x["changed"] for x in fr_r), fr_r
    assert fr_q and any("channels" in x["changed"] for x in fr_q), fr_q
    return f"router {len(fr_r)} frontieres, sequence {len(fr_q)}"


def main() -> int:
    # Redirected on Windows, stdout is cp1252: an arrow in a detail must not
    # turn a green run into a crash (server/selftest.py has exactly that bug).
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    width = max(len(n) for n, _, _ in _results)
    for name, ok, detail in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n  {passed}/{len(_results)} passed\n")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
