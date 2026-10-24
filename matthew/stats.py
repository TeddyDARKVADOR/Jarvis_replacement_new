"""
matthew/stats.py — des barres d'erreur sur les examens, pour ne pas promouvoir sur du bruit.

Avec 93 cas et un score de 0,90, l'intervalle à 95 % fait ±6 points : une marge fixe de
2 points ne prouve rien. D'après « Adding Error Bars to Evals » (Miller, arXiv 2411.00640) :

    1. les passes répétées d'un même cas sont d'abord moyennées (la question est l'unité,
       pas la passe : sinon l'erreur type est sous-estimée) ;
    2. candidat et champion répondent aux MÊMES cas : on compare les écarts cas par cas
       (test apparié), bien plus sensible que deux moyennes indépendantes ;
    3. l'intervalle de l'écart vient d'un bootstrap apparié (on retire des cas avec remise) ;
       calibré comme McNemar sur les scores binaires (arXiv 2605.30315), et il accepte nos
       scores fractionnaires (part des arguments justes).

Promotion seulement si la borne basse de l'écart est au-dessus de zéro ; sinon « indécis » et
le champion reste. Graine fixe : deux décisions sur les mêmes notes donnent le même verdict.
Sans dépendance (pas de numpy) : quelques centaines de cas, 2 000 tirages, une fraction de seconde.
"""
from __future__ import annotations

import math
import random

EXPLORATORY_N = 10        # en dessous, le score d'un outil est indicatif, pas une mesure


def mean_ci(values: list[float], z: float = 1.96) -> dict:
    """Moyenne et intervalle normal (erreur type des cas) ; n < 2 → intervalle [0 ; 1]."""
    n = len(values)
    if not n:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    m = sum(values) / n
    if n < 2:
        return {"mean": round(m, 4), "lo": 0.0, "hi": 1.0, "n": n}
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))
    half = z * sd / math.sqrt(n)
    return {"mean": round(m, 4), "lo": round(max(0.0, m - half), 4), "hi": round(min(1.0, m + half), 4), "n": n}


def wilson(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de Wilson : reste sensé à 0/1 ou 1/1 (là où l'intervalle normal dit [0 ; 0])."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def per_target(items: list[dict]) -> dict[str, dict]:
    """Par outil visé : moyenne, Wilson, n, et « exploratoire » sous EXPLORATORY_N cas."""
    by: dict[str, list[float]] = {}
    for it in items:
        by.setdefault(it["t"], []).append(it["s"])
    out = {}
    for t, v in sorted(by.items()):
        lo, hi = wilson(sum(v), len(v))
        out[t] = {"mean": round(sum(v) / len(v), 3), "lo": lo, "hi": hi, "n": len(v),
                  "exploratory": len(v) < EXPLORATORY_N}
    return out


def paired_bootstrap(a: list[float], b: list[float], iters: int = 2000, seed: int = 0,
                     level: float = 0.95) -> dict:
    """Écart moyen a − b sur les mêmes cas, intervalle percentile du bootstrap apparié,
    et p bilatéral (part des tirages du mauvais côté de zéro, ×2)."""
    if len(a) != len(b) or not a:
        raise ValueError("bootstrap apparié : deux listes de même longueur, non vides")
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    rnd = random.Random(seed)
    means = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    tail = (1 - level) / 2
    lo, hi = means[int(tail * iters)], means[min(iters - 1, int((1 - tail) * iters))]
    diff = sum(d) / n
    side = sum(1 for m in means if (m <= 0 if diff > 0 else m >= 0)) / iters
    return {"diff": round(diff, 4), "lo": round(lo, 4), "hi": round(hi, 4),
            "p": round(min(1.0, 2 * side), 4), "n": n,
            "better": sum(1 for x in d if x > 0), "worse": sum(1 for x in d if x < 0)}


def paired(candidate: dict, champion: dict) -> dict | None:
    """Les cas notés des deux examens s'ils sont appariables (mêmes cas, même ordre), sinon None."""
    a, b = candidate.get("items") or [], champion.get("items") or []
    if not a or len(a) != len(b) or any(x["id"] != y["id"] for x, y in zip(a, b)):
        return None
    return paired_bootstrap([x["s"] for x in a], [y["s"] for y in b])


def strip(score: dict) -> dict:
    """La note sans le détail cas par cas (champion.json, vitrine, rapports légers)."""
    return {k: v for k, v in score.items() if k != "items"}
