"""
nightshift/hud.py — l'onglet NUIT du panneau ESPRIT (lecture seule).

    nightshift.latest   {refresh}   le dernier rapport, son âge, d'où il vient

Appelé par la page du HUD via local_hud.LocalBackend, comme cognition.* : un
clic de l'utilisateur, jamais un outil du modèle. Le rapport vient de
fetch.py (cache de 30 min ; « refresh » force une relecture). Toutes les
réponses sont des dicts JSON ; une erreur est {"error": "..."}.
"""
from __future__ import annotations

from nightshift import DATA_BRANCH, fetch

OPS = ("nightshift.latest",)


def call(op: str, data: dict | None = None) -> dict:
    data = data if isinstance(data, dict) else {}
    if op not in OPS:
        return {"error": f"opération inconnue : {op}"}
    try:
        st = fetch.status(force=bool(data.get("refresh")))
        repo = st["repo"]
        return {**st,
                "actions_url": f"https://github.com/{repo}/actions/workflows/nightly.yml",
                "data_url": f"https://github.com/{repo}/tree/{DATA_BRANCH}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
