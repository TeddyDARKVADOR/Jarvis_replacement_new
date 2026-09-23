"""
eval_lab/runner.py — run scenarios, keep everything, decide nothing hastily.

THE VERDICTS, AND WHOSE PROBLEM EACH ONE IS
    PASS      the trace satisfies the oracle and every property.
    FAIL      JARVIS's answer contradicts the oracle or a property.
              A candidate bug — not yet a bug: the oracle may be wrong.
    CRASH     JARVIS's own code raised (world.SutCrash). A bug in JARVIS
              until shown otherwise.
    INVALID   the scenario is malformed or describes an impossible world.
              The generator's problem. JARVIS never ran.
    INFRA     the lab itself broke (simulator, Node, a surface). The lab's
              problem. Counted apart so it can never inflate FAIL.
    INCONCLUSIVE  only a property the project has explicitly left undecided
              is involved (properties.py, strength "inconclusive"). No
              behaviour is imposed; the trial is neither PASS nor FAIL.

    A scenario is judged in that order, and the first applicable verdict
    wins: an invalid scenario is never "also" a failure.

RESUMABLE BY CONSTRUCTION
    Results are appended one JSON line per trial and flushed. A run that is
    interrupted is resumed by reading the ids already present and skipping
    them; the scenario id is content-derived, so "already run" cannot be
    confused with "a different scenario with the same name".
"""
from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import scenario as sc
from .world import SutCrash

VERDICTS = ("PASS", "FAIL", "INCONCLUSIVE", "CRASH", "INVALID", "INFRA")


@dataclass
class Summary:
    counts: dict = field(default_factory=lambda: {v: 0 for v in VERDICTS})
    skipped: int = 0
    seconds: float = 0.0

    def add(self, verdict: str) -> None:
        self.counts[verdict] += 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def line(self) -> str:
        parts = " ".join(f"{k}={v}" for k, v in self.counts.items())
        rate = self.total / self.seconds if self.seconds else 0.0
        return (f"{self.total} essais ({self.skipped} deja faits) en {self.seconds:.1f} s "
                f"— {rate:.0f}/s — {parts}")


def verdict_of(problems: list[dict]) -> str:
    """Soft-property warnings ride along on a PASS; they never make a FAIL."""
    kinds = {p["kind"] for p in problems}
    if "infra" in kinds:
        return "INFRA"
    if kinds & {"expected", "forbidden", "property", "relation"}:
        return "FAIL"
    if "inconclusive" in kinds:
        return "INCONCLUSIVE"
    return "PASS"


def judge(s: dict, trace: dict) -> tuple[str, list[dict]]:
    """Oracle + properties. Pure: the trace is read, never modified."""
    from .properties import check_all
    problems = sc.diff(s, trace) + check_all(s, trace)
    return verdict_of(problems), problems


def _record(s: dict, verdict: str, *, trace=None, problems=None, error=None,
            ms: float = 0.0) -> dict:
    return {
        "id": s.get("id"), "surface": s.get("surface"), "source": s.get("source"),
        "family": s.get("family"), "verdict": verdict,
        "problems": problems or [], "error": error, "trace": trace, "ms": round(ms, 3),
        "legacy_ref": (s.get("lineage") or {}).get("legacy_ref"),
    }


def run_one(s: dict) -> dict:
    """Validate, execute, judge — one non-face scenario."""
    from .surfaces import RUNNERS
    t0 = time.perf_counter()
    try:
        sc.validate(s)
    except sc.Invalid as e:
        return _record(s, "INVALID", error=list(e.args[0]) if e.args else [str(e)])
    try:
        trace = RUNNERS[s["surface"]](s)
    except SutCrash as e:
        return _record(s, "CRASH", error=str(e), ms=(time.perf_counter() - t0) * 1000)
    except sc.Invalid as e:
        # Only discoverable by running it (time asked to run backwards...):
        # still the scenario's fault, still not a verdict about JARVIS.
        return _record(s, "INVALID", error=list(e.args[0]) if e.args else [str(e)])
    except Exception as e:          # noqa: BLE001 — anything else is the lab's
        return _record(s, "INFRA", error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}",
                       ms=(time.perf_counter() - t0) * 1000)
    verdict, problems = judge(s, trace)
    return _record(s, verdict, trace=trace, problems=problems, ms=(time.perf_counter() - t0) * 1000)


def _run_face(batch: list[dict]) -> list[dict]:
    from .face import FaceInfraError, run_batch
    valid, out = [], {}
    for s in batch:
        try:
            sc.validate(s)
            valid.append(s)
        except sc.Invalid as e:
            out[s["id"]] = _record(s, "INVALID", error=list(e.args[0]))
    if valid:
        t0 = time.perf_counter()
        try:
            traces = run_batch(valid)
        except FaceInfraError as e:
            for s in valid:
                out[s["id"]] = _record(s, "INFRA", error=str(e))
            traces = None
        if traces is not None:
            per = (time.perf_counter() - t0) * 1000 / len(valid)
            for s, t in zip(valid, traces):
                if not t.get("ok"):
                    out[s["id"]] = _record(s, "CRASH", error=t.get("error"), ms=per)
                    continue
                verdict, problems = judge(s, t)
                out[s["id"]] = _record(s, verdict, trace=t, problems=problems, ms=per)
    return [out[s["id"]] for s in batch]


def done_ids(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    ids = set()
    with results_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue            # a torn last line from an interrupted run
    return ids


def run(scenarios, results_path: Path, *, resume: bool = True,
        shard: tuple[int, int] = (0, 1), on_result=None,
        keep_pass_traces: bool = True, tiers: tuple[str, ...] = ("fast", "full")) -> Summary:
    """Run `scenarios` (any iterable), appending to `results_path`.

    `shard=(k, n)` keeps only scenarios whose id hashes to k mod n, so n
    processes (or machines) can split a corpus with no coordination.

    `on_result(scenario, record)` sees the FULL record, trace included, before
    anything is trimmed — coverage is computed there. With
    `keep_pass_traces=False` a PASS is stored as id + verdict + warnings: at a
    million trials the traces of what worked are most of the disk, and a PASS
    is reproducible from its scenario anyway.
    """
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    seen = done_ids(results_path) if resume else set()
    summary = Summary()
    k, n = shard
    t0 = time.perf_counter()
    face_batch: list[dict] = []

    # A run killed mid-write leaves a torn last line. Appending straight after
    # it would glue the first new record onto it and lose that one too.
    if resume and results_path.exists() and results_path.stat().st_size:
        with results_path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            torn = fh.read(1) != b"\n"
        if torn:
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write("\n")

    with results_path.open("a" if resume else "w", encoding="utf-8") as out:
        def emit(s: dict, rec: dict) -> None:
            if on_result:
                on_result(s, rec)
            if not keep_pass_traces and rec["verdict"] == "PASS":
                rec = {k: v for k, v in rec.items() if k != "trace"}
            out.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            summary.add(rec["verdict"])

        def flush_face() -> None:
            for fs, rec in zip(face_batch, _run_face(face_batch)):
                emit(fs, rec)
            face_batch.clear()
            out.flush()

        for s in scenarios:
            sid = s.get("id") or ""
            if n > 1 and int(sc.fingerprint(s)[:8], 16) % n != k:
                continue
            # REAL is never run here, whatever `tiers` says: it needs a person,
            # a phone, a microphone. See `python -m eval_lab real`.
            if s.get("tier", "fast") not in tiers or s.get("tier") == "real":
                continue
            if sid in seen:
                summary.skipped += 1
                continue
            seen.add(sid)          # a duplicate inside the input runs once
            if s.get("surface") == "face":
                face_batch.append(s)
                if len(face_batch) >= 800:
                    flush_face()
                continue
            emit(s, run_one(s))
        if face_batch:
            flush_face()
        out.flush()
        os.fsync(out.fileno())
    summary.seconds = time.perf_counter() - t0
    return summary


def load_jsonl(path: Path):
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
