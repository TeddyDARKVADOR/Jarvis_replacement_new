## JARVIS Night Shift — 2026-10-14

**14/14 suites vertes (1 ignorée) ; aucune régression ; ruff : 4 remarques.**

commit `85114b2` · branche `main` · ubuntu24 20261004.327.1 · Python 3.12.15 · 0 min 54 s · [exécution](https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/38532442387)

| Suite | Score | État | Durée |
|---|---:|---|---:|
| capabilities.selftest | 11/11 | ✅ vert | 0.5 s |
| context.selftest | 33/33 | ✅ vert | 0.1 s |
| server.selftest | 35/35 | ✅ vert | 2.0 s |
| server.routing_selftest | 35/35 | ✅ vert | 0.7 s |
| client_desktop --selftest | — | ⏭️ ignorée — dépendance absente : ImportError: libEGL.so.1: cannot open shared object file: No such file or directory | 0.0 s |
| cognition.selftest | 64/64 | ✅ vert | 3.5 s |
| eval_lab.selftest | 28/28 | ✅ vert | 6.5 s |
| core.code_workspace.selftest | 33/34 | ☑️ échec connu (n°2) | 33.7 s |
| reminders.selftest | 4/4 | ✅ vert | 0.3 s |
| resilience.selftest | 13/13 | ✅ vert | 0.6 s |
| extensions.selftest | 11/11 | ✅ vert | 0.7 s |
| offline.selftest | 10/10 | ✅ vert | 1.3 s |
| core.token_usage_selftest | 1/1 | ✅ vert | 0.0 s |
| actions.repository_review_selftest | 1/1 | ✅ vert | 0.0 s |
| nightshift.selftest | 14/14 | ✅ vert | 3.3 s |

### ruff 0.17.0 — 4 remarque(s)

- `actions/code_helper.py:481` F841 Local variable `image_base64` is assigned to but never used
- `core/code_workspace/selftest.py:761` F811 Redefinition of unused `webcheck` from line 57: `webcheck` redefined here
- `eval_lab/mutate.py:97` F841 Local variable `stim` is assigned to but never used
- `offline/selftest.py:217` F841 Local variable `again` is assigned to but never used

### Limites

- client_desktop --selftest non testée sur linux : dépendance absente : ImportError: libEGL.so.1: cannot open shared object file: No such file or directory
- code_workspace n°2 : échec connu sur linux
- pas de rapport précédent : aucune régression ne peut être établie cette nuit
