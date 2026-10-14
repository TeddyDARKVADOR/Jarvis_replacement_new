"""
nightshift/propose.py — demander UN correctif au modèle, et le vérifier sans l'exécuter.

    python -m nightshift.propose --veille DIR --repo . --out DIR [--github-output]

Le seul code qui voit GROQ_API_KEY (job « proposition », une seule étape). Il ne
lance aucun test et n'exécute rien de ce que le modèle écrit : il lit sa réponse
comme une donnée.

Au plus 2 tentatives. Chacune :
  1. appel à la chaîne gratuite (llm.py), sous budget ;
  2. extraction du diff (bloc ```diff, sinon lignes « --- a/ ») — « NO_PATCH »
     ou rien de reconnaissable : tentative ratée ;
  3. zone.check_patch : un seul chemin hors zone et le patch est rejeté entier
     (main.py, core/, tests, nightshift/…), sans jamais toucher au dépôt ;
  4. reanchor.repair : les en-têtes @@ refaits d'après le contenu des blocs
     (les modèles écrivent « @@ ... @@ » ou de faux numéros — c'est ce qui a
     fait échouer le premier vrai run, le 2026-10-14) ;
  5. git apply --check --recount : le patch s'applique-t-il ? (vérification
     seule, le dépôt n'est pas modifié).
Une tentative ratée renvoie l'erreur exacte au modèle pour la suivante. Chaque
tentative garde un extrait de la réponse du modèle (passé par redact) : sans
lui, un échec ne se diagnostique pas.

Écrit proposal.json (toujours : issue, raison, tentatives, consommation) et
patch.diff (seulement si un patch est proposé).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import llm, reanchor, zone
from nightshift.run import redact

MAX_ATTEMPTS = 2
_FENCE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)


def extract_diff(text: str) -> tuple[str | None, str]:
    """(diff, raison) — None si la réponse n'en contient pas."""
    text = (text or "").strip()
    if not text:
        return None, "réponse vide"
    m = re.match(r"^NO_PATCH\s*:?\s*(.*)", text, re.IGNORECASE)
    if m:
        return None, f"le modèle renonce : {m.group(1).strip()[:200] or 'sans raison'}"
    for block in _FENCE.findall(text):
        if re.search(r"^(---|\+\+\+) \S", block, re.MULTILINE):
            return zone.normalize(block), ""
    if re.search(r"^--- \S", text, re.MULTILINE) and re.search(r"^\+\+\+ \S", text, re.MULTILINE):
        start = re.search(r"^(diff --git|--- )", text, re.MULTILINE).start()
        return zone.normalize(text[start:]), ""
    return None, "aucun diff unifié dans la réponse"


def apply_check(diff: str, repo: Path) -> tuple[bool, str]:
    """git apply --check : le patch s'applique-t-il ? Ne modifie rien."""
    try:
        p = subprocess.run(["git", "apply", "--check", "--recount", "--whitespace=nowarn", "-"], cwd=str(repo),
                           input=diff, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, f"git apply impossible : {type(e).__name__}"
    return p.returncode == 0, redact(p.stderr.strip())[:600]


def _feedback(reason: str) -> dict:
    return {"role": "user", "content": (
        f"Your previous answer was rejected: {reason}\n"
        "Answer again with ONE corrected unified diff in a ```diff block, context lines copied verbatim "
        "from the code shown, editable paths only (or NO_PATCH: reason).")}


def propose(task: dict, prompt: dict, repo: Path, chain: llm.Chain, log=print) -> dict:
    """Jusqu'à MAX_ATTEMPTS tentatives. → proposal (dict), avec « diff » si un patch passe."""
    messages = list(prompt["messages"])
    attempts: list[dict] = []
    outcome, reason, diff_ok = "no_patch", "", None
    for n in range(1, MAX_ATTEMPTS + 1):
        effort = "low" if n == 1 else "medium"
        try:
            reply = chain.complete(messages, max_tokens=int(prompt.get("response_budget") or 1800),
                                   reasoning_effort=effort)
        except llm.BudgetExhausted as e:
            reason = f"budget : {e}"
            attempts.append({"n": n, "result": "budget", "detail": str(e)})
            break
        except llm.LLMError as e:
            reason = f"modèle indisponible : {e}"
            attempts.append({"n": n, "result": "llm_error", "detail": str(e)[:300]})
            continue
        diff, why = extract_diff(reply.text)
        said = redact(reply.text)[:3000]
        if diff is None:
            reason = why
            attempts.append({"n": n, "result": "no_diff", "model": reply.model, "detail": why, "response": said})
            messages += [{"role": "assistant", "content": reply.text[:4000]}, _feedback(why)]
            continue
        check = zone.check_patch(diff)
        if not check.ok:
            reason = "hors zone : " + " ; ".join(check.reasons)
            attempts.append({"n": n, "result": "out_of_zone", "model": reply.model, "files": check.files,
                             "detail": reason[:300], "response": said})
            log(f"  tentative {n} : patch rejeté entier — {reason[:160]}")
            messages += [{"role": "assistant", "content": reply.text[:4000]}, _feedback(reason)]
            continue
        fixed, notes = reanchor.repair(diff, repo)
        ok, err = apply_check(fixed, repo) if fixed else (False, "")
        if ok:
            diff = fixed
        else:                                        # le réancrage n'a pas pu : la version brute a sa chance
            ok, raw_err = apply_check(diff, repo)
            err = " ; ".join(x for x in (" ; ".join(notes), err, raw_err) if x)
        if not ok:
            reason = f"le patch ne s'applique pas : {err}"
            attempts.append({"n": n, "result": "apply_failed", "model": reply.model, "files": check.files,
                             "detail": err[:300], "response": said})
            log(f"  tentative {n} : git apply --check refuse — {err[:160]}")
            messages += [{"role": "assistant", "content": reply.text[:4000]}, _feedback(reason)]
            continue
        outcome, reason, diff_ok = "patch_proposed", "", diff
        attempts.append({"n": n, "result": "ok", "model": reply.model, "files": check.files, "repair": notes,
                         "response": said})
        log(f"  tentative {n} : patch accepté ({', '.join(check.files)}) par {reply.model}")
        break
    out = {"task_id": task.get("id"), "outcome": outcome, "reason": reason, "attempts": attempts,
           "usage": chain.budget.usage(), "files": attempts[-1].get("files", []) if diff_ok else []}
    if diff_ok:
        out["diff"] = diff_ok
        out["patch_sha256"] = hashlib.sha256(diff_ok.encode("utf-8")).hexdigest()
    return out


def deadline_from(report: dict, minutes: int) -> float | None:
    try:
        return datetime.fromisoformat(str(report.get("started_at"))).timestamp() + minutes * 60
    except (TypeError, ValueError):
        return time.time() + minutes * 60


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nightshift.propose")
    ap.add_argument("--veille", required=True, help="dossier de la veille : report.json, task.json, prompt.json")
    ap.add_argument("--repo", default=".", help="le dépôt, au commit de la veille")
    ap.add_argument("--out", required=True)
    ap.add_argument("--github-output", action="store_true", help="écrire patch=true|false dans $GITHUB_OUTPUT")
    args = ap.parse_args(argv)
    veille, out = Path(args.veille), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        task = json.loads((veille / "task.json").read_text(encoding="utf-8"))["task"]
        prompt = json.loads((veille / "prompt.json").read_text(encoding="utf-8"))
        report = json.loads((veille / "report.json").read_text(encoding="utf-8"))
        budget = llm.Budget(max_calls=_int_env("NIGHTLY_MAX_LLM_CALLS", 12),
                            max_tokens=_int_env("NIGHTLY_MAX_TOKENS", 60000),
                            deadline=deadline_from(report, _int_env("NIGHTLY_MAX_RUNTIME_MINUTES", 45)))
        print(f"Proposition pour {task['id']} ({prompt.get('estimated_tokens')} tokens de contexte)")
        result = propose(task, prompt, Path(args.repo), llm.Chain.from_env(budget=budget))
    except Exception as e:  # jamais de plantage silencieux : la raison va au rapport
        result = {"task_id": None, "outcome": "no_patch", "reason": f"proposition impossible : {type(e).__name__}: "
                  f"{redact(str(e))}"[:300], "attempts": [], "usage": llm.Budget().usage(), "files": []}
    diff = result.pop("diff", None)
    if diff:
        (out / "patch.diff").write_text(diff, encoding="utf-8")
    (out / "proposal.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Issue : {result['outcome']}" + (f" — {result['reason']}" if result["reason"] else ""))
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"patch={'true' if diff else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
