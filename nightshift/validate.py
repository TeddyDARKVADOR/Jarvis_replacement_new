"""
nightshift/validate.py — le patch prouve-t-il ce qu'il prétend ? Les suites le disent.

    python -m nightshift.validate --veille DIR --proposal DIR --repo . --out DIR [--github-output]

Job « validation » : aucun secret, aucun jeton d'écriture. C'est le seul endroit
où le code écrit par le modèle s'exécute, d'où ces deux absences.

  1. zone.check_patch, de nouveau : la proposition a pu être altérée en route ;
  2. git apply, puis un commit LOCAL (jamais poussé d'ici) : les gardes
     git status de server, client_desktop et eval_lab l'exigent ;
  3. les mêmes suites que la veille, comparées à la veille du même commit ;
  4. verdict : validé seulement si la cible est réparée (le contrôle passe, ou
     la remarque ruff a disparu), sans aucune régression ni nouvelle remarque ;
  5. phase 4 : si un indicateur se dégrade (eval_lab, cognition, coût en
     contexte), le correctif reste validé mais il est « à relire de près ».

Écrit validation.json (verdict et preuves), after.json (le rapport des suites
après le patch) et pr_body.md : la description de la PR, tirée des résultats et
jamais de l'avis du modèle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import report as R
from nightshift import run, zone
from nightshift.suites import by_name, short

REPO = Path(__file__).resolve().parent.parent


def apply_and_commit(diff: str, repo: Path, message: str, files: list[str] | None = None) -> tuple[bool, str]:
    """git apply puis commit local des seuls fichiers du patch. (ok, erreur)."""
    try:
        p = subprocess.run(["git", "apply", "--recount", "--whitespace=nowarn", "-"], cwd=str(repo), input=diff,
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            return False, f"git apply : {run.redact(p.stderr.strip())[:300]}"
        subprocess.run(["git", "add", "-A", "--", *(files or ["."])], cwd=str(repo), capture_output=True,
                       timeout=60, check=True)
        p = subprocess.run(["git", "-c", "user.name=jarvis-nightshift", "-c", "user.email=nightshift@localhost",
                            "commit", "-q", "--no-verify", "-m", message], cwd=str(repo),
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            return False, f"git commit : {(p.stderr or p.stdout).strip()[:300]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:300]
    return True, ""


def target_fixed(task: dict, after: dict) -> tuple[bool, str]:
    """La cible de la tâche est-elle réparée dans le rapport d'après ?"""
    if task.get("source") == "ruff":
        lint = after.get("lint") or {}
        if not lint.get("available"):
            return False, "ruff n'a pas tourné après le patch"
        same = [f for f in lint.get("findings") or [] if (f.get("code"), f.get("path"), f.get("message"))
                == (task.get("code"), task.get("path"), task.get("message"))]
        return (not same), ("la remarque a disparu" if not same else "la remarque est toujours là")
    suite = next((s for s in after.get("suites") or [] if s.get("name") == task.get("suite")), None)
    if suite is None or suite.get("outcome", suite.get("status")) in ("error", "timeout", "skipped"):
        return False, f"{task.get('suite')} n'a pas tourné après le patch"
    still = str(task.get("check")) in {str(f.get("id")) for f in suite.get("failed") or []}
    return (not still), (f"le n°{task.get('check')} passe" if not still else f"le n°{task.get('check')} échoue encore")


def verdict(task: dict, after: dict) -> dict:
    fixed, why = target_fixed(task, after)
    regs = after.get("regressions") or []
    new_lint = (after.get("lint") or {}).get("new") or []
    ok = fixed and not regs and not new_lint
    reasons = [] if ok else [r for r in (
        None if fixed else why,
        f"{len(regs)} régression(s) : " + ", ".join(R._ref(x) for x in regs[:3]) if regs else None,
        f"{len(new_lint)} nouvelle(s) remarque(s) ruff" if new_lint else None) if r]
    return {"validated": ok, "target_fixed": fixed, "target": why, "regressions": regs, "new_lint": new_lint,
            "reason": " ; ".join(reasons), "review_closely": after.get("metrics_changes") or []}


def title(task: dict) -> str:
    if task.get("source") == "ruff":
        return f"Night Shift : {task.get('code')} dans {task.get('path')}"
    kind = "régression" if task.get("source") == "regression" else "contrôle rouge"
    return f"Night Shift : {kind} {short(task.get('suite', ''))} n°{task.get('check')}"


def _scores(rep: dict) -> dict[str, str]:
    out = {}
    for s in rep.get("suites") or []:
        score = f"{s.get('passed')}/{s.get('total')}" if s.get("total") is not None else "—"
        red = [str(f.get("id")) for f in s.get("failed") or [] if f.get("class") != "known"]
        known = [str(f.get("id")) for f in s.get("failed") or [] if f.get("class") == "known"]
        out[s["name"]] = score + (f" (n°{', '.join(red)} rouge)" if red else "") + (
            f" (n°{', '.join(known)} connu)" if known else "") + ("" if s.get("status") != "skipped" else " (ignorée)")
    return out


def pr_body(task: dict, proposal: dict, before: dict, after: dict, v: dict, files: list[str]) -> str:
    """La description de la PR : des résultats, pas d'opinion."""
    b, a = _scores(before), _scores(after)
    if task.get("source") == "ruff":
        problem = f"ruff signale **{task.get('code')}** dans `{task.get('path')}:{task.get('line')}` : {task.get('message')}"
    else:
        origin = {"regression": f"régression depuis `{task.get('since_commit') or '?'}`",
                  "failing_check": "contrôle rouge"}.get(task.get("source"), task.get("source"))
        problem = (f"**{task.get('suite')}**, contrôle n°{task.get('check')} « {task.get('label')} » — {origin}.\n\n"
                   f"Symptôme : `{(task.get('detail') or '').strip()[:300] or 'voir le rapport de la nuit'}`")
    lines = [
        "## Le problème", "", problem, "",
        "## La preuve", "",
        f"- Cible : {v['target']}.",
        "- Toutes les suites relancées sur le commit corrigé, comparées à la même nuit sans le correctif : "
        + ("aucune régression, aucune nouvelle remarque ruff." if v["validated"] else v["reason"]),
        "", "| Suite | Avant | Après |", "|---|---|---|",
        *[f"| {name} | {b.get(name, '—')} | {a.get(name, '—')} |" for name in a],
        *(["", "## ⚠️ À relire de près", "",
            "Les suites sont vertes, mais un indicateur a bougé (phase 4) :", "",
            *[f"- **{x['metric']}** : {x['before']} → {x['now']} ({x['verdict']})"
              + (f" — {x['detail']}" if x.get("detail") else "") for x in v.get("review_closely") or []]]
          if v.get("review_closely") else []),
        "", "## Fichiers touchés", "", *[f"- `{f}`" for f in files], "",
        "## Ce qu'il faut relire", "",
        "- Le diff, en entier : il est court par construction (zone autorisée, tests intouchables).",
        f"- Proposé par {proposal.get('attempts', [{}])[-1].get('model', '?')} en "
        f"{len(proposal.get('attempts') or [])} tentative(s). Cette description vient des résultats des "
        "suites, pas du modèle.",
        *[f"- Limite : {x}" for x in (after.get("limitations") or [])[:5]],
        "", f"Exécution : {after.get('run_url') or 'locale'}",
        "", "_Ouverte par le veilleur de nuit (Night Shift, phase 2). Rien n'est fusionné sans vous._",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.validate")
    ap.add_argument("--veille", required=True, help="dossier de la veille (report.json, task.json)")
    ap.add_argument("--proposal", required=True, help="dossier de la proposition (proposal.json, patch.diff)")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--out", required=True)
    ap.add_argument("--github-output", action="store_true", help="écrire validated=true|false dans $GITHUB_OUTPUT")
    args = ap.parse_args(argv)
    veille, prop_dir, out, repo = Path(args.veille), Path(args.proposal), Path(args.out), Path(args.repo)
    out.mkdir(parents=True, exist_ok=True)
    task = json.loads((veille / "task.json").read_text(encoding="utf-8"))["task"] or {}
    before = json.loads((veille / "report.json").read_text(encoding="utf-8"))
    proposal = json.loads((prop_dir / "proposal.json").read_text(encoding="utf-8"))
    diff = (prop_dir / "patch.diff").read_text(encoding="utf-8") if (prop_dir / "patch.diff").is_file() else ""
    result: dict = {"task_id": task.get("id"), "validated": False, "files": [], "title": title(task),
                    # la publication vérifie qu'elle publie exactement ce patch-ci
                    "patch_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else None}

    check = zone.check_patch(diff)
    if not check.ok:
        result["reason"] = "patch rejeté avant toute exécution — " + " ; ".join(check.reasons)
    else:
        result["files"] = check.files
        ok, err = apply_and_commit(diff, repo, title(task), check.files)
        if not ok:
            result["reason"] = f"le patch ne s'applique pas : {err}"
        else:
            print(f"Patch appliqué et commité localement ({', '.join(check.files)}) : les suites tournent.")
            after = run.night(by_name(), before, repo=repo, output_dir=out / "outputs")
            (out / "after.json").write_text(R.dumps(after), encoding="utf-8")
            v = verdict(task, after)
            result.update(v)
            (out / "pr_body.md").write_text(pr_body(task, proposal, before, after, v, check.files), encoding="utf-8")
    print(f"Verdict : {'VALIDÉ' if result['validated'] else 'rejeté'}"
          + (f" — {result.get('reason')}" if result.get("reason") else ""))
    (out / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"validated={'true' if result['validated'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
