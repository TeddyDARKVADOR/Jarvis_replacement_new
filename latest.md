## JARVIS Night Shift — 2026-10-21

**21/21 suites vertes ; aucune régression ; réparé : matthew « vitrine : liste blanche, aucun texte du journal, g » ; ruff : 0 remarque ; rien à corriger.**

commit `7a6d16f` · branche `main` · ubuntu24 20261018.339.1 · Python 3.12.15 · 1 min 16 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/39584355316)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.5 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 1.8 s |
| server.routing_selftest | 35/35 | ✅ vert | 0.6 s |
| client_desktop --selftest | 28/28 | ✅ vert | 0.6 s |
| cognition.selftest | 64/64 | ✅ vert | 3.5 s |
| eval_lab.selftest | 28/28 | ✅ vert | 4.8 s |
| core.code_workspace.selftest | 34/34 | ✅ vert | 33.7 s |
| reminders.selftest | 4/4 | ✅ vert | 0.2 s |
| resilience.selftest | 15/15 | ✅ vert | 0.5 s |
| extensions.selftest | 15/15 | ✅ vert | 0.7 s |
| offline.selftest | 10/10 | ✅ vert | 1.2 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 36/36 | ✅ vert | 5.8 s |
| meeting.selftest | 21/21 | ✅ vert | 0.9 s |
| pilot.selftest | 13/13 | ✅ vert | 1.7 s |
| quota.selftest | 7/7 | ✅ vert | 0.0 s |
| daily.selftest | 8/8 | ✅ vert | 15.1 s |
| incidents.selftest | 6/6 | ✅ vert | 0.1 s |
| matthew.selftest | 35/35 | ✅ vert | 1.6 s |

### Tâche de la nuit

- rien à corriger
- Consommation : 0 appel(s) sur 12 · 0 tokens sur 60000

### Cette nuit, passe par passe

- Passe 1 : extensions « manager 1 examen sans exécution : déclaré, réellem » — correctif rejeté · 3685 tokens
- Passe 2 : rien de tenté — rien de tenté · 0 tokens
- Passe 3 : rien de tenté — rien de tenté · 0 tokens
- Passe ? : rien de tenté — rien de tenté · 0 tokens
- Passe ? : rien de tenté — rien de tenté · 0 tokens

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
- Coût en contexte : prompt 3540 car. + outils 30893 car. (42 outils, mode local) ≈ 9838 tokens par connexion
- Cognition : 14 valeurs mesurées (architecture complète)

### Modèle local

- indisponible : aucun avis local : PC éteint, ou worker non installé (phase 5, facultative)

### Backlog du graphe — 20 nœud(s) bug ou partiel

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
- partiel · `general/f.pilote` — Pilote universel · hors zone
- partiel · `general/f.reunion` — Copilote de réunion · hors zone
- partiel · `android/m.matrice` — Ce qui arrive sur Android · hors zone
- partiel · `cognition/l.local` — Mode local seulement · hors zone
- partiel · `deployment/l.dependances` — Ce qui dépend d'autre chose que ce PC · hors zone

### Réparé

- matthew « vitrine : liste blanche, aucun texte du journal, g » — vitrine : liste blanche, aucun texte du journal, garde-fou qui refuse chaque fuite : AssertionError:
