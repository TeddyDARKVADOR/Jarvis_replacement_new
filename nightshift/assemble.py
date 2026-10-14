"""
nightshift/assemble.py — un seul rapport pour toute la nuit.

    python -m nightshift.assemble --veille DIR [--proposal DIR] [--validation DIR]
                                  [--publish DIR] --dry-run true|false --out DIR

Job « assemblage » : aucun secret, lecture seule. Il réunit ce que chaque job a
laissé — la veille (report.json, task.json), la proposition (proposal.json), la
validation (validation.json), la publication (publish.json) — en un rapport
jarvis.nightly/1 complet : la tâche, son issue, la PR ou la raison de son
absence, la consommation comparée aux budgets. Le job « rapport », qui écrit sur
nightshift-data, n'a plus qu'à pousser le fichier : il n'exécute aucun code.

Une pièce manquante (job sauté ou en échec) n'empêche rien : le rapport dit ce
qui manque au lieu de l'inventer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import report as R


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def task_section(choice: dict | None, proposal: dict | None, validation: dict | None,
                 publish: dict | None, dry_run: bool) -> tuple[dict, str | None]:
    """(r["task"], pull_request_url) selon ce que chaque job a produit."""
    choice = choice or {"task": None, "reason": "sélection absente (la veille a-t-elle échoué ?)", "out_of_zone": []}
    t = choice.get("task")
    out_of_zone = choice.get("out_of_zone") or []
    if not t:
        return {"id": None, "source": None, "files": [], "attempts": 0, "outcome": "skipped",
                "reason": choice.get("reason") or "rien à corriger", "out_of_zone": out_of_zone}, None
    task = {k: t.get(k) for k in ("id", "source", "suite", "check", "label", "code", "path", "line") if t.get(k)}
    task.update(files=t.get("files") or [], attempts=0, out_of_zone=out_of_zone)
    if proposal is None:
        task.update(outcome="no_patch", reason="la proposition n'a pas abouti (job sauté ou en échec)")
        return task, None
    task["attempts"] = len(proposal.get("attempts") or [])
    models = [a.get("model") for a in proposal.get("attempts") or [] if a.get("model")]
    if models:
        task["model"] = models[-1]
    if proposal.get("outcome") != "patch_proposed":
        task.update(outcome="no_patch", reason=proposal.get("reason") or "aucun patch")
        return task, None
    task["files"] = proposal.get("files") or task["files"]
    if validation is None:
        task.update(outcome="patch_rejected", reason="la validation n'a pas abouti (job sauté ou en échec)")
        return task, None
    if not validation.get("validated"):
        task.update(outcome="patch_rejected", reason=validation.get("reason") or "les suites ne le prouvent pas")
        return task, None
    task.update(outcome="patch_validated", reason=validation.get("target") or "")
    if dry_run:
        task["publication"] = "aucune PR : dry run (NIGHTLY_DRY_RUN=true)"
        return task, None
    if publish is None:
        task["publication"] = "publication absente (job sauté ou en échec)"
        return task, None
    if publish.get("published"):
        task["publication"] = "PR ouverte"
        return task, publish.get("url")
    if publish.get("reason") == "pr_already_open":
        task["publication"] = f"une PR Night Shift est déjà ouverte ({publish.get('url')}) : rien de publié"
        return task, None
    task["publication"] = f"publication impossible : {publish.get('reason') or '?'}"
    return task, None


def assemble(veille: Path, proposal: Path | None, validation: Path | None, publish: Path | None,
             dry_run: bool) -> dict:
    report = _load(veille / "report.json")
    if report is None:
        report = R.failed({"run_id": os.environ.get("GITHUB_RUN_ID")}, {}, "rapport de la veille absent")
    task, url = task_section(_load(veille / "task.json"), _load(proposal / "proposal.json") if proposal else None,
                             _load(validation / "validation.json") if validation else None,
                             _load(publish / "publish.json") if publish else None, dry_run)
    report["task"] = task
    report["pull_request_url"] = url
    prop = _load(proposal / "proposal.json") if proposal else None
    if prop and isinstance(prop.get("usage"), dict):
        report["usage"] = prop["usage"]
    report["budgets"] = R.budgets()
    report["dry_run"] = dry_run
    report["summary"] = R.summarize(report)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.assemble")
    ap.add_argument("--veille", required=True)
    ap.add_argument("--proposal")
    ap.add_argument("--validation")
    ap.add_argument("--publish")
    ap.add_argument("--dry-run", default="true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    opt = lambda p: Path(p) if p and Path(p).is_dir() else None   # noqa: E731
    report = assemble(Path(args.veille), opt(args.proposal), opt(args.validation), opt(args.publish),
                      args.dry_run != "false")
    md = R.to_markdown(report)
    R.write(report, md, Path(args.out))
    R.step_summary(md)
    print(report["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
