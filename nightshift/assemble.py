"""
nightshift/assemble.py — un seul rapport pour toute la nuit.

    python -m nightshift.assemble --veille DIR [--proposal DIR] [--validation DIR]
                                  [--publish DIR] --dry-run true|false --out DIR

Job « assemblage » : aucun secret, lecture seule. Il réunit ce que chaque job a
laissé — la veille (report.json, task.json), la proposition (proposal.json), la
validation (validation.json), la publication (publish.json) — en un rapport
matthew_assistant.nightly/1 complet : la tâche, son issue, la PR ou la raison de son
absence, la consommation comparée aux budgets. Le job « rapport », qui écrit sur
nightshift-data, n'a plus qu'à pousser le fichier : il n'exécute aucun code.

Une pièce manquante (job sauté ou en échec) n'empêche rien : le rapport dit ce
qui manque au lieu de l'inventer.

PHASE 3 — il tient aussi la mémoire (memory.py) : la tentative de la nuit, les
verdicts des PR, la nuit elle-même, écrits dans state.json que le job
« rapport » pousse sur nightshift-data (nuits de main seulement). Le rapport
porte les tâches passées par la mémoire, les dossiers de diagnostic des échecs
persistants, le backlog du graphe et, le dimanche, le résumé de la semaine.

PASSES — la nuit en compte jusqu'à trois (passes.py). Le rapport porte le
numéro de sa passe et « tonight » : toutes les passes de la nuit lues dans la
mémoire, pour que le rapport de la dernière — celui que Matthew lit le matin —
dise ce que la nuit entière a fait.

PHASE 5 — il écrit aussi queue.json, la file du PC réveillé la nuit
(local_worker.py) : des tâches d'avis seulement (résumer les échecs, trier les
remarques ruff), tirées du rapport public. Et il reprend les derniers avis du
PC (local/latest.json) comme des hypothèses — ou « indisponible » : la nuit ne
compte jamais sur le PC.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime

from nightshift import memory
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
    task.update(outcome="patch_validated", reason=validation.get("target") or "",
                review_closely=validation.get("review_closely") or [])
    if dry_run:
        task["publication"] = "aucune PR : dry run (NIGHTLY_DRY_RUN=true)"
        return task, None
    if publish is None:
        task["publication"] = "publication absente (job sauté ou en échec)"
        return task, None
    if publish.get("published"):
        task["publication"] = ("PR fusionnée automatiquement sur la branche du run" if publish.get("merged") else
                               "PR ouverte" + (f" (pas de fusion automatique : {publish['merge_reason']})"
                                               if publish.get("merge_reason") else ""))
        task["merged"] = bool(publish.get("merged"))
        return task, publish.get("url")
    if publish.get("reason") == "pr_already_open":
        task["publication"] = f"une PR Night Shift est déjà ouverte ({publish.get('url')}) : rien de publié"
        return task, None
    if publish.get("reason") == "pr_limit":
        task["publication"] = (f"{publish.get('open')} PR Night Shift attendent déjà leur relecture "
                               f"(plafond NIGHTLY_MAX_OPEN_PRS) : rien de publié")
        return task, None
    task["publication"] = f"publication impossible : {publish.get('reason') or '?'}"
    return task, None


def _load_list(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


QUEUE_SCHEMA = "matthew_assistant.nightshift-queue/1"
LOCAL_SCHEMA = "matthew_assistant.nightshift-local/1"
LOCAL_SCHEMAS = (LOCAL_SCHEMA, "jarvis.nightshift-local/1")   # ancien nom, encore lu


def queue_for(report: dict) -> dict:
    """La file du PC pour la nuit prochaine : des avis à donner, rien à exécuter."""
    tasks = []
    red = [{"suite": s.get("name"), "status": s.get("status"),
            "failures": [{"check": f.get("id"), "label": f.get("label"), "detail": f.get("detail")}
                         for f in s.get("failed") or [] if f.get("class") != "known"]}
           for s in report.get("suites") or [] if s.get("status") in ("regression", "failing", "error", "timeout")]
    if red:
        tasks.append({"id": f"resume-{report.get('run_id')}", "kind": "resume",
                      "input": {"summary": report.get("summary"), "red": red[:10]}})
    findings = [{k: f.get(k) for k in ("code", "path", "line", "message")}
                for f in (report.get("lint") or {}).get("findings") or []]
    if findings:
        tasks.append({"id": f"triage-{report.get('run_id')}", "kind": "triage", "input": {"findings": findings[:40]}})
    return {"schema": QUEUE_SCHEMA, "run_id": report.get("run_id"), "created": report.get("finished_at"),
            "tasks": tasks}


def local_section(batch: dict | None) -> dict:
    """Les derniers avis du PC, ou « indisponible » — jamais « en attente »."""
    if not isinstance(batch, dict) or batch.get("schema_version") not in LOCAL_SCHEMAS:
        return {"available": False,
                "reason": "aucun avis local : PC éteint, ou worker non installé (phase 5, facultative)"}
    return {"available": True, "status": batch.get("status"), "host": batch.get("host"),
            "finished_at": batch.get("finished_at"), "model": batch.get("model"), "error": batch.get("error"),
            "tasks": [{"task_id": t.get("task_id"), "kind": t.get("kind"), "status": t.get("status"),
                       "output": str(t.get("output") or "")[:800], "error": t.get("error")}
                      for t in (batch.get("tasks") or [])[:6] if isinstance(t, dict)]}


INCIDENTS_SCHEMA = "matthew_assistant.incidents/1"
INCIDENTS_SCHEMAS = (INCIDENTS_SCHEMA, "jarvis.incidents/1")
INCIDENTS_STALE_DAYS = 3


def incidents_section(digest: dict | None, today=None) -> dict:
    """Les erreurs réelles partagées par Matthew (paquet incidents/), les plus fréquentes d'abord. Affichées,
    jamais confiées au modèle : aucun contrôle ne prouverait le correctif (principe n°3). Pur."""
    if not isinstance(digest, dict) or digest.get("schema") not in INCIDENTS_SCHEMAS:
        return {"available": False, "reason": "pas de résumé partagé (incidents_share désactivé, ou Matthew pas lancé)"}
    rows = [r for r in digest.get("incidents") or [] if isinstance(r, dict)]
    rows.sort(key=lambda r: (int(r.get("count") or 0), int(r.get("days") or 0)), reverse=True)
    out = {"available": True, "since": digest.get("since"), "until": digest.get("until"), "total": len(rows),
           "top": [{k: r.get(k) for k in ("signature", "kind", "exc", "where", "message", "count", "days", "last")}
                   for r in rows[:8]]}
    try:
        age = ((today or memory.today_paris()) - datetime.fromisoformat(str(digest.get("until"))).date()).days
    except (TypeError, ValueError):
        age = None
    if age is not None and age > INCIDENTS_STALE_DAYS:
        out["stale"] = f"résumé vieux de {age} jours (Matthew n'a pas tourné, ou le partage échoue)"
    return out


def pass_info() -> dict:
    """La passe de ce run, transmise par le workflow (job « passe »)."""
    def num(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name) or default)
        except ValueError:
            return default
    return {"n": num("NIGHTSHIFT_PASS", 0), "of": num("NIGHTSHIFT_PASSES", 1)}


def _night_date(report: dict):
    try:
        return datetime.fromisoformat(str(report.get("started_at"))).date()
    except (TypeError, ValueError):
        return memory.today_paris()


def assemble(veille: Path, proposal: Path | None, validation: Path | None, publish: Path | None,
             dry_run: bool) -> tuple[dict, dict | None]:
    """(le rapport de la nuit, la mémoire après la nuit).

    Sans rapport de la veille, la mémoire vaut None : elle voyage avec la veille,
    donc elle n'a pas été lue, et la réécrire depuis une mémoire vide effacerait
    les nuits et les idées déjà tentées. nightshift-data garde l'ancienne."""
    report = _load(veille / "report.json")
    measured = report is not None
    if report is None:
        report = R.failed({"run_id": os.environ.get("GITHUB_RUN_ID")}, {}, "rapport de la veille absent")
    choice = _load(veille / "task.json")
    task, url = task_section(choice, _load(proposal / "proposal.json") if proposal else None,
                             _load(validation / "validation.json") if validation else None,
                             _load(publish / "publish.json") if publish else None, dry_run)
    task["skipped"] = (choice or {}).get("skipped") or []
    report["task"] = task
    report["pull_request_url"] = url
    prop = _load(proposal / "proposal.json") if proposal else None
    if prop and isinstance(prop.get("usage"), dict):
        report["usage"] = prop["usage"]
    report["budgets"] = R.budgets()
    report["dry_run"] = dry_run
    report["backlog"] = (choice or {}).get("backlog") or []
    report["pass"] = pass_info()
    state = memory.update(memory.load(veille / "state.json"), report, (choice or {}).get("task"),
                          _load_list(veille / "prs.json"))
    report["diagnostics"] = memory.diagnostics(state)
    report["week"] = memory.week(state, _night_date(report))
    report["tonight"] = memory.tonight(state, str(report.get("started_at") or "")[:10])
    report["local"] = local_section(_load(veille / "local.json"))
    report["incidents"] = incidents_section(_load(veille / "incidents.json"))
    report["summary"] = R.summarize(report)
    return report, state if measured else None


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
    report, state = assemble(Path(args.veille), opt(args.proposal), opt(args.validation), opt(args.publish),
                             args.dry_run != "false")
    md = R.to_markdown(report)
    R.write(report, md, Path(args.out))
    if state is not None:   # sans state.json, le job « rapport » ne touche pas à la mémoire
        R._atomic_write(Path(args.out) / "state.json", json.dumps(state, ensure_ascii=False, indent=1) + "\n")
        R._atomic_write(Path(args.out) / "queue.json",
                        json.dumps(queue_for(report), ensure_ascii=False, indent=1) + "\n")
    R.step_summary(md)
    print(report["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
