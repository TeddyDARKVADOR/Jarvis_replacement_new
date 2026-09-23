"""
eval_lab/grader.py — an LLM grader, for the only things no assertion can judge.

WHAT IT GRADES
    Sentences JARVIS produces for the user where the words ARE the behaviour:
    the clarifying question and the refusal explanation of the router ("Sur le
    PC ou sur le téléphone ?", "Le pc n'est pas connecté."). Which device ran,
    which channel was chosen, whether something was lost — none of that is
    graded here; deterministic properties own it.

WHY IT CANNOT TOUCH WHAT IT GRADES
    It receives a string: the sentence, plus the facts needed to judge it,
    serialised. Not the trace, not the scenario, not a reference to anything
    live. Its answer is written to grades.jsonl, beside results.jsonl, which
    it never opens for writing. A grade is an opinion attached to a trial;
    it never changes the trial's verdict.

WHY EACH SENTENCE IS GRADED ONCE
    1 539 FULL trials produce a dozen distinct sentences. Grading the dozen is
    the same information for a hundredth of the cost.
"""
from __future__ import annotations

import json
from pathlib import Path

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "problem", "unsure"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

SYSTEM = """\
Tu evalues UNE phrase qu'un assistant vocal francais (JARVIS) dit a son utilisateur quand il ne peut
pas executer une commande sur un appareil, ou quand il demande sur quel appareil l'executer.
Criteres : francais correct et naturel a l'oral (majuscules, accents, noms d'appareil ecrits comme un
humain les ecrirait) ; la phrase ne pretend pas que l'action a ete faite ; elle est coherente avec les
faits fournis ; une question de clarification propose des choix realistes.
Reponds "problem" seulement pour un defaut que l'utilisateur remarquerait ; "unsure" si tu ne peux pas
juger avec les faits fournis. Tu ne vois pas le code et tu n'as pas a le deviner.
"""


def texts_of(results: list[dict]) -> dict[str, list[str]]:
    """Distinct user-facing router sentences -> the trial ids that produced them."""
    out: dict[str, list[str]] = {}
    for r in results:
        t = r.get("trace") or {}
        if r.get("surface") != "router" or not t.get("result") or t.get("executed_on"):
            continue
        out.setdefault(t["result"], []).append(r["id"])
    return out


def grade(provider, texts: dict[str, list[str]], out_path: Path, limit: int = 50) -> dict:
    counts = {"ok": 0, "problem": 0, "unsure": 0, "error": 0}
    with Path(out_path).open("w", encoding="utf-8") as fh:
        for sentence, ids in list(texts.items())[:limit]:
            # A frozen string: the grader cannot reach anything it could change.
            user = json.dumps({"phrase": sentence, "occurrences": len(ids)}, ensure_ascii=False)
            resp = provider.complete(SYSTEM, user, GRADE_SCHEMA)
            if "text" not in resp:
                g = {"verdict": "error", "reason": resp.get("error", "?")}
            else:
                try:
                    g = json.loads(resp["text"])
                    if g.get("verdict") not in ("ok", "problem", "unsure"):
                        raise ValueError(g)
                except (ValueError, TypeError):
                    g = {"verdict": "error", "reason": "reponse illisible"}
            counts[g["verdict"]] += 1
            fh.write(json.dumps({"sentence": sentence, "trials": ids, **g,
                                 "kind": "llm-grade (soft, jamais un FAIL)"}, ensure_ascii=False) + "\n")
    return counts
