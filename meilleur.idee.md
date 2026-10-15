# JARVIS Night Shift — ce qui est fait, ce qu'il reste à faire

> État au 2026-10-15. Le détail de chaque pièce vit dans le graphe
> `capabilities/graphs/nightshift.json` ; les rapports de chaque nuit sur la
> branche `nightshift-data`. Le plan d'origine (`meilleur.idee.v1.md`) et le
> plan détaillé des phases (version 2 de ce fichier) sont dans l'historique Git.

## Ce que fait la nuit, aujourd'hui

Chaque nuit à 03 h 17 (heure de Paris), GitHub Actions lance les 15 suites du
dépôt sous Linux et ruff, compare avec la dernière nuit mesurée, mesure si
JARVIS s'améliore, choisit au plus une tâche, en demande le correctif à un
modèle gratuit (Groq), le prouve par les suites et — hors dry run — ouvre une
PR. Rien n'est jamais fusionné par une machine. Le matin, JARVIS dit une phrase
au briefing quand il y a du nouveau ; le détail est dans l'onglet NUIT du
panneau ESPRIT. Coût : zéro (dépôt public, quotas gratuits).

## Ce qui est fait

| Phase | État | Preuve |
|---|---|---|
| 0 — Préparation | ✅ faite à la main | secret `GROQ_API_KEY`, variables `GROQ_MODEL` et `NIGHTLY_DRY_RUN`, case « Allow GitHub Actions to create and approve pull requests » |
| 1 — Le veilleur | ✅ validée (6 critères sur 6) | runs 38532442387 (premier vert), 38535049698 (régression simulée bien classée) |
| 2 — Une tâche IA par nuit | ✅ 5 critères sur 6 | PR #1 ouverte par la nuit (run 38565066768), pas de doublon et clé absente des journaux (run 38566336246) |
| 3 — Mémoire et backlog | ✅ réalisée | selftest n°23 à 26, commandes réelles |
| 4 — Mesurer si JARVIS s'améliore | ✅ réalisée | indicateurs mesurés sur le vrai dépôt et dans chaque rapport |
| 5 — PC réveillé, modèles locaux | ✅ réalisée, facultative | selftest n°29 et 30, worker lancé pour de vrai (Ollama arrêté) |
| 6 — Orchestration multi-modèles | ⏸ pas commencée | voir « Plus tard » |

**Phase 1 — le veilleur.** Le paquet `nightshift/` (racine, retirable : il
n'importe rien de JARVIS) et le workflow `.github/workflows/nightly.yml`
(`ubuntu-24.04` épinglé, actions épinglées par SHA). Le rapport
`jarvis.nightly/1` va sur la branche orpheline `nightshift-data`, jamais sur
main ; JARVIS le lit sans jeton. Une régression est classée avec son commit
suspect ; un échec connu ne l'est jamais ; une nuit ratée ne fait pas oublier
la précédente. `pyproject.toml` réduit ruff aux vrais bugs probables (4
remarques). Sous Linux, les 15 suites tournent : 14 vertes, code_workspace à
son échec connu près (n°2).

**Phase 2 — une tâche IA par nuit.** La veille choisit une seule tâche
(régression, puis contrôle rouge, puis remarque ruff « vrai bug »), dans la
zone autorisée — les tests, `nightshift/` et le cœur en sont exclus — et
prépare un contexte de moins de 4 000 tokens. La proposition appelle
`gpt-oss-120b` (secours : qwen, gpt-oss-20b, Gemini) ; la clé ne vit que dans
une étape ; le diff est réancré sur le vrai fichier, 2 tentatives au plus. La
validation relance les 15 suites : validé seulement si la cible passe sans
régression. La publication ouvre une PR, jamais deux, jamais de fusion. Deux
leçons des vrais runs sont corrigées : les en-têtes `@@` sans numéros (le
réancrage) et les chemins ruff relatifs qui faisaient rejeter un bon correctif.
La PR #1, ouverte par la nuit sur un bug planté, restaurait exactement la ligne
retirée ; c'était un essai, fermée sans fusion.

**Phase 3 — mémoire et backlog.** `state.json` sur `nightshift-data` : une
tâche déjà tentée au même commit n'est pas refaite ; une PR fermée n'est
jamais reproposée ; deux échecs donnent un dossier de diagnostic (les faits,
puis les hypothèses, marquées non vérifiées) ; le dimanche, la semaine en
chiffres au briefing. `nightshift/priorities.toml` force ou interdit une tâche
(humain seulement). Le backlog — les 15 nœuds bug et partiel des graphes — est
affiché, jamais confié au modèle : aucun contrôle ne prouverait le correctif.

**Phase 4 — indicateurs.** Dans chaque rapport : eval_lab (76/77 sur le corpus
de référence, 9/9 sur les régressions), l'évaluation déterministe de la
cognition (14 valeurs), le coût en contexte (≈ 7 731 tokens par connexion
Gemini). Un correctif qui en dégrade un est « à relire de près », même si les
suites sont vertes.

**Phase 5 — PC réveillé.** `python -m nightshift.local_worker` : la file
`queue.json` (des avis seulement — résumer les échecs, trier les remarques),
Ollama et `gemma4:12b`, un résultat structuré par tâche, poussé sur
`nightshift-data`, puis la mise en veille. Sans PC : « indisponible », jamais
« en attente ». Inactif tant qu'il n'est pas planifié.

## Ce qu'il reste à faire

1. **Dix nuits en dry run**, le dernier critère de la phase 2 : 1 sur 10.
   La première planifiée sur main (2026-10-15, run 38689556451) : 15/15
   suites vertes, aucune régression, correctif `eval_lab/mutate.py` validé, pas
   de PR (dry run). Si les suivantes se passent sans surprise, passer la variable
   `NIGHTLY_DRY_RUN` à `false`. Les correctifs validés deviendront alors de
   vraies PR vers main, à relire et fusionner soi-même.
2. **À vérifier au fil de ces nuits**, sur main : `state.json` et
   `queue.json` sont écrits depuis le 2026-10-15 ; restent la relecture de
   `state.json` (une tâche déjà tentée n'est pas refaite au même commit), le
   verdict « PR refusée » sur une première vraie PR fermée.
3. **À voir dans le vrai JARVIS** : l'onglet NUIT dans le HUD (son rendu n'a
   été testé que hors HUD) et la phrase du briefing dans une session vocale.
4. **Facultatif** : planifier le worker local sur le PC à RTX 3060 —
   `python -m nightshift.local_worker --install-help` donne les étapes (le
   portable, avec 4 Go de GPU, ne fait pas tenir `gemma4:12b`).
5. **Défauts trouvés par la nuit, hors de sa zone** — corrigés à la main le
   2026-10-15 : `NO_DELIVERY_WITHOUT_SINK` dans `context/policy.py` (une voix
   sans sortie ni client est maintenant différée ; eval_lab legacy 77/77), le
   nettoyage du contrôle n°2 de code_workspace, et les remarques ruff de
   `actions/code_helper.py`, `core/code_workspace/selftest.py` et
   `offline/selftest.py`. Reste `eval_lab/mutate.py:97` (F841), laissé à la nuit.

**Plus tard, peut-être** : la phase 6 (orchestration multi-modèles), seulement
après des semaines de fonctionnement et si un goulot précis la justifie. Ses
règles sont déjà appliquées : résultats structurés, 2 tentatives au plus, un
worker absent déclaré « indisponible ».

## S'en servir

- **Le matin** : le briefing dit une phrase s'il y a du nouveau ; le détail est
  dans l'onglet NUIT ou dans le résumé du dernier run (Actions → nightly). Un
  run rouge, avec un e-mail de GitHub, signale une régression : le rapport
  donne le contrôle cassé et le commit suspect.
- **Une PR Night Shift** : relire la preuve (tableau avant/après) et le diff ;
  fusionner si convaincu, sinon la fermer — elle ne sera plus reproposée.
- **Arrêter** : `NIGHTLY_MAX_TASKS=0` garde le veilleur sans IA ;
  Actions → nightly → « Disable workflow » arrête tout.
- **Garder la planification** : GitHub la désactive après 60 jours sans
  activité du dépôt ; un commit par mois suffit.

| Variable du dépôt | Défaut | Rôle |
|---|---|---|
| `NIGHTLY_DRY_RUN` | `true` | `true` : tout sauf la PR |
| `NIGHTLY_MAX_TASKS` | `1` | tâches IA par nuit ; `0` coupe l'IA |
| `NIGHTLY_MAX_LLM_CALLS` | `12` | appels au modèle par nuit |
| `NIGHTLY_MAX_TOKENS` | `60000` | tokens par nuit (Groq : 200 000 par jour) |
| `NIGHTLY_MAX_RUNTIME_MINUTES` | `45` | durée maximale d'une nuit |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | premier modèle essayé |
