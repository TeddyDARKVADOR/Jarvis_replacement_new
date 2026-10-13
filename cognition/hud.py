"""
cognition/hud.py — ce que le panneau ESPRIT du HUD peut lire et faire.

    cognition.overview                 état, consignes, causes, objectifs,
                                       fiabilité des outils, calibration, limites
    cognition.memories   {query, view} souvenirs : all|unverified|contested|reflective|validated
    cognition.memory_review {id, verdict}      validate | reject | forget
    cognition.goal_action   {id, action}       approve | complete | cancel
    cognition.autonomy      {enabled}          interrupteur global (false = arrêt d'urgence)
    cognition.goal_autonomy {id, on}           autoriser l'autonomie sur un objectif
    cognition.autonomy_run  {id}               lancer une séance maintenant (en fond)
    cognition.forget_about  {subject}          effacer TOUT ce qui mentionne un sujet

QUI PEUT APPELER ÇA
    Uniquement la page du HUD, par le pont Qt (local_hud.LocalBackend), donc un
    clic de l'utilisateur. Aucun outil exposé au modèle n'y mène : c'est ce
    qui fait d'un clic ici une preuve (validation d'un souvenir) ou une
    autorisation (approbation d'un objectif), au même titre que le bouton
    CONFIRMER de core/confirm.py. Le selftest vérifie qu'aucun plugin
    n'importe ce module.

Toutes les réponses sont des dicts JSON ; une erreur est {"error": "..."}.
"""
from __future__ import annotations

import threading

from . import affect as A
from . import autonomy, selfmodel

_VIEWS = {
    "all":        "status IN ('proposed','active','validated','contested')",
    "unverified": "status = 'proposed' AND kind != 'reflective'",
    "contested":  "status = 'contested'",
    "reflective": "kind = 'reflective' AND status IN ('proposed','validated')",
    "validated":  "status = 'validated'",
    "episodes":   "kind = 'episodic' AND status IN ('active','validated')",
}


def call(cog, op: str, data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    handler = _OPS.get(str(op))
    if handler is None:
        return {"error": f"opération inconnue : {op}"}
    try:
        return handler(cog, data)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _overview(cog, _data: dict) -> dict:
    st = cog.current_affect()
    causes = cog.store.query(
        "SELECT ts, var, before, after, rule FROM affect_log ORDER BY id DESC LIMIT 12")
    counts = cog.memory.counts()
    status_totals: dict[str, int] = {}
    for by_status in counts.values():
        for k, v in by_status.items():
            status_totals[k] = status_totals.get(k, 0) + v
    return {
        "constitution": cog.constitution.version,
        "session": cog.session_id,
        "affect": [{"name": n, "value": round(st.values[n], 3), "baseline": v.baseline,
                    "lo": v.lo, "hi": v.hi, "word": A._word(n, st.values[n]),
                    "meaning": v.meaning} for n, v in A.VARS.items()],
        "directives": [{"key": k, "text": A.ALLOWED_DIRECTIVES[k]} for k in A.directives(st)],
        "causes": [dict(r) for r in causes],
        "goals": cog.goals.open_goals(),
        "closed_goals": [dict(r) for r in cog.store.query(
            "SELECT id, title, status, closed_reason, updated_at FROM goals "
            "WHERE status NOT IN ('proposed','active','blocked') ORDER BY updated_at DESC LIMIT 8")],
        "memory": {"by_kind": counts, "by_status": status_totals},
        "tools": selfmodel.reliability(cog.store),
        "calibration": {"tools": selfmodel.tool_calibration(cog.store),
                        "memories": selfmodel.memory_calibration(cog.store)},
        "autonomy": {
            "enabled": cog.autonomy.enabled,
            "runs_today": cog.autonomy.runs_today(),
            "why_not": cog.autonomy.why_not(),
            "goals": {g["id"]: cog.autonomy.why_not(g) for g in cog.goals.open_goals()},
            "runs": cog.autonomy.recent_runs(),
            "limits": {"steps_per_run": autonomy.MAX_STEPS_PER_RUN,
                       "runs_per_day": autonomy.MAX_RUNS_PER_DAY,
                       "interval_min": autonomy.MIN_INTERVAL_S // 60,
                       "tools": sorted(autonomy.SAFE_TOOLS)},
        },
        "limits": list(selfmodel.HARD_LIMITS),
        "limits_fr": list(selfmodel.HARD_LIMITS_FR),
        "providers": [{k: p[k] for k in ("name", "model", "available", "paused_s", "calls",
                                         "failures", "latency_s")}
                      for p in (cog.chain.status() if cog.chain else [])],
    }


def _memories(cog, data: dict) -> dict:
    view = str(data.get("view") or "all")
    where = _VIEWS.get(view)
    if where is None:
        raise ValueError(f"vue inconnue : {view}")
    query = str(data.get("query") or "").strip()
    if query:
        rows = [r for r in cog.memory.search(query, limit=60, touch=False)]
        ids = {r["id"] for r in cog.store.query(f"SELECT id FROM memories WHERE {where}")}
        rows = [r for r in rows if r["id"] in ids]
    else:
        rows = [dict(r) for r in cog.store.query(
            f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC LIMIT 200")]
    keep = ("id", "kind", "slot", "content", "source", "confidence", "importance", "status",
            "created_at", "updated_at", "valid_to")
    out = []
    for r in rows:
        item = {k: r.get(k) for k in keep}
        item["cites"] = [x["dst"] for x in cog.store.query(
            "SELECT dst FROM memory_links WHERE src=? AND relation='cites'", (r["id"],))]
        out.append(item)
    totals = {v: cog.store.query(f"SELECT count(*) n FROM memories WHERE {w}")[0]["n"]
              for v, w in _VIEWS.items()}
    return {"view": view, "items": out, "totals": totals}


def _memory_review(cog, data: dict) -> dict:
    mid, verdict = str(data.get("id", "")), str(data.get("verdict", ""))
    result = selfmodel.review_memory(cog.memory, mid, verdict)
    cog.memory.log_event("system", session_id=cog.session_id, actor="user",
                         summary=f"souvenir {verdict} sur le HUD")
    return result


def _goal_action(cog, data: dict) -> dict:
    gid, action = str(data.get("id", "")), str(data.get("action", ""))
    if not cog.goals.get(gid):
        raise ValueError("objectif introuvable")
    if action == "approve":
        msg = cog.goal_approve(gid)
    elif action == "complete":
        msg = cog.goal_complete(gid, "confirmé dans le panneau ESPRIT")
    elif action == "cancel":
        msg = cog.goal_cancel(gid, "annulé dans le panneau ESPRIT")
    else:
        raise ValueError(f"action inconnue : {action}")
    return {"ok": True, "detail": msg, "goal": cog.goals.get(gid)}


def _autonomy(cog, data: dict) -> dict:
    on = bool(data.get("enabled"))
    cog.autonomy.set_enabled(on)
    cog.memory.log_event("system", session_id=cog.session_id, actor="user",
                         summary="autonomie activée" if on else "autonomie coupée (arrêt)")
    return {"ok": True, "enabled": cog.autonomy.enabled}


def _goal_autonomy(cog, data: dict) -> dict:
    gid = str(data.get("id", ""))
    g = cog.goals.get(gid)
    if not g:
        raise ValueError("objectif introuvable")
    if data.get("on") and g["status"] != "active":
        raise ValueError("seul un objectif actif peut être confié à l'autonomie")
    cog.autonomy.set_goal(gid, bool(data.get("on")))
    return {"ok": True, "goal": cog.goals.get(gid)}


def _autonomy_run(cog, data: dict) -> dict:
    gid = str(data.get("id", ""))
    g = cog.goals.get(gid)
    if not g:
        raise ValueError("objectif introuvable")
    reason = cog.autonomy.why_not(g)
    # « Lancer maintenant » est un clic : il passe outre l'intervalle minimal,
    # jamais les autres portes.
    if reason and not reason.startswith("dernière séance"):
        raise ValueError(f"impossible : {reason}")
    threading.Thread(target=lambda: cog.autonomy._run_now(gid), name="cognition-autonomy-now",
                     daemon=True).start()
    return {"ok": True, "detail": "séance lancée"}


def _forget_about(cog, data: dict) -> dict:
    subject = str(data.get("subject", "")).strip()
    if len(subject) < 2:
        raise ValueError("sujet trop court")
    if data.get("preview"):
        rows = cog.forget_preview(subject)
        return {"count": len(rows), "items": [r["content"][:120] for r in rows[:20]]}
    return {"ok": True, "detail": cog.forget_about(subject)}


_OPS = {
    "cognition.forget_about": _forget_about,
    "cognition.autonomy": _autonomy,
    "cognition.goal_autonomy": _goal_autonomy,
    "cognition.autonomy_run": _autonomy_run,
    "cognition.overview": _overview,
    "cognition.memories": _memories,
    "cognition.memory_review": _memory_review,
    "cognition.goal_action": _goal_action,
}
