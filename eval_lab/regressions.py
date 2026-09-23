"""
eval_lab/regressions.py — a confirmed bug becomes a permanent scenario.

WHAT A REGRESSION IS HERE
    The minimal reproducer of a cluster, promoted by a human (`promote`),
    with a stable id (REG-0001…), the commit that discovered it, and a status:

        open    the bug is still there. The scenario is EXPECTED to fail with
                its signature. If it starts passing, `check` says "fixed?" —
                it does not flip the status itself.
        fixed   the bug was fixed. The scenario must now pass, forever. A
                failure is a regression in the plain sense of the word.

    Promotion is never automatic. The lab proposes (every jarvis-candidate
    cluster, with its minimal reproducer); a person decides it is a bug. That
    is the line between "JARVIS is wrong" and "the lab thinks so".

NOTHING DISAPPEARS SILENTLY
    manifest.json maps each id to the fingerprint of its scenario. It is
    append-only: the selftest compares it with the committed version and fails
    if an id vanished or its scenario changed. Retiring a regression means
    editing the manifest in a commit that says why.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
from pathlib import Path

from . import scenario as sc

DIR = Path(__file__).resolve().parent / "corpus" / "regressions"
CORPUS = DIR / "regressions.jsonl"
MANIFEST = DIR / "manifest.json"


def load() -> list[dict]:
    if not CORPUS.exists():
        return []
    return [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def known_signatures() -> set[str]:
    return {s["lineage"]["regression"]["signature"] for s in load()}


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=DIR.parent.parent.parent,
                              capture_output=True, text=True).stdout.strip() or "?"
    except OSError:
        return "?"


def promote(cluster: dict, *, title: str, component: str, run: str, status: str = "open") -> dict:
    """Write the cluster's minimal reproducer into the regression corpus."""
    if status not in ("open", "fixed"):
        raise ValueError("status: open | fixed")
    s = cluster.get("minimal")
    if s is None:
        raise ValueError(f"{cluster['cluster']} n'a pas de reproducteur minimal")
    items, man = load(), manifest()
    if cluster["signature"] in {x["lineage"]["regression"]["signature"] for x in items}:
        raise ValueError("cette signature est deja une regression")
    reg_id = f"REG-{len(man) + 1:04d}"
    s = json.loads(json.dumps(s))
    s["source"] = "regression"
    s["family"] = f"regression.{reg_id}"
    s["lineage"]["regression"] = {
        "id": reg_id, "status": status, "signature": cluster["signature"], "title": title,
        "component": component, "first_invariant": cluster.get("first_invariant"),
        "discovered": {"commit": _commit(), "date": _dt.date.today().isoformat(), "run": run,
                       "cluster": cluster["cluster"], "cluster_size": cluster["size"]},
    }
    sc.restamp(s)
    sc.validate(s)
    DIR.mkdir(parents=True, exist_ok=True)
    with CORPUS.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(sc.dumps(s) + "\n")
    man[reg_id] = sc.fingerprint(s)
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return s


def check() -> list[dict]:
    """Run every regression and say what its status implies."""
    from .runner import _run_face, run_one
    from .triage import signature
    out = []
    for s in load():
        reg = s["lineage"]["regression"]
        rec = _run_face([s])[0] if s["surface"] == "face" else run_one(s)
        same = rec["verdict"] != "PASS" and signature(rec) == reg["signature"]
        if reg["status"] == "open":
            state = "open (still failing)" if same else \
                "fixed? (passes now - confirm, then set status fixed)" if rec["verdict"] == "PASS" else \
                "open, but fails DIFFERENTLY - look"
        else:
            state = "ok (stays fixed)" if rec["verdict"] == "PASS" else "REGRESSED"
        out.append({"id": reg["id"], "title": reg["title"], "status": reg["status"],
                    "verdict": rec["verdict"], "state": state})
    return out


def check_manifest(committed: dict | None) -> list[str]:
    """Problems with the corpus vs. its manifest (and vs. the committed one)."""
    problems = []
    items = {s["lineage"]["regression"]["id"]: s for s in load()}
    man = manifest()
    for reg_id, fp in man.items():
        if reg_id not in items:
            problems.append(f"{reg_id} dans le manifeste mais absent du corpus")
        elif sc.fingerprint(items[reg_id]) != fp:
            problems.append(f"{reg_id} : le scenario a change depuis sa promotion")
    for reg_id in items:
        if reg_id not in man:
            problems.append(f"{reg_id} dans le corpus mais absent du manifeste")
    for reg_id, fp in (committed or {}).items():
        if reg_id not in man:
            problems.append(f"{reg_id} a disparu du manifeste depuis le dernier commit")
        elif man[reg_id] != fp:
            problems.append(f"{reg_id} : empreinte modifiee depuis le dernier commit")
    return problems
