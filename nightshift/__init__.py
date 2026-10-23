"""
nightshift/ — le veilleur de nuit de Matthew (phase 1 du plan Night Shift).

Trois fois par nuit (01 h 17, 03 h 17, 05 h 17, heure de Paris — passes.py),
le workflow .github/workflows/nightly.yml lance les suites du dépôt testables
sous Linux et ruff, compare avec la passe précédente et pousse un rapport `matthew_assistant.nightly/1` sur la branche orpheline
`nightshift-data` — jamais sur main. Le matin, Matthew lit ce rapport sans jeton
(dépôt public) : une phrase au briefing quand il y a du nouveau, le détail
dans l'onglet NUIT du panneau ESPRIT. Aucune IA en phase 1.

    python -m nightshift.run --out DIR    lancer les suites ici, écrire le rapport
    python -m nightshift.run --list       les suites et ce qu'on sait d'elles
    python -m nightshift.selftest         sans réseau

Côté CI : suites.py, run.py, compare.py, report.py.
Côté PC : fetch.py, briefing.py, hud.py, state.py.

Paquet racine au sens du contrat V2 (graphe `contrat`) : il n'importe rien de
main.py, du cœur, du serveur, des clients ni des autres paquets de Matthew. Le
supprimer ne casse rien : ses hooks (main.py : attach et une ligne au
briefing ; local_hud.py : la route nightshift.* ; le HUD : l'onglet NUIT) sont
gardés et redeviennent inertes. La documentation est le graphe
capabilities/graphs/nightshift.json.
"""
from __future__ import annotations

SCHEMA = "matthew_assistant.nightly/1"
SCHEMAS = (SCHEMA, "jarvis.nightly/1")   # ancien nom, encore lu
DATA_BRANCH = "nightshift-data"
DEFAULT_REPO = "TeddyDARKVADOR/Jarvis_replacement_new"


def attach(matthew_assistant) -> None:
    """Hook de main.py : donne au briefing `matthew_assistant._night_clause()` et lance la
    lecture du rapport en fond, pour que le briefing n'attende pas le réseau.
    Ne lève jamais."""
    try:
        from nightshift import briefing
        matthew_assistant._night_clause = briefing.clause_now
        briefing.prefetch()
    except Exception:
        pass
