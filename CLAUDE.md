# JARVIS — consignes de travail

## La documentation, ce sont les graphes

Les fonctionnalités de JARVIS ne se décrivent plus dans des README : elles sont
dans `capabilities/graphs/*.json` (format `jarvis.graph/1`, voir
`capabilities/__init__.py`). `general.json` couvre tout JARVIS ; chaque
sous-graphe détaille une partie (`server`, `hud`, `client_desktop`, `android`,
`cognition`, `context`, `studio`, `deployment`, `eval_lab`, `preprod`,
`contrat`). JARVIS lit ces graphes lui-même (outil `capabilities`) et les
affiche dans le HUD quand on le lui demande.

- **Ne pas créer de README ni de nouveau `.md` de documentation.** Une
  fonctionnalité se décrit par des nœuds et des arêtes ; les explications vont
  dans `summary` et `details`. `capabilities.selftest` échoue si un README de
  dossier revient.
- Restent en `.md` pour l'instant : ce fichier, `readme.md` (page du dépôt),
  `docs/cognition/RECHERCHE.md` (bibliographie), les rapports datés de
  `docs/cognition/evaluations/` et `cognition/constitution/CHANGELOG.md`.

## À chaque fin de session : mettre le graphe à jour

Avant de conclure une session où du code a changé :

1. Pour chaque fonctionnalité ajoutée, modifiée ou retirée, modifier le graphe
   concerné : nœuds (`summary`, `details`, `files`, `status`), arêtes, et
   **`updated` à la date du jour**. Un nouvel outil a son nœud de genre
   `outil` dans `general.json`, avec `tool` et le fichier qui le déclare.
2. L'état d'un nœud dit ce qui a été **vérifié** : `ok`, `partiel`,
   `a_tester`, `bug` ; sans `status` = non évalué. Ne pas marquer `ok` ce qui
   n'a pas été vérifié.
3. `python -m capabilities.selftest` doit passer (outils, fichiers, liens,
   README).

## Le contrat V2

Toute fonctionnalité ajoutée après V1 se retire sans casser V1 (graphe
`contrat`) : plugin d'abord, puis Android seul, puis paquet racine avec un hook
d'une ligne, et le cœur (`main.py`, `core/`) en dernier recours. `actions/` est
figé : les nouvelles capacités vont dans `plugins/`.

## Vérifier

```
python -m capabilities.selftest
python -m context.selftest
python -m server.selftest
python -m server.routing_selftest
python -m client_desktop --selftest
python -m cognition.selftest
python -m eval_lab.selftest
python -m core.code_workspace.selftest
```

Les selftests `server`, `client_desktop` et `eval_lab` échouent tant qu'une
modification de `main.py`, `core/`, `actions/`, `plugins/`, `dashboard/`,
`memory/` ou `requirements.txt` n'est pas commitée : c'est voulu.
