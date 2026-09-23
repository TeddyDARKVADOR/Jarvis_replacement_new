"""
eval_lab/triage.py — from thousands of failures to a handful of causes.

    failures -> signatures -> clusters -> classification -> minimal reproducer

SIGNATURE
    What a failure IS, stripped of what it happens to contain: the surface,
    the first invariant it breaks, and the few trace facts that invariant is
    about (the rule that fired, the kind of payload lost, the channel chosen).
    Two failures with the same signature are presumed to share a cause; the
    cluster keeps every member so that presumption can be checked.

CLASSIFICATION — whose problem is it
    jarvis-crash        JARVIS raised.
    jarvis-candidate    a HARD property or relation, stated by the project
                        itself, is broken. The strongest signal the lab has.
    oracle-disputed     only a lab-written expectation disagrees (adversarial,
                        boundary). JARVIS or the lab's oracle is wrong; a human
                        decides which.
    legacy-regression   a legacy scenario (an existing test) fails: either a
                        real regression, or the code changed on purpose.
    superseded-legacy   a legacy scenario whose oracle a dated decision of the
                        project owner has overruled (corpus/decisions.json):
                        expected to fail until JARVIS and the old test change.
    inconclusive        only an explicitly undecided property is involved.
    lab                 INFRA — the simulator, a surface, Node.
    generator           INVALID — a scenario that should not exist.
    unstable            the representative does not reproduce identically.
    known               the signature is already in the regression corpus.

MINIMISATION
    Greedy delta debugging: drop an event, a phone field, a device, a
    capability, a system fact, a delivery; shorten a wait — keep the change
    only if the scenario stays valid AND still fails with the same signature.
    Repeat until nothing can be removed. What is left is the smallest
    counter-example the lab could find, not necessarily the smallest one.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from pathlib import Path

from . import scenario as sc


DECISIONS_FILE = Path(__file__).resolve().parent / "corpus" / "decisions.json"


def superseded() -> dict:
    if not DECISIONS_FILE.exists():
        return {}
    return json.loads(DECISIONS_FILE.read_text(encoding="utf-8")).get("superseded_legacy", {})


def _first(rec: dict) -> dict | None:
    order = {"property": 0, "relation": 1, "expected": 2, "forbidden": 3, "infra": 4, "inconclusive": 5}
    probs = [p for p in rec.get("problems") or [] if p["kind"] in order]
    return min(probs, key=lambda p: order[p["kind"]]) if probs else None


def _abstract(rec: dict, p: dict | None) -> tuple:
    """The trace facts that matter for this invariant — nothing else."""
    t = rec.get("trace") or {}
    name = (p or {}).get("property") or (p or {}).get("path")
    if name == "DEFERRED_NEVER_LOST":
        lost = [x for st in t.get("steps") or [] for x in st.get("lost") or []]
        return ("payload", "dict" if lost and isinstance(lost[0], dict) else "str")
    if rec["surface"] == "routing":
        return ("rule", t.get("rule"), t.get("kind"))
    if rec["surface"] in ("policy", "situation"):
        return ("outcome", t.get("situation"), t.get("channel"))
    if p and p["kind"] in ("expected", "forbidden"):
        return ("want", json.dumps(p.get("want"), sort_keys=True))
    return ()


def signature(rec: dict) -> str:
    if rec["verdict"] in ("CRASH", "INFRA", "INVALID"):
        err = str(rec.get("error") or "")
        head = err.split(":")[0] if rec["verdict"] != "INVALID" else err[:60]
        return f"{rec['verdict']}|{rec['surface']}|{head}"
    p = _first(rec)
    name = (p or {}).get("property") or (p or {}).get("path") or "?"
    return "|".join([rec["verdict"], rec["surface"], name, *map(str, _abstract(rec, p))])


def classify(rec: dict, s: dict | None, known: set[str]) -> str:
    sig = signature(rec)
    if sig in known:
        return "known"
    v = rec["verdict"]
    if v == "CRASH":
        return "jarvis-crash"
    if v == "INFRA":
        return "lab"
    if v == "INVALID":
        return "generator"
    if v == "INCONCLUSIVE":
        return "inconclusive"
    sup = superseded().get((s or {}).get("id") or rec["id"])
    if sup and sup["property"] in {p.get("property") for p in rec.get("problems") or []}:
        return "superseded-legacy"
    kinds = {p["kind"] for p in rec.get("problems") or []}
    if kinds & {"property", "relation"}:
        return "jarvis-candidate"
    if (s or {}).get("source") == "legacy":
        return "legacy-regression"
    return "oracle-disputed"


# ── clustering ───────────────────────────────────────────────────────────────

def cluster(results, scenarios: dict[str, dict], known: set[str]) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for rec in results:
        if rec["verdict"] == "PASS":
            continue
        groups[signature(rec)].append(rec)
    out = []
    for sig, members in groups.items():
        rep = min(members, key=lambda r: len(sc.dumps(scenarios.get(r["id"], {}))))
        s = scenarios.get(rep["id"])
        p = _first(rep)
        out.append({
            "signature": sig,
            "size": len(members),
            "class": classify(rep, s, known),
            "representative": rep["id"],
            "members": [r["id"] for r in members],
            "first_invariant": (p or {}).get("property") or (p or {}).get("path"),
            "source": (p or {}).get("source"),
            "example": (p or {}).get("got") if p else rep.get("error"),
            "families": sorted({r.get("family") or "" for r in members}),
        })
    out.sort(key=lambda c: (c["class"] != "jarvis-crash", c["class"] != "jarvis-candidate", -c["size"]))
    for i, c in enumerate(out, 1):
        c["cluster"] = f"C{i:03d}"
    return out


# ── minimisation ─────────────────────────────────────────────────────────────

def _run(s: dict) -> dict:
    from .runner import _run_face, run_one
    return _run_face([s])[0] if s["surface"] == "face" else run_one(s)


def _candidates(s: dict):
    """Every one-step simplification of `s`, smallest-effect first."""
    w = s["world"]
    for i in range(len(s.get("events") or [])):
        c = copy.deepcopy(s)
        del c["events"][i]
        yield c
    for i, ev in enumerate(s.get("events") or []):
        if ev.get("op") == "alerts" and len(ev.get("alerts") or []) > 1:
            c = copy.deepcopy(s)
            c["events"][i]["alerts"] = ev["alerts"][:1]
            yield c
        if ev.get("op") == "world" and isinstance(ev.get("phone"), dict):
            for k in list(ev["phone"]):
                if k != "age_s":
                    c = copy.deepcopy(s)
                    del c["events"][i]["phone"][k]
                    yield c
    for k in list((w.get("phone") or {})):
        if k != "age_s":
            c = copy.deepcopy(s)
            del c["world"]["phone"][k]
            yield c
    for key in ("system", "delivered", "policy", "thresholds", "queue"):
        if w.get(key):
            c = copy.deepcopy(s)
            del c["world"][key]
            yield c
    for i, d in enumerate(w.get("devices") or []):
        c = copy.deepcopy(s)
        gone = c["world"]["devices"].pop(i)["id"]
        if (c["world"].get("turn") or {}).get("origin") == gone:
            continue
        yield c
        for cap in d.get("caps") or []:
            c = copy.deepcopy(s)
            c["world"]["devices"][i]["caps"] = [x for x in d["caps"] if x != cap]
            yield c
    if s["surface"] == "routing" and s["stimulus"].get("model_hint"):
        c = copy.deepcopy(s)
        c["stimulus"]["model_hint"] = ""
        yield c


def minimise(s: dict, sig: str, max_runs: int = 400) -> tuple[dict, int]:
    """Shrink `s` while it still fails with signature `sig`. -> (minimal, runs)."""
    current, runs = copy.deepcopy(s), 0
    improved = True
    while improved and runs < max_runs:
        improved = False
        for cand in _candidates(current):
            cand["source"] = s["source"]
            sc.restamp(cand)
            try:
                sc.validate(cand)
            except sc.Invalid:
                continue
            runs += 1
            rec = _run(cand)
            if rec["verdict"] != "PASS" and signature(rec) == sig:
                current, improved = cand, True
                break
            if runs >= max_runs:
                break
    current["lineage"] = {**current.get("lineage", {}), "minimised_from": s["id"]}
    return sc.restamp(current), runs


def stable(s: dict, times: int = 3) -> bool:
    """FAST is deterministic by construction; this checks the construction."""
    traces = [json.dumps(_run(s).get("trace"), sort_keys=True) for _ in range(times)]
    return len(set(traces)) == 1


# ── the pipeline ─────────────────────────────────────────────────────────────

def triage(run_dir: Path, corpora: list[Path], known: set[str], *, minimise_top: int = 30) -> dict:
    from .runner import load_jsonl
    scenarios: dict[str, dict] = {}
    for f in corpora:
        for s in load_jsonl(f):
            scenarios[s["id"]] = s
    results = [r for f in sorted(run_dir.glob("results*.jsonl")) for r in load_jsonl(f)]
    clusters = cluster(results, scenarios, known)
    for c in clusters[:minimise_top]:
        s = scenarios.get(c["representative"])
        if s is None or c["class"] in ("lab", "generator", "known"):
            continue
        if not stable(s):
            c["class"] = "unstable"
            continue
        minimal, runs = minimise(s, c["signature"])
        c["minimal"] = minimal
        c["minimise_runs"] = runs
        c["minimal_size"] = len(sc.dumps(sc.semantic_key(minimal)))
        c["original_size"] = len(sc.dumps(sc.semantic_key(s)))
    return {
        "trials": len(results),
        "failures": sum(1 for r in results if r["verdict"] != "PASS"),
        "verdicts": _count(r["verdict"] for r in results),
        "clusters": clusters,
    }


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out
