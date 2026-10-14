"""
nightshift/ — le veilleur de nuit de JARVIS (phase 1 du plan Night Shift).

Chaque nuit à 03 h 17 (heure de Paris), le workflow .github/workflows/nightly.yml
lance les suites du dépôt testables sous Linux et ruff, compare avec la nuit
précédente et pousse un rapport `jarvis.nightly/1` sur la branche orpheline
`nightshift-data` — jamais sur main. Le matin, JARVIS lit ce rapport sans jeton
(dépôt public) : une phrase au briefing quand il y a du nouveau, le détail
dans l'onglet NUIT du panneau ESPRIT. Aucune IA en phase 1.

    python -m nightshift.run --out DIR    lancer les suites ici, écrire le rapport
    python -m nightshift.run --list       les suites et ce qu'on sait d'elles
    python -m nightshift.selftest         sans réseau

Côté CI : suites.py, run.py, compare.py, report.py.
Côté PC : fetch.py, briefing.py, hud.py, state.py.

Paquet racine au sens du contrat V2 (graphe `contrat`) : il n'importe rien de
main.py, du cœur, du serveur, des clients ni des autres paquets de JARVIS. Le
supprimer ne casse rien : ses hooks (main.py : attach et une ligne au
briefing ; local_hud.py : la route nightshift.* ; le HUD : l'onglet NUIT) sont
gardés et redeviennent inertes. La documentation est le graphe
capabilities/graphs/nightshift.json.
"""
from __future__ import annotations

SCHEMA = "jarvis.nightly/1"
DATA_BRANCH = "nightshift-data"
DEFAULT_REPO = "TeddyDARKVADOR/Jarvis_replacement_new"


def attach(jarvis) -> None:
    """Hook de main.py : donne au briefing `jarvis._night_clause()` et lance la
    lecture du rapport en fond, pour que le briefing n'attende pas le réseau.
    Ne lève jamais."""
    try:
        from nightshift import briefing
        jarvis._night_clause = briefing.clause_now
        briefing.prefetch()
    except Exception:
        pass
