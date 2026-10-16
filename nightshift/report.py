"""
nightshift/report.py — le rapport jarvis.nightly/1 : JSON, Markdown, résumé du run.

    build(...)          le rapport complet d'une nuit (appelle compare.py)
    failed(...)         le rapport d'une nuit qui n'a rien pu mesurer
    to_markdown(r)      pour $GITHUB_STEP_SUMMARY et latest.md
    write(r, md, d)     report.json + report.md dans d (écriture atomique)
    step_summary(md)    ajoute le Markdown au résumé du run, dans GitHub Actions

LE SCHÉMA — jarvis.nightly/1
  1.0  celui du plan Night Shift (section 7) : run_id, run_url, commit,
       started_at, finished_at, status, summary, suites, regressions, task,
       pull_request_url, usage, budgets, limitations.
  1.1  ajouté en phase 1 : repository, ref, event, commit_full, previous,
       duration_s, environment, repaired, preexisting, lint, green_streak ;
       dans chaque suite : outcome (ce qu'elle a fait, avant classement), failed
       (id, libellé, détail, classe), reason, tail.
  1.2  unreferenced : les échecs que la veille ne permet pas de dater (pas de
       rapport, suite qui n'avait pas tourné), et la classe d'échec du même nom.
       Avant, ils passaient pour « déjà rouges ».
  1.3  phase 2 : task (id, source, suite et check ou code et path, files,
       attempts, outcome, reason, model, publication, out_of_zone), usage
       (appels et tokens réels), budgets (max_tasks en plus), dry_run.
  1.4  phase 3 : task.skipped (ce que la mémoire a passé, et pourquoi),
       diagnostics (échecs persistants : faits, hypothèses), backlog (nœuds bug
       et partiel des graphes), week (le dimanche : la semaine en chiffres).
  1.5  phase 4 : metrics (eval_lab par corpus et par cause, cognition, coût en
       contexte) et metrics_changes (baisse, hausse, changement face à la
       référence) ; task.review_closely quand un correctif dégrade un indicateur.
  1.6  phase 5 : local (les derniers avis du PC réveillé la nuit, ou
       « indisponible » avec la raison — jamais « en attente »).
  Un champ ajouté fait monter la version mineure (notée dans le graphe
  nightshift) ; un changement incompatible passera à jarvis.nightly/2.

status : success (tout est vert, échecs connus compris), partial_success (le
run a tourné et quelque chose est rouge), failed (le run n'a rien pu mesurer).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from nightshift import SCHEMA
from nightshift import compare as C
from nightshift import metrics as M
from nightshift.suites import short


def _iso(t: datetime | str | None) -> str | None:
    return t.isoformat() if isinstance(t, datetime) else t


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def budgets() -> dict:
    """Les plafonds de la nuit (variables du dépôt), tels que la nuit les a appliqués."""
    return {"max_tasks": _int_env("NIGHTLY_MAX_TASKS", 1), "max_llm_calls": _int_env("NIGHTLY_MAX_LLM_CALLS", 12),
            "max_tokens": _int_env("NIGHTLY_MAX_TOKENS", 60000),
            "max_runtime_minutes": _int_env("NIGHTLY_MAX_RUNTIME_MINUTES", 45),
            "passes": _int_env("NIGHTLY_PASSES", 3), "max_open_prs": _int_env("NIGHTLY_MAX_OPEN_PRS", 3)}


OUTCOME_FR = {"patch_validated": "correctif validé", "patch_rejected": "correctif rejeté",
              "no_patch": "pas de correctif", "skipped": "rien de tenté"}


def task_ref(t: dict) -> str:
    """« reminders n°1 », « F841 eval_lab/mutate.py »."""
    if t.get("source") == "ruff":
        return f"{t.get('code')} {t.get('path')}"
    return _ref({"suite": t.get("suite", "?"), "check": t.get("check", "?"), "label": t.get("label")})


def _task_part(r: dict) -> str | None:
    t = r.get("task")
    if not isinstance(t, dict) or not t.get("outcome"):
        return None
    if t["outcome"] == "skipped":
        return t.get("reason") or "rien à corriger"
    part = f"tâche {task_ref(t)} : {OUTCOME_FR.get(t['outcome'], t['outcome'])}"
    if t["outcome"] == "patch_validated" and t.get("review_closely"):
        part += " (à relire de près)"
    if t["outcome"] == "patch_validated":
        part += " — " + ("PR ouverte" if r.get("pull_request_url") else (t.get("publication") or "pas de PR"))
    elif t.get("reason"):
        part += f" ({str(t['reason'])[:90]})"
    return part


def _ref(x: dict) -> str:
    """« cognition n°21 », « server (suite entière) », « context « libellé » »."""
    name, check = short(x.get("suite", "?")), str(x.get("check", "?"))
    if check == "*":
        return f"{name} (suite entière)"
    if check[:1].isdigit():
        return f"{name} n°{check}"
    return f"{name} « {str(x.get('label') or check)[:50]} »"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def _status(suites: list[dict], regressions: list[dict]) -> str:
    ran = [s for s in suites if s.get("status") != "skipped"]
    if not ran:
        return "failed"
    if not regressions and all(s.get("status") in ("ok", "known_failure") for s in ran):
        return "success"
    return "partial_success"


def summarize(r: dict) -> str:
    """La phrase du rapport : « 13/14 suites vertes ; aucune régression ; ruff : 4 remarques. »"""
    if r.get("status") == "failed" and not r.get("suites"):
        return r.get("summary") or "Nuit sans mesure."
    suites = r.get("suites") or []
    ran = [s for s in suites if s.get("status") != "skipped"]
    green = [s for s in ran if s.get("status") in ("ok", "known_failure")]
    skipped = len(suites) - len(ran)
    head = f"{len(green)}/{len(ran)} suites vertes"
    if skipped:
        head += f" ({_plural(skipped, 'ignorée')})"
    parts = [head]
    regs = r.get("regressions") or []
    if regs:
        more = f" et {len(regs) - 3} autre(s)" if len(regs) > 3 else ""
        parts.append(f"{_plural(len(regs), 'régression')} ({', '.join(_ref(x) for x in regs[:3])}{more})")
    else:
        parts.append("aucune régression")
    unref = r.get("unreferenced") or []
    if unref and r.get("previous") is not None:   # la première nuit, tout est sans référence
        parts.append(f"{_plural(len(unref), 'échec')} sans référence ({', '.join(_ref(x) for x in unref[:3])})")
    broken = [s for s in ran if s.get("status") in ("error", "timeout")]
    if broken:
        parts.append(f"en erreur : {', '.join(short(s['name']) for s in broken[:3])}")
    reps = r.get("repaired") or []
    if reps:
        parts.append(f"réparé : {', '.join(_ref(x) for x in reps[:3])}")
    lint = r.get("lint") or {}
    if lint.get("available"):
        new = len(lint.get("new") or [])
        parts.append(f"ruff : {_plural(lint.get('count', 0), 'remarque')}"
                     + (f" dont {_plural(new, 'nouvelle')}" if new else ""))
    changes = r.get("metrics_changes") or []
    if changes:
        parts.append(f"indicateurs : {_plural(len(changes), 'changement')} à relire ({changes[0]['metric']})")
    task = _task_part(r)
    if task:
        parts.append(task)
    return " ; ".join(parts) + "."


def _limitations(suites: list[dict], previous: dict | None, lint: dict, regressions: list[dict]) -> list[str]:
    out = []
    for s in suites:
        if s.get("status") == "skipped":
            out.append(f"{s['name']} non testée sur {s.get('platform')} : {s.get('reason', '?')}")
        for f in s.get("failed") or []:
            if f.get("class") == "known":
                out.append(f"{short(s['name'])} n°{f.get('id')} : échec connu sur {s.get('platform')}")
    if previous is None:
        out.append("pas de rapport précédent : aucune régression ne peut être établie cette nuit")
    if not lint.get("available"):
        out.append(lint.get("reason") or "ruff non lancé")
    if regressions and any(x.get("suspects") is None for x in regressions):
        out.append("commits suspects inconnus : l'historique git ne contient pas le dernier commit vert")
    return out


def _previous_ref(previous: dict | None) -> dict | None:
    if not previous:
        return None
    return {k: previous.get(k) for k in ("run_id", "commit", "finished_at", "status")}


def build(*, meta: dict, git: dict, env: dict, started_at: datetime, finished_at: datetime,
          suites: list[dict], lint: dict, previous: dict | None, suspects: list[str] | None,
          known: dict[str, dict[str, str]], metrics: dict | None = None) -> dict:
    """Le rapport complet. `previous` est le dernier rapport qui a des résultats de suites."""
    cmp = C.classify(suites, previous, known, commit=git.get("commit", ""),
                     previous_commit=(previous or {}).get("commit", ""), suspects=suspects)
    lint = dict(lint)
    if lint.get("available"):
        lint["new"] = C.lint_new(lint, previous)
    status = _status(cmp["suites"], cmp["regressions"])
    r = {
        "schema_version": SCHEMA,
        "run_id": meta.get("run_id"),
        "run_url": meta.get("run_url"),
        "repository": meta.get("repository"),
        "ref": git.get("ref"),
        "event": meta.get("event"),
        "commit": git.get("commit"),
        "commit_full": git.get("commit_full"),
        "previous": _previous_ref(previous),
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "duration_s": round((finished_at - started_at).total_seconds(), 1),
        "status": status,
        "summary": "",
        "environment": env,
        "suites": cmp["suites"],
        "regressions": cmp["regressions"],
        "repaired": cmp["repaired"],
        "preexisting": cmp["preexisting"],
        "unreferenced": cmp["unreferenced"],
        "lint": lint,
        "metrics": metrics,
        "metrics_changes": M.compare(metrics, (previous or {}).get("metrics")) if metrics else [],
        "green_streak": C.green_streak(status == "success", previous),
        "task": None,
        "pull_request_url": None,
        "usage": {"llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "providers": {}},
        "budgets": budgets(),
        "limitations": _limitations(cmp["suites"], previous, lint, cmp["regressions"]),
    }
    for name, m in (metrics or {}).items():
        if not (m or {}).get("available"):
            r["limitations"].append(f"indicateur {name} indisponible : {(m or {}).get('reason', '?')}"[:200])
    r["summary"] = summarize(r)
    return r


def failed(meta: dict, git: dict, reason: str, started_at: datetime | None = None) -> dict:
    """Une nuit qui n'a rien pu mesurer : le rapport le dit, avec la cause."""
    t = _iso(started_at) if started_at else datetime.now().astimezone().replace(microsecond=0).isoformat()
    return {
        "schema_version": SCHEMA, "run_id": meta.get("run_id"), "run_url": meta.get("run_url"),
        "repository": meta.get("repository"), "ref": git.get("ref"), "event": meta.get("event"),
        "commit": git.get("commit"), "commit_full": git.get("commit_full"), "previous": None,
        "started_at": t, "finished_at": t, "duration_s": 0.0, "status": "failed",
        "summary": f"Nuit sans mesure : {reason}"[:300], "environment": {}, "suites": [],
        "regressions": [], "repaired": [], "preexisting": [], "unreferenced": [],
        "lint": {"tool": "ruff", "available": False},
        "green_streak": 0, "task": None, "pull_request_url": None,
        "usage": {"llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "providers": {}},
        "budgets": {"max_llm_calls": 0, "max_tokens": 0, "max_runtime_minutes": 45},
        "limitations": [reason[:300]],
    }


# ── Markdown ─────────────────────────────────────────────────────────────────

STATE_FR = {
    "ok": "✅ vert", "known_failure": "☑️ échec connu", "failing": "❌ rouge",
    "regression": "🔴 RÉGRESSION", "error": "💥 en erreur", "timeout": "⏱️ délai dépassé",
    "skipped": "⏭️ ignorée",
}


def _cell(text) -> str:
    return str(text if text is not None else "—").replace("|", "\\|").replace("\n", " ")


def to_markdown(r: dict) -> str:
    day = str(r.get("started_at") or "")[:10]
    env = r.get("environment") or {}
    ps = r.get("pass") if isinstance(r.get("pass"), dict) else {}
    title = f"## JARVIS Night Shift — {day}" + (f" · passe {ps.get('n')}/{ps.get('of')}" if ps.get("n") else "")
    lines = [title, "", f"**{_cell(r.get('summary'))}**", ""]
    meta = [f"commit `{r.get('commit') or '?'}`"]
    if r.get("ref"):
        meta.append(f"branche `{r['ref']}`")
    if env:
        meta.append(" · ".join(x for x in (env.get("image") or env.get("os"), f"Python {env.get('python')}") if x))
    if r.get("duration_s") is not None:
        meta.append(f"{int(r['duration_s'] // 60)} min {int(r['duration_s'] % 60):02d} s")
    if r.get("run_url"):
        meta.append(f"[exécution]({r['run_url']})")
    lines += [" · ".join(meta), ""]

    if r.get("suites"):
        lines += ["| Suite | Score | État | Durée |", "|---|---:|---|---:|"]
        for s in r["suites"]:
            score = f"{s.get('passed')}/{s.get('total')}" if s.get("total") is not None else "—"
            state = STATE_FR.get(s.get("status"), s.get("status"))
            classes = {f.get("class") for f in s.get("failed") or []}
            if s.get("status") == "failing":
                state += " (sans référence)" if "unreferenced" in classes else " (déjà)"
            known = [f.get("id") for f in s.get("failed") or [] if f.get("class") == "known"]
            if known:
                state += f" (n°{', '.join(known)})"
            if s.get("reason"):
                state += f" — {s['reason']}"
            lines.append(f"| {_cell(s.get('name'))} | {score} | {_cell(state)} | {s.get('duration_s', 0):.1f} s |")
        lines.append("")

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend([f"### {title}", "", *rows, ""])

    t = r.get("task")
    if isinstance(t, dict) and t.get("outcome"):
        u, bud = r.get("usage") or {}, r.get("budgets") or {}
        rows = []
        if t["outcome"] == "skipped":
            rows.append(f"- {_cell(t.get('reason') or 'rien à corriger')}")
        else:
            src = {"regression": "régression", "failing_check": "contrôle rouge", "ruff": "ruff"}.get(t.get("source"), "?")
            head = f"- **{_cell(task_ref(t))}** ({src}) — {OUTCOME_FR.get(t['outcome'], t['outcome'])}"
            if t.get("reason"):
                head += f" : {_cell(t['reason'])}"
            rows.append(head)
            if t.get("publication") or r.get("pull_request_url"):
                rows.append(f"- Publication : {_cell(r.get('pull_request_url') or t.get('publication'))}")
            rows.append(f"- {len(t.get('files') or [])} fichier(s) : "
                        + (", ".join(f"`{_cell(f)}`" for f in t.get("files") or []) or "—")
                        + f" · {t.get('attempts', 0)} tentative(s)" + (f" · {_cell(t['model'])}" if t.get("model") else ""))
        rows.append(f"- Consommation : {u.get('llm_calls', 0)} appel(s) sur {bud.get('max_llm_calls', '?')} · "
                    f"{u.get('tokens_in', 0) + u.get('tokens_out', 0)} tokens sur {bud.get('max_tokens', '?')}")
        rows += [f"- Hors zone (pour un humain) : {_cell(x.get('id'))} — {_cell(x.get('reason'))}"
                 for x in (t.get("out_of_zone") or [])[:6]]
        section("Tâche de la nuit", rows)
        section("Mémoire — tâches passées cette nuit", [
            f"- `{_cell(x.get('idea') or x.get('id'))}` — {_cell(x.get('reason'))}" for x in t.get("skipped") or []])
    tonight = [x for x in r.get("tonight") or [] if isinstance(x, dict)]
    if len(tonight) > 1:
        rows = []
        for x in tonight:
            ref = x.get("ref") if isinstance(x.get("ref"), dict) else {}
            what = task_ref(ref) if ref else "rien de tenté"
            out = OUTCOME_FR.get(x.get("outcome"), x.get("outcome") or "—")
            rows.append(f"- Passe {x.get('pass') or '?'} : {_cell(what)} — {_cell(out)}"
                        + (f" · [PR]({x['pr']})" if x.get("pr") else "") + f" · {int(x.get('tokens') or 0)} tokens")
        section("Cette nuit, passe par passe", rows)
    for d in r.get("diagnostics") or []:
        section(f"Échec persistant — {_cell(d.get('label') or d.get('idea'))}", [
            f"Idée `{_cell(d.get('idea'))}`, {d.get('attempts')} tentatives sur le même contexte.", "",
            "**Faits**", *[f"- {_cell(x)}" for x in d.get("facts") or []], "",
            "**Hypothèses** (non vérifiées)", *[f"- {_cell(x)}" for x in d.get("hypotheses") or []]])
    w = r.get("week")
    if isinstance(w, dict):
        section("La semaine", [
            f"- {w.get('nights')} nuit(s) du {w.get('from')} au {w.get('to')}, dont {w.get('green')} verte(s)",
            f"- {w.get('tried')} tâche(s) tentée(s), {w.get('validated')} correctif(s) validé(s)",
            f"- {w.get('prs')} PR ouverte(s) : {w.get('accepted')} acceptée(s), {w.get('rejected')} refusée(s)"
            + (f", {w['expired']} expirée(s) sans relecture" if w.get("expired") else ""),
            f"- {w.get('tokens')} tokens consommés"])
    m = r.get("metrics") or {}
    if m:
        rows = []
        for corpus, c in ((m.get("eval_lab") or {}).get("corpora") or {}).items():
            if c.get("available"):
                causes = ", ".join(f"{k} ×{v}" for k, v in (c.get("causes") or {}).items())
                rows.append(f"- eval_lab {corpus} : {c['pass']}/{c['total']} réussis"
                            + (f" · échecs par cause : {_cell(causes)}" if causes else ""))
        ctx = m.get("context") or {}
        if ctx.get("available"):
            rows.append(f"- Coût en contexte : prompt {ctx['system_prompt_chars']} car. + outils {ctx['local']['chars']} car. "
                        f"({ctx['local']['tools']} outils, mode local) ≈ {ctx['estimated_tokens_local']} tokens par connexion")
        cog = m.get("cognition") or {}
        if cog.get("available"):
            rows.append(f"- Cognition : {len(cog.get('values') or {})} valeurs mesurées (architecture complète)")
        rows += [f"- **{_cell(x['metric'])}** : {_cell(x['before'])} → {_cell(x['now'])} ({x['verdict']})"
                 + (f" — {_cell(x['detail'])}" if x.get("detail") else "") for x in r.get("metrics_changes") or []]
        section("Indicateurs — JARVIS s'améliore-t-il ?", rows)

    loc = r.get("local")
    if isinstance(loc, dict):
        if loc.get("available"):
            section(f"Avis du modèle local (non vérifiés) — {_cell(loc.get('model'))}, {loc.get('status')}", [
                f"- {_cell(x.get('kind'))} · {x.get('status')}"
                + (f" : {_cell(x.get('output'))}" if x.get("output") else f" — {_cell(x.get('error'))}")
                for x in loc.get("tasks") or []] or ["- aucune tâche"])
        else:
            section("Modèle local", [f"- indisponible : {_cell(loc.get('reason'))}"])

    bl = r.get("backlog") or []
    section(f"Backlog du graphe — {len(bl)} nœud(s) bug ou partiel", [
        f"- {b.get('status')} · `{_cell(b.get('graph'))}/{_cell(b.get('node'))}` — {_cell(b.get('label'))} · "
        + ("zone autorisée" if b.get("zone") else "hors zone") for b in bl[:20]])

    section("Régressions", [
        f"- **{_cell(_ref(x))}** — {_cell(x.get('label'))} · depuis `{x.get('since_commit') or '?'}`"
        + (f" (dernier vert : `{x['last_green_commit']}`)" if x.get("last_green_commit") else "")
        + (f" · commits suspects : {', '.join('`' + _cell(c) + '`' for c in x['suspects'][:5])}"
           if x.get("suspects") else "")
        for x in r.get("regressions") or []])
    section("Réparé", [f"- {_cell(_ref(x))} — {_cell(x.get('label'))}" for x in r.get("repaired") or []])
    section("Déjà rouge", [f"- {_cell(_ref(x))} — {_cell(x.get('label'))}" for x in r.get("preexisting") or []])
    if r.get("unreferenced"):
        section("Rouge, sans référence", [
            "La veille ne permet pas de dire si c'est nouveau (pas de rapport, ou la suite n'avait pas tourné).", "",
            *[f"- {_cell(_ref(x))} — {_cell(x.get('label'))}" for x in r["unreferenced"]]])
    for s in r.get("suites") or []:
        if s.get("tail"):
            section(f"Fin de sortie — {s['name']}", ["```", *s["tail"], "```"])
    lint = r.get("lint") or {}
    if lint.get("available"):
        new = {(f.get("code"), f.get("path"), f.get("message")) for f in lint.get("new") or []}
        section(f"ruff {lint.get('version', '')} — {lint.get('count', 0)} remarque(s)", [
            f"- `{_cell(f.get('path'))}:{f.get('line')}` {f.get('code')} {_cell(f.get('message'))}"
            + (" **(nouvelle)**" if (f.get("code"), f.get("path"), f.get("message")) in new else "")
            for f in lint.get("findings") or []])
    section("Limites", [f"- {_cell(x)}" for x in r.get("limitations") or []])
    return "\n".join(lines).rstrip() + "\n"


# ── écriture ─────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dumps(r: dict) -> str:
    return json.dumps(r, ensure_ascii=False, indent=1) + "\n"


def write(r: dict, md: str, out_dir: Path) -> None:
    _atomic_write(out_dir / "report.json", dumps(r))
    _atomic_write(out_dir / "report.md", md)


def step_summary(md: str) -> bool:
    """Ajoute le Markdown au résumé du run GitHub ($GITHUB_STEP_SUMMARY), s'il existe."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")
        return True
    except OSError:
        return False
