"""
nightshift/tasks.py — la tâche de la nuit : une seule, choisie par le code.

    python -m nightshift.tasks --report DIR/report.json --out DIR [--github-output]

Ordre du plan (phase 2) :
  1. une régression ;
  2. un contrôle rouge qui n'est pas un échec connu (déjà rouge ou sans référence) ;
  3. une remarque ruff « vrai bug » : F821, F811, F841, F82x, E9xx, erreur de syntaxe ;
  4. (phase 3) un nœud bug ou partiel du graphe.
Une tâche n'est candidate que si ce qu'il faudrait corriger est dans la zone
autorisée (zone.py) : le paquet testé pour un contrôle, le fichier pour ruff.
Les autres sont listées « hors zone » : elles reviennent à un humain.

Rien à corriger → pas d'appel au modèle : c'est un bon résultat.
NIGHTLY_MAX_TASKS=0 coupe l'IA ; le veilleur, lui, continue.

PHASE 3 — la mémoire (memory.py) passe ce qui a déjà été tenté au même commit
avec le même contexte, ce qu'un humain a refusé (PR fermée) et les échecs
persistants ; priorities.toml force ou interdit une tâche. Le backlog (nœuds
bug et partiel des graphes) est affiché, pas confié au modèle : aucun contrôle
ne prouverait son correctif (principe n°3 du plan).

Écrit task.json (toujours : la tâche ou la raison de son absence, et ce qui a été
écarté) et prompt.json (seulement s'il y a une tâche : le contexte ciblé).
Tourne dans le job « veille », sans secret.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import context, memory, zone
from nightshift.suites import short

REPO = Path(__file__).resolve().parent.parent
REAL_BUG = re.compile(r"^(F821|F811|F841|F82\d|E9\d\d|syntax)$")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.]+", "-", str(text)).strip("-")[:60]


def _check_task(source: str, x: dict, report: dict, detail: str = "") -> dict:
    commit = report.get("commit") or "?"
    prefix = "reg" if source == "regression" else "fail"
    return {"id": f"{prefix}-{_slug(short(x['suite']))}-{_slug(x['check'])}-{commit}", "source": source,
            "suite": x["suite"], "check": str(x["check"]), "label": x.get("label", ""), "detail": detail,
            "since_commit": x.get("since_commit"), "suspects": x.get("suspects") or []}


def candidates(report: dict, repo: Path = REPO) -> tuple[list[dict], list[dict]]:
    """(candidates dans l'ordre du plan, écartées hors zone avec leur raison)."""
    details = {(s.get("name"), str(f.get("id"))): f.get("detail", "")
               for s in report.get("suites") or [] for f in s.get("failed") or []}
    found: list[dict] = []
    out: list[dict] = []

    def consider(task: dict, target: str | None) -> None:
        if task["id"] in {t["id"] for t in found}:
            return
        ok, why = zone.allowed(target) if target else (False, "paquet testé introuvable")
        if ok:
            task["files"] = [target if task["source"] == "ruff" else target.rsplit("/", 1)[0] + "/"]
            found.append(task)
        else:
            out.append({"id": task["id"], "source": task["source"], "reason": why})

    def pkg_target(suite: str) -> str | None:
        pkg = context.package_dir(suite, repo)
        return f"{pkg}__init__.py" if pkg else None

    for x in report.get("regressions") or []:
        if x.get("check") == "*":      # une suite qui ne s'exécute plus : pas de contrôle précis à donner
            out.append({"id": f"reg-{_slug(short(x['suite']))}-suite", "source": "regression",
                        "reason": "la suite entière ne s'exécute plus : diagnostic humain"})
            continue
        consider(_check_task("regression", x, report, details.get((x["suite"], str(x["check"])), "")),
                 pkg_target(x["suite"]))
    for kind in ("preexisting", "unreferenced"):
        for x in report.get(kind) or []:
            consider(_check_task("failing_check", x, report, details.get((x["suite"], str(x["check"])), "")),
                     pkg_target(x["suite"]))
    lint = report.get("lint") or {}
    for f in lint.get("findings") or []:
        if REAL_BUG.match(str(f.get("code"))):
            task = {"id": f"ruff-{f['code']}-{_slug(f['path'])}-{report.get('commit') or '?'}", "source": "ruff",
                    "code": f["code"], "path": f["path"], "line": f.get("line"), "message": f.get("message", "")}
            consider(task, f["path"])
    return found, out


def choose(report: dict, max_tasks: int = 1, repo: Path = REPO, state: dict | None = None,
           prs: list | None = None, prio: dict | None = None, outputs_dir: Path | None = None) -> dict:
    """{"task", "prompt", "reason", "out_of_zone", "skipped", "considered"} — la tâche ou pourquoi rien."""
    found, out = candidates(report, repo)
    base = {"task": None, "prompt": None, "out_of_zone": out, "skipped": [], "considered": len(found)}
    if max_tasks <= 0:
        return {**base, "reason": "IA coupée (NIGHTLY_MAX_TASKS=0)"}
    if report.get("status") == "failed" or not report.get("suites"):
        return {**base, "reason": "la veille n'a rien mesuré : pas de tâche", "considered": 0}
    if not found:
        return {**base, "reason": "rien à corriger" if not out else "rien à corriger dans la zone autorisée"}
    state, prio, by_url = state or memory.empty(), prio or {"force": [], "forbid": []}, memory.prs_by_url(prs)
    for task in memory.order(found, prio)[:10]:
        prompt = context.build(task, repo, outputs_dir)
        fp = memory.fingerprint(prompt)
        why = memory.skip_reason(task, report.get("commit") or "", fp, state, by_url, prio)
        if why:
            base["skipped"].append({"id": task["id"], "idea": memory.idea_key(task), "reason": why})
            continue
        task.update(fingerprint=fp, idea=memory.idea_key(task), prompt_tokens=prompt["estimated_tokens"],
                    sections=prompt["sections"])
        return {**base, "task": task, "prompt": prompt, "reason": ""}
    return {**base, "reason": "rien de neuf : les tâches possibles sont déjà tentées, refusées ou en échec persistant"}


def backlog(repo: Path = REPO) -> list[dict]:
    """Les nœuds bug et partiel des graphes : régression > bug > partiel, puis le moins de fichiers.

    Affichés pour l'humain (onglet NUIT, rapport), jamais confiés au modèle :
    aucun contrôle ne prouverait leur correctif. Lus dans les fichiers JSON des
    graphes : nightshift n'importe pas capabilities."""
    rows = []
    for f in sorted((repo / "capabilities" / "graphs").glob("*.json")):
        try:
            g = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for n in g.get("nodes") or []:
            if n.get("status") not in ("bug", "partiel"):
                continue
            files = [str(x) for x in n.get("files") or []]
            inzone = bool(files) and all(zone.allowed(x)[0] for x in files)
            rows.append({"graph": g.get("id", f.stem), "node": n.get("id"), "label": n.get("label", ""),
                         "status": n["status"], "summary": str(n.get("summary", ""))[:200], "files": files,
                         "zone": inzone, "owner": "humain" if not inzone else "humain (aucun contrôle ne le prouverait)"})
    rows.sort(key=lambda r: (0 if r["status"] == "bug" else 1, len(r["files"]) or 99, r["graph"], r["node"]))
    return rows


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.tasks")
    ap.add_argument("--report", required=True, help="report.json de la veille")
    ap.add_argument("--out", required=True, help="dossier où écrire task.json et prompt.json")
    ap.add_argument("--github-output", action="store_true", help="écrire has_task=… dans $GITHUB_OUTPUT")
    ap.add_argument("--state", help="state.json de la branche nightshift-data (la mémoire)")
    ap.add_argument("--prs", help="prs.json : les PR Night Shift lues par gh (tous états)")
    ap.add_argument("--priorities", help="priorities.toml (défaut : nightshift/priorities.toml)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state = memory.load(args.state)
    try:
        prs = json.loads(Path(args.prs).read_text(encoding="utf-8")) if args.prs else []
    except Exception:
        prs = []
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        choice = choose(report, _int_env("NIGHTLY_MAX_TASKS", 1), REPO, state, prs,
                        memory.load_priorities(args.priorities), out / "outputs")
        prompt = choice.pop("prompt")
        if prompt:
            (out / "prompt.json").write_text(json.dumps(prompt, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # pas de tâche plutôt qu'un plantage : la veille a déjà son rapport
        choice = {"task": None, "reason": f"sélection impossible : {type(e).__name__}: {e}"[:200],
                  "out_of_zone": [], "skipped": [], "considered": 0}
    choice["backlog"] = backlog(REPO)
    choice["diagnostics"] = memory.diagnostics(state)
    (out / "task.json").write_text(json.dumps(choice, ensure_ascii=False, indent=1), encoding="utf-8")
    t = choice["task"]
    print(f"Tâche de la nuit : {t['id']} ({t.get('prompt_tokens')} tokens de contexte)" if t
          else f"Pas de tâche : {choice['reason']}")
    for x in choice["out_of_zone"]:
        print(f"  hors zone : {x['id']} — {x['reason']}")
    for x in choice.get("skipped") or []:
        print(f"  mémoire : {x['id']} — {x['reason']}")
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"has_task={'true' if t else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
