## JARVIS Night Shift — 2026-10-16 · passe 3/3

**15/15 suites vertes ; aucune régression ; ruff : 0 remarque ; rien à corriger.**

commit `0534773` · branche `main` · ubuntu24 20261012.334.1 · Python 3.12.15 · 1 min 00 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/38871469222)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.5 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 2.0 s |
| server.routing_selftest | 35/35 | ✅ vert | 0.7 s |
| client_desktop --selftest | 28/28 | ✅ vert | 0.7 s |
| cognition.selftest | 64/64 | ✅ vert | 3.6 s |
| eval_lab.selftest | 28/28 | ✅ vert | 6.8 s |
| core.code_workspace.selftest | 34/34 | ✅ vert | 33.7 s |
| reminders.selftest | 4/4 | ✅ vert | 0.3 s |
| resilience.selftest | 13/13 | ✅ vert | 0.7 s |
| extensions.selftest | 11/11 | ✅ vert | 0.8 s |
| offline.selftest | 10/10 | ✅ vert | 1.3 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 32/32 | ✅ vert | 6.4 s |

### Tâche de la nuit

- rien à corriger
- Consommation : 0 appel(s) sur 12 · 0 tokens sur 60000

### Cette nuit, passe par passe

- Passe 1 : F841 eval_lab/mutate.py — correctif validé · 1160 tokens
- Passe 3 : rien de tenté — rien de tenté · 0 tokens

### Indicateurs — JARVIS s'améliore-t-il ?

- eval_lab legacy : 77/77 réussis
- eval_lab regressions : 9/9 réussis
- Coût en contexte : prompt 3540 car. + outils 23520 car. (34 outils, mode local) ≈ 7731 tokens par connexion
- Cognition : 14 valeurs mesurées (architecture complète)

### Modèle local

- indisponible : aucun avis local : PC éteint, ou worker non installé (phase 5, facultative)

### Backlog du graphe — 12 nœud(s) bug ou partiel

- partiel · `context/l.voiture` — Détection de la voiture · zone autorisée
- partiel · `general/tool.weather_report` — weather_report · hors zone
- partiel · `general/tool.youtube_video` — youtube_video · hors zone
- partiel · `nightshift/nightshift` — nightshift/ — le veilleur de nuit · hors zone
- partiel · `preprod/t.audio` — audio · hors zone
- partiel · `server/l.barge_in` — Interruption à la voix (option) · hors zone
- partiel · `general/f.nightshift` — Veilleur de nuit (Night Shift) · hors zone
- partiel · `general/tool.reminder` — reminder · rappels · hors zone
- partiel · `general/tool.system_status` — system_status · hors zone
- partiel · `android/m.matrice` — Ce qui arrive sur Android · hors zone
- partiel · `cognition/l.local` — Mode local seulement · hors zone
- partiel · `deployment/l.dependances` — Ce qui dépend d'autre chose que ce PC · hors zone
