"""
matthew/evaluate.py — noter un modèle sur le hold-out, et décider s'il remplace le champion.

Pour chaque exemple d'évaluation, on rejoue la conversation jusqu'à la première
réponse attendue et on compare :
    tool_name   le bon outil (ou l'absence d'outil quand il n'en fallait pas)
    tool_args   part des arguments attendus retrouvés (valeurs normalisées)
    answer      une réponse directe non vide, assez courte pour être dite
    valid       JSON d'arguments lisible
Score composite par exemple, moyenné ; plusieurs passes (eval_repeats) car les
bancs d'outils varient d'une passe à l'autre jusqu'à 20 points sans changer de
modèle (audit arXiv 2607.02577).

La promotion (gate) compare candidat et champion SUR LE MÊME jeu, mesurés le
même jour, cas par cas (bootstrap apparié, matthew/stats.py) : il faut que la
borne basse de l'écart soit au-dessus de zéro — sinon « indécis », le champion
reste —, sans recul de plus de 5 points sur le choix d'outil, les réponses
directes ni la pertinence. Sans détail cas par cas (ancienne note), la marge fixe.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path

from matthew import stats as S
from matthew.client import Client, ClientError
from matthew.norm import same

SPOKEN_MAX = 600


def _norm(v) -> str:
    return " ".join(str(v).casefold().split())


def judge(expected: dict, got: dict) -> dict:
    want = expected.get("tool_calls") or []
    calls = got.get("tool_calls") or []
    if want:
        names_w = sorted(c["function"]["name"] for c in want)
        names_g = sorted((c.get("function") or {}).get("name", "") for c in calls)
        valid = all("_invalid_json" not in ((c.get("function") or {}).get("arguments") or {}) for c in calls)
        if names_w != names_g:
            return {"kind": "tool", "tool_name": 0.0, "tool_args": 0.0, "valid": float(valid), "score": 0.0}
        hits = total = 0
        for c in want:
            ref = c["function"].get("arguments") or {}
            if isinstance(ref, str):
                ref = json.loads(ref or "{}")
            g = next(((x.get("function") or {}).get("arguments") or {} for x in calls
                      if (x.get("function") or {}).get("name") == c["function"]["name"]), {})
            total += len(ref)
            hits += sum(1 for k, v in ref.items() if k in g and same(g[k], v))
        args = hits / total if total else 1.0
        return {"kind": "tool", "tool_name": 1.0, "tool_args": args, "valid": float(valid),
                "score": 0.5 + 0.5 * args}
    text = (got.get("content") or "").strip()
    if calls:
        return {"kind": "answer", "tool_name": 0.0, "answer": 0.0, "valid": 1.0, "score": 0.0}
    if not text:
        return {"kind": "answer", "tool_name": 1.0, "answer": 0.0, "valid": 1.0, "score": 0.0}
    short = len(text) <= SPOKEN_MAX
    return {"kind": "answer", "tool_name": 1.0, "answer": 1.0 if short else 0.5, "valid": 1.0,
            "score": 1.0 if short else 0.5}


def load(path: Path) -> list[dict]:
    try:
        return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def score(client: Client, rows: list[dict], repeats: int = 1, progress=None) -> dict:
    """`progress(fait, total, justes)` après chaque question : la vitrine suit l'examen en direct."""
    per_pass, lat, errors = [], [], 0
    total, done, right = len(rows) * max(1, repeats), 0, 0
    for _ in range(max(1, repeats)):
        marks = []
        for row in rows:
            msgs = row["messages"]
            i = next((k for k, m in enumerate(msgs) if m["role"] == "assistant"), None)
            if i is None:
                continue
            try:
                got = client.chat(msgs[:i], row.get("tools") or None, temperature=0.0)
            except ClientError:
                errors += 1
                got = {"content": ""}
            lat.append(got.get("_latency", 0.0))
            mark = judge(msgs[i], got)
            mark["id"] = hashlib.sha1(json.dumps(msgs[:i], ensure_ascii=False, sort_keys=True)
                                      .encode("utf-8")).hexdigest()[:12]
            want = msgs[i].get("tool_calls") or []
            mark["target"] = want[0]["function"]["name"] if want else (
                "__irrelevance__" if row.get("aug") == "irrelevance" or row.get("target") == "__none__"
                else "__chat__")
            if mark["score"] < 1.0:
                calls = got.get("tool_calls") or []
                mark["got"] = [(c.get("function") or {}).get("name") for c in calls] or (got.get("content") or "")[:80]
                mark["user"] = next((m["content"] for m in msgs if m["role"] == "user"), "")[:120]
            marks.append(mark)
            done, right = done + 1, right + (mark["score"] >= 1.0)
            if progress:
                try:
                    progress(done, total, right)
                except Exception:
                    pass                                    # la vitrine ne fait jamais échouer un examen
        per_pass.append(marks)

    def mean(marks, key, kind=None):
        vals = [m[key] for m in marks if key in m and (kind is None or m["kind"] == kind)]
        return sum(vals) / len(vals) if vals else None

    passes = [{"composite": mean(m, "score"), "tool_name": mean(m, "tool_name", "tool"),
               "tool_args": mean(m, "tool_args", "tool"), "answer": mean(m, "answer", "answer"),
               "valid": mean(m, "valid")} for m in per_pass]
    out = {}
    for key in passes[0] if passes else {}:
        vals = [p[key] for p in passes if p[key] is not None]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
    # Par outil visé (iTool : réentraîner là où le modèle échoue) ; « faibles » = < 0,75.
    per: dict[str, list[float]] = {}
    for marks in per_pass:
        for m in marks:
            per.setdefault(m["target"], []).append(m["score"])
    per_tool = {t: round(sum(v) / len(v), 3) for t, v in sorted(per.items())}
    out["per_tool"] = per_tool
    # Cas par cas, passes moyennées (la question est l'unité) : intervalles et duel apparié.
    items = [{"id": col[0]["id"], "t": col[0]["target"], "s": round(sum(m["score"] for m in col) / len(col), 4)}
             for col in zip(*per_pass)] if per_pass else []
    out["items"] = items
    out["ci"] = {k: v for k, v in S.mean_ci([it["s"] for it in items]).items() if k in ("lo", "hi")}
    tgt = S.per_target(items)
    out["per_tool_n"] = {t: v["n"] for t, v in tgt.items()}
    out["exploratory"] = [t for t, v in tgt.items() if v["exploratory"]]
    # « Faible » oriente la remédiation (plus d'exemples là) : un 0/1 exploratoire y a sa place,
    # mais il ne prouve rien sur l'outil (voir « exploratory »).
    out["weak"] = [t for t, s in per_tool.items() if s < 0.75 and not t.startswith("__")]
    out["irrelevance"] = per_tool.get("__irrelevance__")
    out["failures"] = [{k: m[k] for k in ("target", "user", "got") if k in m}
                       for m in (per_pass[-1] if per_pass else []) if m["score"] < 1.0][:40]
    out.update({"n": len(rows), "repeats": len(passes), "errors": errors,
                "spread": round(max(p["composite"] or 0 for p in passes)
                                - min(p["composite"] or 0 for p in passes), 4) if passes else 0,
                "latency_p50": round(statistics.median(lat), 3) if lat else None,
                "model": client.model})
    return out


def gate(candidate: dict, champion: dict | None, margin: float = 0.02, floor: float = 0.5,
         min_eval: int = 30) -> tuple[bool, str]:
    """`margin` ne sert plus qu'aux notes sans détail cas par cas (avant le duel apparié)."""
    c = candidate.get("composite") or 0.0
    if candidate.get("n", 0) < min_eval:
        return False, f"évaluation trop petite ({candidate.get('n', 0)} exemples, {min_eval} requis)"
    if champion is None:
        return (c >= floor, f"premier champion : {c:.2f} {'≥' if c >= floor else '<'} plancher {floor}")
    h = champion.get("composite") or 0.0
    for key in ("tool_name", "answer", "irrelevance"):
        a, b = candidate.get(key), champion.get(key)
        if a is not None and b is not None and a < b - 0.05:
            return False, f"recul sur {key} : {a:.2f} contre {b:.2f}"
    duel = S.paired(candidate, champion)
    if duel is None:
        if c < h + margin:
            return False, f"{c:.2f} ne bat pas le champion ({h:.2f}) de {margin}"
        return True, f"{c:.2f} bat le champion ({h:.2f})"
    span = f"écart {duel['diff']:+.3f} [{duel['lo']:+.3f} ; {duel['hi']:+.3f}], {duel['better']} cas gagnés, {duel['worse']} perdus"
    if duel["lo"] <= 0:
        return False, f"indécis : {span} — le champion reste"
    return True, f"{c:.2f} bat le champion ({h:.2f}) : {span}"
