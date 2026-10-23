## JARVIS Night Shift — 2026-10-23 · passe 3/3

**21/21 suites vertes ; aucune régression ; ruff : 5 remarques ; rien à corriger.**

commit `763c975` · branche `main` · ubuntu24 20261012.334.1 · Python 3.12.15 · 1 min 27 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/39924885862)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.5 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 1.9 s |
| server.routing_selftest | 39/39 | ✅ vert | 0.6 s |
| client_desktop --selftest | 28/28 | ✅ vert | 0.6 s |
| cognition.selftest | 65/65 | ✅ vert | 4.8 s |
| eval_lab.selftest | 28/28 | ✅ vert | 5.5 s |
| core.code_workspace.selftest | 35/35 | ✅ vert | 33.6 s |
| reminders.selftest | 4/4 | ✅ vert | 0.2 s |
| resilience.selftest | 15/15 | ✅ vert | 0.6 s |
| extensions.selftest | 15/15 | ✅ vert | 0.8 s |
| offline.selftest | 10/10 | ✅ vert | 1.2 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 38/38 | ✅ vert | 6.3 s |
| meeting.selftest | 21/21 | ✅ vert | 0.9 s |
| pilot.selftest | 13/13 | ✅ vert | 1.7 s |
| quota.selftest | 7/7 | ✅ vert | 0.0 s |
| daily.selftest | 8/8 | ✅ vert | 15.2 s |
| incidents.selftest | 6/6 | ✅ vert | 0.2 s |
| matthew.selftest | 44/44 | ✅ vert | 10.6 s |

### Tâche de la nuit

- rien à corriger
- Consommation : 0 appel(s) sur 12 · 0 tokens sur 60000

### Cette nuit, passe par passe

- Passe 1 : rien de tenté — rien de tenté · 0 tokens
- Passe 2 : rien de tenté — rien de tenté · 0 tokens
- Passe 3 : rien de tenté — rien de tenté · 0 tokens

### Échec persistant — manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte

Idée `check:extensions.selftest:manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte`, 2 tentatives sur le même contexte.

**Faits**
- 2026-10-17 · commit 47a5308 · patch_rejected : le n°manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte échoue encore (groq/openai/gpt-oss-120b)
- 2026-10-21 · commit 7212e01 · patch_rejected : le n°manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte échoue encore (groq/openai/gpt-oss-120b)

**Hypothèses** (non vérifiées)
- le correctif ne touche pas la cause

### Indicateurs — JARVIS s'améliore-t-il ?

- eval_lab legacy : 77/77 réussis
- eval_lab regressions : 9/9 réussis
- Coût en contexte : prompt 3552 car. + outils 30894 car. (42 outils, mode local) ≈ 9841 tokens par connexion
- Cognition : 14 valeurs mesurées (architecture complète)

### Modèle local

- indisponible : aucun avis local : PC éteint, ou worker non installé (phase 5, facultative)

### Backlog du graphe — 23 nœud(s) bug ou partiel

- partiel · `client_desktop/f.instance` — Une seule instance · hors zone
- partiel · `context/l.voiture` — Détection de la voiture · zone autorisée
- partiel · `general/dom.matthew` — Matthew — notre propre modèle · hors zone
- partiel · `general/tool.weather_report` — weather_report · hors zone
- partiel · `general/tool.youtube_video` — youtube_video · hors zone
- partiel · `matthew/h.offline` — Hook : Matthew, cerveau hors-ligne (voix en cascade) · zone autorisée
- partiel · `matthew/m.flywheel` — flywheel.py — la boucle automatique · hors zone
- partiel · `matthew/matthew` — Matthew — notre propre modèle · hors zone
- partiel · `nightshift/nightshift` — nightshift/ — le veilleur de nuit · hors zone
- partiel · `preprod/t.audio` — audio · hors zone
- partiel · `server/l.barge_in` — Interruption à la voix (option) · hors zone
- partiel · `general/tool.reminder` — reminder · rappels · hors zone
- partiel · `general/tool.system_status` — system_status · hors zone
- partiel · `server/m.assist` — assist.py — le cerveau d'Oracle pour les appareils · hors zone
- partiel · `client_desktop/f.plugins_appareil` — Plugins d'appareil (PLUGIN_DEVICE) · hors zone
- partiel · `general/f.nightshift` — Veilleur de nuit (Night Shift) · hors zone
- partiel · `matthew/m.oracle` — oracle.py — le journal collecté sur Oracle rejoint l'entraînement · hors zone
- partiel · `matthew/m.sched` — sched.py — planifier sous Windows comme sous Linux · hors zone
- partiel · `general/f.pilote` — Pilote universel · hors zone
- partiel · `general/f.reunion` — Copilote de réunion · hors zone

### ruff 0.17.0 — 5 remarque(s)

- `matthew/flywheel.py:258` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/selftest.py:1177` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/session.py:64` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/specialist.py:145` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/specialist.py:271` E731 Do not assign a `lambda` expression, use a `def`
