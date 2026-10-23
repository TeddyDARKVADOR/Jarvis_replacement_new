"""
cognition/ — architecture cognitive persistante de Matthew (phases 1–2).

Paquet racine au sens du contrat V2 (capabilities/graphs/contrat.json) : il n'importe rien de main.py,
dashboard/, actions/, server/ ou plugins/ ; main.py l'appelle par des hooks
d'une ligne, gardés. Supprimer ce dossier rend Matthew identique à avant.

    python -m cognition.selftest      # sans clé, sans réseau, sans micro
    python -m cognition.providers     # sonde réelle des générateurs configurés

Voir capabilities/graphs/cognition.json et docs/cognition/RECHERCHE.md.
"""
from .runtime import Cognition, attach, get_active

__all__ = ["Cognition", "attach", "get_active"]
