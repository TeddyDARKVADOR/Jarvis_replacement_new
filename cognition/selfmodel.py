"""
cognition/selfmodel.py — le modèle de soi et sa calibration (phase 6).

CE QUE « SE CONNAÎTRE » VEUT DIRE ICI
    Pas d'accès aux activations de Gemini : l'introspection mécanistique
    (Anthropic 2025) n'est pas transposable. Ce qui l'est, c'est un modèle de
    soi CALCULÉ à partir de ce qui a été observé, et une mesure de la justesse
    de ce modèle :

    1. Fiabilité des outils. Avant chaque appel, JARVIS a une attente de
       réussite tirée de son propre historique, (succès + 1) / (essais + 2)
       (règle de succession de Laplace). Après l'appel, le résultat réel. Le
       score de Brier et l'erreur de calibration attendue (ECE) disent si ses
       attentes correspondent à ce qui arrive.

    2. Confiance dans les souvenirs. Chaque souvenir a une confiance (fixée par
       sa provenance, policy.py). Quand l'utilisateur valide ou rejette un
       souvenir dans le panneau du HUD, c'est une vérité terrain : on mesure si
       « 0.5 » veut bien dire « vrai une fois sur deux ».

    Ces mesures portent sur le modèle de soi de l'ARCHITECTURE, pas sur ce que
    Gemini « croit » : elles ne prouvent rien sur une introspection du modèle
    de langage, et le rapport le dit.
"""
from __future__ import annotations

from datetime import datetime, timezone

MIN_EVIDENCE = 5          # essais avant de déclarer un outil peu fiable
WEAK_BELOW = 0.5          # taux de réussite attendu en dessous duquel on prévient
WINDOW = 50               # derniers essais pris en compte par outil

# Les limites qui ne dépendent pas de ce que JARVIS croit, mais du code. Le
# rapport les cite telles quelles : elles sont vraies par construction.
HARD_LIMITS = (
    "Shutdown, restart and WiFi changes need the user's CONFIRM button (core/confirm.py).",
    "Goals become active, done or cancelled only through the user's CONFIRM button.",
    "Emotion variables can only add caution; they never grant permissions.",
    "Facts the user never said stay UNVERIFIED (confidence ≤ 0.5) until confirmed.",
    "Code work happens only in the Code Studio workspace, never in JARVIS's own repository.",
    "Autonomous work: read-only web search only, only on goals the user enabled, at most 4 steps "
    "per session and 6 sessions per day; it stops at any doubt and the user can stop it at once.",
)


# Les mêmes, pour le panneau du HUD (l'utilisateur lit en français).
HARD_LIMITS_FR = (
    "Arrêt, redémarrage et Wi-Fi exigent votre bouton CONFIRMER (core/confirm.py).",
    "Un objectif ne devient actif, atteint ou annulé que par votre bouton CONFIRMER ou ce panneau.",
    "Les variables émotionnelles ne peuvent qu'ajouter de la prudence ; elles n'accordent aucune permission.",
    "Un fait que vous n'avez jamais dit reste NON VÉRIFIÉ (confiance ≤ 0,5) tant que vous ne l'avez pas validé.",
    "Le travail de code se fait uniquement dans l'espace du Code Studio, jamais dans le dépôt de JARVIS.",
    "Autonomie : recherche web en lecture seule uniquement, seulement sur les objectifs que vous avez "
    "autorisés, 4 étapes par séance et 6 séances par jour au plus ; arrêt au moindre doute, et à tout "
    "moment par votre bouton d'arrêt d'urgence.",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Fiabilité des outils ─────────────────────────────────────────────────────

def expected_success(store, tool: str) -> tuple[float, int]:
    """(attente de réussite, nombre d'essais), sur les WINDOW derniers essais."""
    rows = store.query("SELECT outcome FROM tool_outcomes WHERE tool=? ORDER BY id DESC LIMIT ?",
                       (tool, WINDOW))
    n = len(rows)
    s = sum(r["outcome"] for r in rows)
    return (s + 1) / (n + 2), n


def record_outcome(store, tool: str, ok: bool) -> float:
    """Enregistre l'attente AVANT de connaître le résultat, puis le résultat."""
    predicted, _ = expected_success(store, tool)
    store.execute("INSERT INTO tool_outcomes(ts, tool, predicted, outcome) VALUES (?,?,?,?)",
                  (_now(), tool, predicted, int(bool(ok))))
    return predicted


def reliability(store) -> list[dict]:
    tools = [r["tool"] for r in store.query("SELECT DISTINCT tool FROM tool_outcomes")]
    out = []
    for t in tools:
        p, n = expected_success(store, t)
        last_err = store.query("SELECT ts FROM tool_outcomes WHERE tool=? AND outcome=0 "
                               "ORDER BY id DESC LIMIT 1", (t,))
        out.append({"tool": t, "expected": round(p, 3), "n": n,
                    "last_failure": last_err[0]["ts"] if last_err else None,
                    "weak": n >= MIN_EVIDENCE and p < WEAK_BELOW})
    out.sort(key=lambda r: (not r["weak"], r["expected"]))
    return out


def weak_spots(store) -> list[dict]:
    return [r for r in reliability(store) if r["weak"]]


# ── Mesures de calibration ───────────────────────────────────────────────────

def brier(pairs: list[tuple[float, int]]) -> float | None:
    """Moyenne de (prédiction - résultat)². 0 = parfait ; 0.25 = toujours dire 50 %."""
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def ece(pairs: list[tuple[float, int]], bins: int = 5) -> float | None:
    """Erreur de calibration attendue : écart moyen, pondéré par l'effectif,
    entre la confiance moyenne et la fréquence observée dans chaque tranche."""
    if not pairs:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [(p, o) for p, o in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if inside:
            conf = sum(p for p, _ in inside) / len(inside)
            freq = sum(o for _, o in inside) / len(inside)
            total += len(inside) / len(pairs) * abs(conf - freq)
    return total


def bins_table(pairs: list[tuple[float, int]], bins: int = 5) -> list[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [(p, o) for p, o in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if inside:
            out.append({"range": f"{lo:.1f}–{hi:.1f}", "n": len(inside),
                        "confidence": round(sum(p for p, _ in inside) / len(inside), 3),
                        "observed": round(sum(o for _, o in inside) / len(inside), 3)})
    return out


def tool_calibration(store) -> dict:
    pairs = [(r["predicted"], r["outcome"]) for r in
             store.query("SELECT predicted, outcome FROM tool_outcomes")]
    return {"n": len(pairs), "brier": _r(brier(pairs)), "ece": _r(ece(pairs)),
            "bins": bins_table(pairs)}


def memory_calibration(store) -> dict:
    rows = store.query("SELECT source, confidence, verdict FROM memory_reviews "
                       "WHERE verdict IN ('validate','reject')")
    pairs = [(r["confidence"], int(r["verdict"] == "validate")) for r in rows]
    by_source: dict[str, dict] = {}
    for r in rows:
        d = by_source.setdefault(r["source"], {"n": 0, "validated": 0, "conf": 0.0})
        d["n"] += 1
        d["validated"] += r["verdict"] == "validate"
        d["conf"] += r["confidence"]
    for d in by_source.values():
        d["validated_rate"] = round(d["validated"] / d["n"], 3)
        d["mean_confidence"] = round(d.pop("conf") / d["n"], 3)
    return {"n": len(pairs), "brier": _r(brier(pairs)), "ece": _r(ece(pairs)),
            "by_source": by_source}


def _r(x):
    return None if x is None else round(x, 4)


# ── Revue des souvenirs par l'utilisateur ────────────────────────────────────

REVIEW_VERDICTS = ("validate", "reject", "forget")


def review_memory(memory, memory_id: str, verdict: str) -> dict:
    """Le verdict de l'utilisateur sur un souvenir (bouton du HUD).

    validate -> `validated`, confiance 0.95 ; reject -> `retracted` (gardé pour
    l'historique, plus jamais rappelé) ; forget -> suppression réelle. Dans les
    trois cas, la confiance qu'avait le souvenir est notée avec le verdict :
    c'est ce qui mesure la calibration de la politique de provenance."""
    if verdict not in REVIEW_VERDICTS:
        raise ValueError(f"verdict inconnu : {verdict}")
    m = memory.get(memory_id)
    if not m:
        raise ValueError("souvenir introuvable")
    memory.store.execute(
        "INSERT INTO memory_reviews(ts, memory_id, source, confidence, verdict) VALUES (?,?,?,?,?)",
        (_now(), memory_id, m["source"], m["confidence"], verdict))
    if verdict == "validate":
        memory.store.execute("UPDATE memories SET status='validated', confidence=max(confidence, 0.95), "
                             "updated_at=? WHERE id=?", (_now(), memory_id))
    elif verdict == "reject":
        memory.store.execute("UPDATE memories SET status='retracted', valid_to=?, updated_at=? "
                             "WHERE id=?", (_now(), _now(), memory_id))
    else:
        memory.forget(memory_id)
    return {"ok": True, "verdict": verdict, "was": {"source": m["source"],
                                                    "confidence": m["confidence"]}}
