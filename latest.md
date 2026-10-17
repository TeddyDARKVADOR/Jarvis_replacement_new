## JARVIS Night Shift — 2026-10-17 · passe 1/3

**18/20 suites vertes ; 1 régression (extensions « manager 1 examen sans exécution : déclaré, réellem ») ; 1 échec sans référence (daily n°2) ; ruff : 0 remarque ; indicateurs : 2 changements à relire (coût en contexte (server)) ; tâche extensions « manager 1 examen sans exécution : déclaré, réellem » : correctif rejeté (le n°manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empr).**

commit `47a5308` · branche `main` · ubuntu24 20261012.334.1 · Python 3.12.15 · 1 min 14 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/38990394367)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.3 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 2.3 s |
| server.routing_selftest | 35/35 | ✅ vert | 0.8 s |
| client_desktop --selftest | 28/28 | ✅ vert | 0.5 s |
| cognition.selftest | 64/64 | ✅ vert | 4.5 s |
| eval_lab.selftest | 28/28 | ✅ vert | 4.2 s |
| core.code_workspace.selftest | 34/34 | ✅ vert | 34.1 s |
| reminders.selftest | 4/4 | ✅ vert | 0.2 s |
| resilience.selftest | 15/15 | ✅ vert | 0.4 s |
| extensions.selftest | 14/15 | 🔴 RÉGRESSION | 0.8 s |
| offline.selftest | 10/10 | ✅ vert | 1.2 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 34/34 | ✅ vert | 5.3 s |
| meeting.selftest | 21/21 | ✅ vert | 0.8 s |
| pilot.selftest | 13/13 | ✅ vert | 1.6 s |
| quota.selftest | 7/7 | ✅ vert | 0.0 s |
| daily.selftest | 7/8 | ❌ rouge (sans référence) | 15.2 s |
| incidents.selftest | 6/6 | ✅ vert | 0.2 s |

### Tâche de la nuit

- **extensions « manager 1 examen sans exécution : déclaré, réellem »** (régression) — correctif rejeté : le n°manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte échoue encore
- 1 fichier(s) : `extensions/manager.py` · 1 tentative(s) · groq/openai/gpt-oss-120b
- Consommation : 1 appel(s) sur 12 · 4082 tokens sur 60000
- Hors zone (pour un humain) : fail-daily-2-47a5308 — hors de la liste blanche : daily/__init__.py

### Indicateurs — JARVIS s'améliore-t-il ?

- eval_lab legacy : 77/77 réussis
- eval_lab regressions : 9/9 réussis
- Coût en contexte : prompt 3540 car. + outils 29894 car. (41 outils, mode local) ≈ 9552 tokens par connexion
- Cognition : 14 valeurs mesurées (architecture complète)
- **coût en contexte (server)** : 22284 → 28658 (hausse) — +6374 caractères (+28.6%) envoyés à chaque connexion
- **coût en contexte (local)** : 23520 → 29894 (hausse) — +6374 caractères (+27.1%) envoyés à chaque connexion

### Modèle local

- indisponible : aucun avis local : PC éteint, ou worker non installé (phase 5, facultative)

### Backlog du graphe — 16 nœud(s) bug ou partiel

- partiel · `context/l.voiture` — Détection de la voiture · zone autorisée
- partiel · `general/tool.weather_report` — weather_report · hors zone
- partiel · `general/tool.youtube_video` — youtube_video · hors zone
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

### Régressions

- **extensions « manager 1 examen sans exécution : déclaré, réellem »** — manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte · depuis `47a5308` (dernier vert : `0534773`) · commits suspects : `47a5308 Graphe : tool.portable liste ses nouvelles actions (resume, audit, check, history…)`, `90c6b90 Portable : auto-réparation, intégrité, batterie, historique, éjection sûre`, `4d46285 Portable : continuité d'identité + preuve du zéro-trace`, `20f56c9 JARVIS portable : clé USB nomade, zéro-trace, animée, multi-clés`, `5f92715 Sonde incidents : vérifier l'effacement d'un vrai nom de fichier, pas du marqueur`

### Rouge, sans référence

La veille ne permet pas de dire si c'est nouveau (pas de rapport, ou la suite n'avait pas tourné).

- daily n°2 — agenda : récurrence, exception, occurrence déplacée, journée entière, annulé, autre fuseau
