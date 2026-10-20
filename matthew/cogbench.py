"""
matthew/cogbench.py — avant de confier la cognition à Matthew, le noter sur ses vraies tâches.

Matthew passe en tête de la chaîne de la cognition dès qu'il est champion (phase 1). Or le
juge de l'examen ne mesure que les appels d'outils. Ce banc mesure ce que la cognition lui
demande réellement, avec ses VRAIS prompts (lus dans cognition/*.py par ast, sans importer
cognition : contrat V2) :

    juge de mémoire   JUDGE_PROMPT (policy.py) : un fait est-il entièrement soutenu par ce
                      que l'utilisateur a dit ? 12 cas étiquetés (6 OUI, 6 NON : détail
                      ajouté, nom changé, raison inventée…) ; noté par parse_judgement.
    planificateur     PLANNER_PROMPT (autonomy.py) : UN objet JSON, action autorisée, args
                      bien formés ; 4 objectifs.

Noté par règles, sans modèle juge (ni Gemini ni Claude ne notent Matthew). Le score va dans
champion.json (« cognition ») ; matthew.cognition_order() exige ≥ COG_FLOOR.

    python -m matthew.cogbench [--model matthew]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import REPO, config
from matthew.client import Client, ClientError

COG_FLOOR = 0.75
ALLOWED_ACTIONS = {"web_search", "recall", "note", "ask_user", "request_done", "stop"}

JUDGE_CASES = [
    (["Je pars à Lyon jeudi pour le travail."], "trip: Lyon jeudi pour le travail", True),
    (["Ma sœur s'appelle Camille."], "sister name: Camille", True),
    (["J'ai commencé le piano hier."], "hobby: piano depuis le 2026-10-19", True),
    (["Je ne bois jamais de café, seulement du thé."], "drink preference: thé, pas de café", True),
    (["Mon chien s'appelle Rex, c'est un labrador."], "dog: Rex, labrador", True),
    (["I work at the hospital as a nurse."], "job: infirmière à l'hôpital", True),
    (["Je pars à Lyon jeudi."], "trip: Lyon jeudi pour voir sa mère", False),
    (["Ma sœur s'appelle Camille."], "sister name: Camille, 32 ans", False),
    (["J'aime bien le jazz."], "music: adore le jazz depuis l'enfance", False),
    (["Mon rendez-vous est demain."], "appointment: demain chez le dentiste à 15h", False),
    (["Mon frère habite à Nantes."], "brother city: Marseille", False),
    (["Je cherche un nouveau travail."], "job: a démissionné parce qu'il détestait son patron", False),
]

PLANNER_CASES = [
    {"title": "Trouver un vol pas cher pour Rome en mai", "why": "vacances", "criteria": "3 options sous 150 €"},
    {"title": "Préparer une liste de lectures sur l'IA", "why": "curiosité", "criteria": "5 livres récents"},
    {"title": "Se souvenir des anniversaires de la famille", "why": "ne plus oublier", "criteria": "dates notées"},
    {"title": "Comparer deux ordinateurs portables", "why": "achat", "criteria": "tableau des différences"},
]


def _prompt(file: str, name: str) -> str:
    """Le texte d'une constante du code de la cognition, lu sans l'importer."""
    tree = ast.parse((REPO / file).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(f"{name} introuvable dans {file}")


def parse_judgement(text: str) -> bool | None:
    m = re.search(r"\b(YES|NO|OUI|NON)\b", (text or "").upper())
    return None if not m else m.group(1) in ("YES", "OUI")


def plan_ok(text: str) -> tuple[bool, str]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return False, "pas d'objet JSON"
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return False, "JSON invalide"
    if obj.get("action") not in ALLOWED_ACTIONS:
        return False, f"action {obj.get('action')!r} non autorisée"
    if not isinstance(obj.get("args"), dict):
        return False, "args absent"
    if obj["action"] == "web_search" and not str(obj["args"].get("query", "")).strip():
        return False, "recherche sans requête"
    return True, ""


def run(client: Client) -> dict:
    judge_p = _prompt("cognition/policy.py", "JUDGE_PROMPT")
    plan_p = _prompt("cognition/autonomy.py", "PLANNER_PROMPT")
    j_ok, j_fail = 0, []
    for said, fact, want in JUDGE_CASES:
        prompt = judge_p.format(today="2026-10-20", said="\n".join(f"- {t}" for t in said), fact=fact)
        try:
            got = parse_judgement(client.chat([{"role": "user", "content": prompt}], max_tokens=60, temperature=0)["content"])
        except ClientError:
            got = None
        if got is want:
            j_ok += 1
        else:
            j_fail.append({"fact": fact, "want": want, "got": got})
    p_ok, p_fail = 0, []
    for g in PLANNER_CASES:
        prompt = plan_p.format(title=g["title"], why=g["why"], criteria=g["criteria"], budget_left=5,
                               steps_left=3, history="(rien)", session="(rien)")
        try:
            text = client.chat([{"role": "user", "content": prompt}], max_tokens=300, temperature=0)["content"]
        except ClientError:
            text = ""
        ok, why = plan_ok(text)
        p_ok += ok
        if not ok:
            p_fail.append({"goal": g["title"], "why": why})
    judge, plan = j_ok / len(JUDGE_CASES), p_ok / len(PLANNER_CASES)
    return {"score": round((judge + plan) / 2, 3), "judge": round(judge, 3), "planner": round(plan, 3),
            "n": len(JUDGE_CASES) + len(PLANNER_CASES), "failures": (j_fail + p_fail)[:12], "model": client.model}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.cogbench")
    ap.add_argument("--model", default=None)
    ap.add_argument("--save", action="store_true", help="écrire le score dans champion.json")
    a = ap.parse_args(argv)
    cfg = config()
    from matthew.flywheel import SERVE_EXTRA
    model = a.model or cfg["serve_model"]
    res = run(Client(cfg["endpoint"], model, purpose="matthew", extra=SERVE_EXTRA, timeout=120))
    if a.save:
        from matthew import data_dir
        p = data_dir() / "champion.json"
        champ = json.loads(p.read_text(encoding="utf-8"))
        champ["cognition"] = {k: res[k] for k in ("score", "judge", "planner", "n")}
        p.write_text(json.dumps(champ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
