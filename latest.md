## Matthew Night Shift — 2026-10-24 · passe 1/3

**20/21 suites vertes ; 1 régression (offline n°3) ; ruff : 5 remarques ; indicateurs : 3 changements à relire (coût en contexte (server)) ; tâche offline n°3 : correctif rejeté (le n°3 échoue encore).**

commit `689bfa6` · branche `main` · ubuntu24 20261023.334.3 · Python 3.12.15 · 1 min 35 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/40050555631)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.6 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 2.0 s |
| server.routing_selftest | 39/39 | ✅ vert | 0.8 s |
| client_desktop --selftest | 28/28 | ✅ vert | 0.7 s |
| cognition.selftest | 65/65 | ✅ vert | 3.6 s |
| eval_lab.selftest | 28/28 | ✅ vert | 7.3 s |
| core.code_workspace.selftest | 35/35 | ✅ vert | 33.8 s |
| reminders.selftest | 4/4 | ✅ vert | 0.3 s |
| resilience.selftest | 15/15 | ✅ vert | 0.7 s |
| extensions.selftest | 15/15 | ✅ vert | 0.8 s |
| offline.selftest | 9/10 | 🔴 RÉGRESSION | 1.3 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 38/38 | ✅ vert | 6.9 s |
| meeting.selftest | 21/21 | ✅ vert | 1.0 s |
| pilot.selftest | 13/13 | ✅ vert | 1.7 s |
| quota.selftest | 7/7 | ✅ vert | 0.1 s |
| daily.selftest | 8/8 | ✅ vert | 15.2 s |
| incidents.selftest | 6/6 | ✅ vert | 0.2 s |
| matthew.selftest | 44/44 | ✅ vert | 15.9 s |

### Tâche de la nuit

- **offline n°3** (régression) — correctif rejeté : le n°3 échoue encore
- 1 fichier(s) : `offline/__init__.py` · 2 tentative(s) · groq/openai/gpt-oss-120b
- Consommation : 2 appel(s) sur 12 · 6939 tokens sur 60000

### Échec persistant — manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte

Idée `check:extensions.selftest:manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte`, 2 tentatives sur le même contexte.

**Faits**
- 2026-10-17 · commit 47a5308 · patch_rejected : le n°manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte échoue encore (groq/openai/gpt-oss-120b)
- 2026-10-21 · commit 7212e01 · patch_rejected : le n°manager 1 examen sans exécution : déclaré, réellement fait, écarts, dépendances, empreinte échoue encore (groq/openai/gpt-oss-120b)

**Hypothèses** (non vérifiées)
- le correctif ne touche pas la cause

### Indicateurs — Matthew s'améliore-t-il ?

- eval_lab legacy : 77/77 réussis
- eval_lab regressions : 9/9 réussis
- Coût en contexte : prompt 3563 car. + outils 30915 car. (42 outils, mode local) ≈ 9850 tokens par connexion
- Cognition : 14 valeurs mesurées (architecture complète)
- **coût en contexte (server)** : 29658 → 29679 (hausse) — +21 caractères (+0.1%) envoyés à chaque connexion
- **coût en contexte (local)** : 30894 → 30915 (hausse) — +21 caractères (+0.1%) envoyés à chaque connexion
- **prompt système** : 3552 → 3563 (hausse) — +11 caractères

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

### Régressions

- **offline n°3** — nom : n'importe où dans la phrase, accents et casse ignorés · depuis `689bfa6` (dernier vert : `763c975`) · commits suspects : `689bfa6 Renommage : JARVIS devient Matthew (textes) et matthew_assistant (code)`, `b9b6cec Kaggle : tournoi de réglages sur 2 GPU dans le budget de la nuit, duel rattrapé au réveil du PC`, `28cf39d Kaggle : le noyau attend que le jeu de données neuf soit prêt et vérifie qu'il lui est rattaché`, `bad567f Kaggle : nom d'utilisateur nettoyé (retour à la ligne du secret), premier lancement réel consigné`, `019d049 Matthew : part minimale de 40 % d'exemples sans outil, 2 époques`

### ruff 0.17.0 — 5 remarque(s)

- `matthew/flywheel.py:259` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/selftest.py:1189` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/session.py:64` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/specialist.py:145` E731 Do not assign a `lambda` expression, use a `def`
- `matthew/specialist.py:271` E731 Do not assign a `lambda` expression, use a `def`
