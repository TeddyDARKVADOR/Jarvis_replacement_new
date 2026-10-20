"""
matthew/hud.py — l'onglet MATTHEW du panneau ESPRIT et le badge « qui répond » du HUD.

    matthew.status        tout ce que l'onglet affiche, et qui répond en ce moment
    matthew.correct       {text}   la bonne réponse au dernier échange (donnée d'or)
    matthew.forget_last            le dernier échange ne sera pas appris
    matthew.publish                lever le drapeau de la vitrine et publier en fond

Appelé par la page du HUD via local_hud.LocalBackend (préfixe « matthew. »), comme
cognition.* et nightshift.* : un clic de l'utilisateur, jamais un outil du modèle.
Réponses en dicts JSON ; une erreur est {"error": "..."}.

QUI RÉPOND
    Gemini Live tant que la liaison tient ; sinon le relais hors-ligne (offline/),
    dont le modèle est Matthew dès qu'il est champion. Les tâches de fond de la
    cognition passent par Matthew s'il a réussi son banc (cogbench).
"""
from __future__ import annotations

import json
import time

OPS = ("matthew.status", "matthew.correct", "matthew.forget_last", "matthew.publish")


def who_answers(jarvis=None) -> dict:
    sup = getattr(getattr(jarvis, "_offline", None), "supervisor", None)
    if sup is not None and getattr(sup, "active", False):
        model = getattr(getattr(sup, "voice", None), "model", "") or "modèle local"
        is_matthew = str(model).startswith("matthew")
        return {"brain": "Matthew" if is_matthew else "hors-ligne", "model": model,
                "detail": "relais hors-ligne : Gemini est injoignable"}
    return {"brain": "Gemini", "model": "Gemini Live", "detail": "la voix passe par Gemini Live"}


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def status(jarvis=None) -> dict:
    import matthew
    from matthew.client import usage_summary
    from matthew.flywheel import Flywheel
    root = matthew.data_dir()
    st = Flywheel().status()
    champ = matthew.champion() or {}
    ev = _read(root / "last_eval.json", {})
    live = _read(root / "vitrine" / "live.json", None)
    if live and time.time() - float(live.get("at", 0)) > 3 * 3600:
        live = None
    today = usage_summary(root, time.strftime("%Y-%m-%d"))
    try:
        cog_first = matthew.cognition_order(["groq"], {}) [0] == "matthew"
    except Exception:
        cog_first = False
    return {
        "who": who_answers(jarvis),
        "cognition_by_matthew": cog_first,
        "champion": {"since": champ.get("since"), "base": champ.get("base"),
                     "score": (champ.get("score") or {}).get("composite"),
                     "tool_name": (champ.get("score") or {}).get("tool_name"),
                     "irrelevance": (champ.get("score") or {}).get("irrelevance"),
                     "cognition": (champ.get("cognition") or {}).get("score")} if champ else None,
        "synthetic": st.get("synthetic", 0), "tools_covered": st.get("synthetic_tools", 0),
        "episodes": st.get("episodes", 0), "corrections": st.get("corrections", 0),
        "tokens_today": {k: v["in"] + v["out"] for k, v in today.items()},
        "last_cycle": (st.get("state") or {}).get("last_cycle"),
        "weak": ev.get("weak") or [], "failures": (ev.get("failures") or [])[:8],
        "live": live, "paused": st.get("paused"),
        "flag_pending": (root / "vitrine" / "FLAG").exists(),
        "vitrine_url": "https://teddydarkvador.github.io/matthew-vitrine/",
    }


def call(op: str, data: dict | None = None, jarvis=None) -> dict:
    data = data if isinstance(data, dict) else {}
    if op not in OPS:
        return {"error": f"opération inconnue : {op}"}
    try:
        if op == "matthew.status":
            return status(jarvis)
        from matthew.journal import Journal
        if op == "matthew.correct":
            text = str(data.get("text") or "").strip()
            if not text:
                return {"error": "écrivez la bonne réponse"}
            rec = Journal().correction(text)
            return {"ok": bool(rec), "message": "Noté : Matthew apprendra cette réponse."
                    if rec else "Aucun échange récent à corriger (ou collecte en pause)."}
        if op == "matthew.forget_last":
            ok = Journal().forget_last()
            return {"ok": ok, "message": "Ce dernier échange ne sera pas appris." if ok else "Aucun échange récent."}
        from matthew import vitrine
        vitrine.flag("publication demandée depuis le HUD")
        vitrine.spawn()
        return {"ok": True, "message": "Vitrine : publication lancée en arrière-plan."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
