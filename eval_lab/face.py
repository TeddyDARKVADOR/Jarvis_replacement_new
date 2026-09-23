"""
eval_lab/face.py — face scenarios, judged by the avatar's own invariants.

One Node process per batch, not per scenario: starting Node costs ~50 ms and
a case costs ~3 ms, so batching is the difference between 5 s and 90 s for
the 1 632 legacy situations. Node absent is an infrastructure fact and is
reported as such — never as 1 632 failures of JARVIS.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "js" / "face_runner.mjs"
BATCH = 800


class FaceInfraError(RuntimeError):
    pass


def node_available() -> bool:
    return shutil.which("node") is not None


def run_batch(scenarios: list[dict]) -> list[dict]:
    """Traces in the same order as `scenarios`. Raises FaceInfraError on a
    runner problem (no Node, bad output) — which is the lab's, not JARVIS's."""
    if not node_available():
        raise FaceInfraError("node introuvable : la surface face ne peut pas tourner ici")
    traces: list[dict] = []
    for start in range(0, len(scenarios), BATCH):
        chunk = scenarios[start:start + BATCH]
        cases = [{"i": i, **s["stimulus"]} for i, s in enumerate(chunk)]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(cases, fh)
            path = fh.name
        try:
            proc = subprocess.run(["node", str(RUNNER), path], capture_output=True,
                                  text=True, encoding="utf-8", timeout=600)
        finally:
            Path(path).unlink(missing_ok=True)
        if proc.returncode != 0:
            raise FaceInfraError(f"face_runner a echoue ({proc.returncode}) : {proc.stderr[-400:]}")
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        if [ln["i"] for ln in lines] != list(range(len(chunk))):
            raise FaceInfraError("face_runner : sortie incomplete ou desordonnee")
        traces.extend(lines)
    return traces
