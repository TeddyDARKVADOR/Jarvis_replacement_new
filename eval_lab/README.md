# `eval_lab/` — EVAL LAB V2

Un paquet racine au sens de [`docs/V2_CONTRACT.md`](../docs/V2_CONTRACT.md) §3 :
il importe `context/`, `server/`, `presence/` et `avatar/` pour les exercer, et
**rien ne l'importe**. Supprimer ce dossier laisse JARVIS identique ;
`python main.py` n'en charge pas une ligne. Vérifié par le selftest et par le
test de retrait.

La seule retouche hors de ce dossier : `avatar/checks/scenario_matrix.mjs`
exporte `runCase`, `violations` et `MATRIX` (sortie en ligne de commande
identique, vérifiée par diff), pour que le labo juge le visage avec **les mêmes
invariants** au lieu d'une copie.

## Le cycle

```bash
python -m eval_lab.selftest                        # le labo lui-même, 26 checks (~20 s)

python -m eval_lab import-legacy                   # tests existants -> corpus/legacy (1 709)
python -m eval_lab run --corpus legacy             # ancien PASS <=> nouveau PASS

python -m eval_lab generate --strategy all         # A B D E F sans LLM -> corpus/generated
python -m eval_lab fuzz --count 20000              # G : mutation guidée par la couverture + relations
python -m eval_lab scale --count 100000 --procs 8  # un barreau de l'échelle, mesuré
python -m eval_lab run --corpus generated --mode full   # FULL : le vrai routeur, sur ses threads

python -m eval_lab triage --run NOM                # échecs -> clusters -> classes -> reproducteurs minimaux
python -m eval_lab report --run NOM                # runs/NOM/report.md
python -m eval_lab regressions promote --run NOM --cluster C001 --title "..." --component "..."
python -m eval_lab regressions check               # open : échoue encore ? fixed : tient toujours ?

pip install -r eval_lab/requirements-llm.txt       # seulement pour ce qui suit
python -m eval_lab llm --mode adversary --batches 5 --max-usd 2   # Opus 5.5, cassette enregistrée
python -m eval_lab llm --replay --run llm          # rejoue la cassette : mêmes scénarios, 0 $
python -m eval_lab grade --run full1               # grader LLM des phrases, avis seulement

python -m eval_lab real list                       # la petite suite sur vrai matériel
python -m eval_lab real record --id ID --verdict PASS --note "..."
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `scenario.py` | le format canonique, l'id (hash du contenu), la validation, les opérateurs d'oracle |
| `world.py` | l'horloge simulée, l'isolation, la porte unique vers JARVIS (`sut()`) |
| `surfaces.py` | scénario -> vrai code -> trace : `situation`, `policy`, `sequence`, `routing` |
| `full.py` | surface `router` (FULL) : `ActionRouter.run()` depuis un thread, boucle réelle |
| `face.py` + `js/face_runner.mjs` | surface `face` : le moteur sous Node, jugé par `scenario_matrix.mjs` |
| `properties.py` | invariants *hard* / *soft*, chacun cite la promesse du projet |
| `space.py` | les dimensions, et les seuils **lus dans le code** |
| `generate.py` | stratégies A pairwise, B frontières, D séquences, E adversarial, F rares, `router` |
| `mutate.py` | C mutation à une variable, relations métamorphiques, recherche de frontière |
| `coverage.py` | traits, paires, cellules, transitions, nouveauté ; G fuzzing guidé |
| `runner.py` | verdicts, reprise, tranches, déduplication, mode compact |
| `triage.py` | signatures, clusters, classification, minimisation |
| `regressions.py` | corpus de régression, manifeste en ajout seul |
| `report.py` | le rapport humain |
| `llm.py` | Opus 5.5 : génération et adversaire, cassette, budget |
| `grader.py` | grader LLM, isolé, jamais un verdict |
| `real.py` | le niveau REAL, exécuté par une personne |
| `legacy.py` | l'import des tests existants, et ce qu'il laisse volontairement |

## Un scénario

| Champ | Rôle |
|---|---|
| `surface` | la couche visée |
| `tier` | `fast`, `full` ou `real` |
| `world` | ce qui est vrai avant : heure, téléphone (`age_s`), appareils, tour de parole, seuils, réglages, livraisons passées |
| `stimulus` / `events` | ce qui arrive : une priorité, une commande, une histoire |
| `expected` / `forbidden` | l'oracle, en **chemins** de trace (`{"steps.2.lost": {"$len": 0}}`) |
| `source`, `family`, `seed`, `lineage` | provenance : parent, mutations, stratégie, test d'origine, auteur de l'oracle |

L'`id` ne dépend que de `surface`, `world`, `stimulus`, `events` : deux
générateurs qui trouvent la même situation produisent le même scénario.

## À qui appartient un résultat

| Verdict | Signification | Problème de |
|---|---|---|
| `PASS` | oracle et propriétés *hard* tenus (avertissements *soft* possibles) | — |
| `FAIL` | oracle, propriété *hard* ou relation *hard* contredits | **candidat** |
| `CRASH` | le code de JARVIS a levé | JARVIS, jusqu'à preuve du contraire |
| `INVALID` | scénario mal formé ou monde impossible | générateur |
| `INFRA` | le labo a cassé | labo |

Puis le triage classe chaque cluster : `jarvis-crash`, `jarvis-candidate`
(une promesse écrite du projet est rompue), `oracle-disputed` (seul un oracle
écrit par le labo ou par le LLM n'est pas d'accord — une personne tranche),
`legacy-regression`, `unstable`, `known`, `lab`, `generator`. **Une divergence
ne devient jamais un bug toute seule** : la promotion en régression est une
commande qu'une personne tape.

## Opus 5.5 : ce qu'il fait, ce qu'il ne fait pas

Il propose des situations et des hypothèses de bug. Ses sorties passent par les
mêmes validateurs que tout le reste ; ce qu'il affirme sur le bon comportement
devient un oracle *d'auteur LLM*, classé `oracle-disputed` en cas de
désaccord. La frontière (une variable qui change le résultat) et le minimum
sont calculés par le labo, pas demandés au modèle. Chaque réponse est
enregistrée dans une cassette : un run LLM se rejoue à l'identique, hors ligne.

## L'échelle, mesurée (sans LLM, 12 cœurs)

| | 1k | 10k | 100k |
|---|---|---|---|
| scénarios uniques | 1 046 | 9 858 | 89 265 |
| exécution | 0,4 s | 1,6 s | 24,5 s (8 processus) |
| mémoire / processus | 30 Mo | 31 Mo | 31 Mo |
| résultats (compact) | 0,2 Mo | 2,4 Mo | 21 Mo |
| traits de couverture | 1 215 | 1 240 | 1 242 |
| clusters / nouveaux candidats | 3 / 1 | 3 / 1 | 3 / 1 |

Lecture : l'architecture tient (mémoire plate, débit linéaire, tranches
indépendantes, reprise). Mais la couverture plafonne dès 10k : sur les
dimensions actuelles, un million d'essais n'apprendrait rien de plus. La suite
n'est pas plus d'essais, c'est plus d'espace — phrases imaginées par Opus,
nouvelles surfaces, niveau FULL.
