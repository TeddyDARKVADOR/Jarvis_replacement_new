## JARVIS Night Shift — 2026-10-14

**14/15 suites vertes ; aucune régression ; ruff : 4 remarques.**

commit `dd5f268` · branche `main` · ubuntu24 20261004.327.1 · Python 3.12.15 · 0 min 55 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/38535001267)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.5 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 1.9 s |
| server.routing_selftest | 35/35 | ✅ vert | 0.7 s |
| client_desktop --selftest | 27/28 | ❌ rouge (déjà) | 0.7 s |
| cognition.selftest | 64/64 | ✅ vert | 3.6 s |
| eval_lab.selftest | 28/28 | ✅ vert | 6.7 s |
| core.code_workspace.selftest | 33/34 | ☑️ échec connu (n°2) | 33.7 s |
| reminders.selftest | 4/4 | ✅ vert | 0.3 s |
| resilience.selftest | 13/13 | ✅ vert | 0.7 s |
| extensions.selftest | 11/11 | ✅ vert | 0.8 s |
| offline.selftest | 10/10 | ✅ vert | 1.3 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 14/14 | ✅ vert | 3.3 s |

### Déjà rouge

- client_desktop n°25 — every one of the 26 HUD members the two hosts use exists

### ruff 0.17.0 — 4 remarque(s)

- `actions/code_helper.py:481` F841 Local variable `image_base64` is assigned to but never used
- `core/code_workspace/selftest.py:761` F811 Redefinition of unused `webcheck` from line 57: `webcheck` redefined here
- `eval_lab/mutate.py:97` F841 Local variable `stim` is assigned to but never used
- `offline/selftest.py:217` F841 Local variable `again` is assigned to but never used

### Limites

- code_workspace n°2 : échec connu sur linux
