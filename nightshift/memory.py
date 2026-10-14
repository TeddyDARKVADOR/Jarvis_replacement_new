"""
nightshift/memory.py — ce que la nuit retient d'une nuit à l'autre (phase 3).

    state = load(path)                                     la mémoire, ou une mémoire vide
    why   = skip_reason(task, commit, fp, state, prs, prio) faut-il passer cette tâche ?
    state = update(state, report, task, prs)               après la nuit (job « assemblage »)

L'ÉTAT — state.json sur la branche nightshift-data, écrit seulement par les nuits de main
  ideas   une entrée par idée de correctif (« check:<suite>:<n°> »,
          « ruff:<code>:<chemin>:<message> ») : ses tentatives (nuit, commit,
          empreinte du contexte, issue, raison, modèle, tokens) et sa PR ;
  nights  les 60 dernières nuits : état, tâche, issue, consommation, PR.

LES RÈGLES
  - même idée, même commit, même contexte : déjà tentée, pas réanalysée ;
  - PR fermée sans fusion : refusée par l'humain, plus jamais reproposée
    (sauf « force » dans priorities.toml) ; fusionnée : acceptée ; ouverte :
    on attend sa relecture ;
  - deux échecs (pas de patch, ou patch rejeté par les suites) sur un contexte
    inchangé : échec persistant — un dossier de diagnostic au rapport, et
    l'idée attend que le code change ;
  - nightshift/priorities.toml (humain seulement : nightshift/ est hors zone) :
    « force » passe en tête, « forbid » n'est jamais proposé.
Le dimanche, le rapport porte le résumé de la semaine, que le briefing annonce.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = "jarvis.nightshift-state/1"
PRIORITIES = Path(__file__).resolve().parent / "priorities.toml"
MAX_NIGHTS = 60
MAX_ATTEMPTS = 10
FAILED = ("no_patch", "patch_rejected")


def empty() -> dict:
    return {"schema": SCHEMA, "updated": None, "ideas": {}, "nights": []}


def load(path: Path | str | None) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except Exception:
        data = None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return empty()
    data.setdefault("ideas", {})
    data.setdefault("nights", [])
    return data


def idea_key(task: dict) -> str:
    """L'idée d'un correctif, stable d'un commit à l'autre."""
    if task.get("source") == "ruff":
        return f"ruff:{task.get('code')}:{task.get('path')}:{task.get('message', '')[:120]}"
    return f"check:{task.get('suite')}:{task.get('check')}"


def fingerprint(prompt: dict) -> str:
    """L'empreinte du contexte montré au modèle : même empreinte, même question."""
    text = "\n".join(str(m.get("content", "")) for m in (prompt or {}).get("messages") or [])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_priorities(path: Path | str | None = None) -> dict:
    try:
        data = tomllib.loads(Path(path or PRIORITIES).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {k: [str(x) for x in data.get(k) or [] if isinstance(x, str)] for k in ("force", "forbid")}


def prs_by_url(prs: list | None) -> dict[str, dict]:
    """Les PR Night Shift lues par gh (« state » : OPEN, CLOSED ou MERGED), par URL."""
    return {p["url"]: p for p in prs or [] if isinstance(p, dict) and p.get("url")}


def verdict(entry: dict | None, prs: dict[str, dict]) -> str | None:
    """accepted · rejected · open · None — selon la PR la plus récente de l'idée."""
    url = next((a.get("pr") for a in reversed((entry or {}).get("attempts") or []) if a.get("pr")), None)
    pr = prs.get(url) if url else None
    if pr is None:
        return (entry or {}).get("verdict")          # dernier verdict connu, si la PR n'est plus listée
    state = str(pr.get("state", "")).upper()
    if state == "MERGED" or pr.get("mergedAt"):
        return "accepted"
    return "rejected" if state == "CLOSED" else "open"


def _failures(entry: dict | None, fp: str) -> list[dict]:
    return [a for a in (entry or {}).get("attempts") or [] if a.get("fingerprint") == fp and a.get("outcome") in FAILED]


def skip_reason(task: dict, commit: str, fp: str, state: dict, prs: dict[str, dict], prio: dict) -> str | None:
    """Pourquoi ne pas proposer cette tâche cette nuit, ou None."""
    idea = idea_key(task)
    if idea in prio.get("forbid", []):
        return "interdite par nightshift/priorities.toml"
    entry = (state.get("ideas") or {}).get(idea)
    if any(a.get("commit") == commit and a.get("fingerprint") == fp for a in (entry or {}).get("attempts") or []):
        return f"déjà tentée au commit {commit} avec le même contexte"
    v = verdict(entry, prs)
    if v == "open":
        return "une PR attend déjà sa relecture pour cette idée"
    forced = idea in prio.get("force", [])
    if v == "rejected" and not forced:
        return "PR refusée (fermée sans fusion) : l'idée n'est plus reproposée"
    if len(_failures(entry, fp)) >= 2 and not forced:
        return "échec persistant (2 tentatives sur ce contexte) : attend que le code change"
    return None


def order(candidates: list[dict], prio: dict) -> list[dict]:
    """« force » en tête, dans l'ordre du fichier ; le reste garde l'ordre du plan."""
    forced = prio.get("force", [])
    first = sorted([t for t in candidates if idea_key(t) in forced], key=lambda t: forced.index(idea_key(t)))
    return first + [t for t in candidates if idea_key(t) not in forced]


def update(state: dict, report: dict, task: dict | None, prs: list | None) -> dict:
    """La mémoire après la nuit : la tentative, les verdicts des PR, la nuit."""
    state = json.loads(json.dumps(state or empty()))
    by_url = prs_by_url(prs)
    t = report.get("task") or {}
    if task and t.get("outcome") in FAILED + ("patch_validated",):
        entry = state["ideas"].setdefault(idea_key(task), {"source": task.get("source"), "attempts": []})
        entry["label"] = task.get("label") or task.get("message") or ""
        u = report.get("usage") or {}
        entry["attempts"].append({
            "run_id": report.get("run_id"), "date": str(report.get("started_at") or "")[:10],
            "commit": report.get("commit"), "fingerprint": task.get("fingerprint"), "outcome": t.get("outcome"),
            "reason": str(t.get("reason") or "")[:300], "model": t.get("model"),
            "tokens": int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0),
            "pr": report.get("pull_request_url")})
        entry["attempts"] = entry["attempts"][-MAX_ATTEMPTS:]
    for entry in state["ideas"].values():
        v = verdict(entry, by_url)
        if v:
            entry["verdict"] = v
    u = report.get("usage") or {}
    state["nights"].append({
        "date": str(report.get("started_at") or "")[:10], "run_id": report.get("run_id"),
        "status": report.get("status"), "task": t.get("id"), "outcome": t.get("outcome"),
        "llm_calls": int(u.get("llm_calls") or 0),
        "tokens": int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0),
        "pr": report.get("pull_request_url")})
    state["nights"] = state["nights"][-MAX_NIGHTS:]
    state["updated"] = report.get("finished_at")
    return state


_HYPOTHESES = (
    ("ne s'applique pas", "le contexte montré ne correspond pas exactement au fichier (lignes, indentation)"),
    ("réponse vide", "le budget de réponse ne suffit pas au raisonnement du modèle"),
    ("hors zone", "la cause est hors de la zone autorisée : elle revient à un humain"),
    ("régression", "le correctif traite le symptôme et casse autre chose"),
    ("échoue encore", "le correctif ne touche pas la cause"),
    ("renonce", "le contexte ne suffit pas au modèle pour conclure"),
)


def diagnostics(state: dict) -> list[dict]:
    """Un dossier par échec persistant : les faits d'abord, les hypothèses à part."""
    out = []
    for idea, entry in (state.get("ideas") or {}).items():
        attempts = entry.get("attempts") or []
        if not attempts:
            continue
        fp = attempts[-1].get("fingerprint")
        failed = _failures(entry, fp)
        if len(failed) < 2 or entry.get("verdict") in ("accepted", "open"):
            continue
        facts = [f"{a.get('date') or 'date non disponible'} · commit {a.get('commit') or '?'} · "
                 f"{a.get('outcome')} : {a.get('reason') or 'raison non disponible'}"
                 + (f" ({a['model']})" if a.get("model") else "") for a in failed]
        reasons = " ".join(a.get("reason") or "" for a in failed)
        hypotheses = [h for key, h in _HYPOTHESES if key in reasons] or ["non disponible"]
        out.append({"idea": idea, "label": entry.get("label", ""), "attempts": len(failed), "facts": facts,
                    "hypotheses": hypotheses})
    return out


def week(state: dict, today: date) -> dict | None:
    """Le résumé des 7 dernières nuits — le dimanche seulement (heure de Paris)."""
    if today.weekday() != 6:
        return None
    since = (today - timedelta(days=6)).isoformat()
    nights = [n for n in state.get("nights") or [] if str(n.get("date") or "") >= since]
    tried = [n for n in nights if n.get("outcome") in FAILED + ("patch_validated",)]
    verdicts = [e.get("verdict") for e in (state.get("ideas") or {}).values()
                if any(str(a.get("date") or "") >= since and a.get("pr") for a in e.get("attempts") or [])]
    return {"from": since, "to": today.isoformat(), "nights": len(nights),
            "green": sum(1 for n in nights if n.get("status") == "success"),
            "tried": len(tried), "validated": sum(1 for n in tried if n.get("outcome") == "patch_validated"),
            "prs": sum(1 for n in nights if n.get("pr")), "accepted": verdicts.count("accepted"),
            "rejected": verdicts.count("rejected"), "tokens": sum(int(n.get("tokens") or 0) for n in nights)}


def today_paris() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return date.today()
