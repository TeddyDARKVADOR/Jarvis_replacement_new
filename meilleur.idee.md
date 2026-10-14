# JARVIS Night Shift — le plan de travail

> Version 2, réécrite le 2026-10-13 à partir de l'analyse du dépôt réel.
> La version d'origine est conservée dans `meilleur.idee.v1.md`.
>
> **Comment s'en servir.** Une phase par session de travail, dans l'ordre. Pour
> lancer une phase avec un assistant IA (Claude Code, Aider…), donne-lui le
> prompt de la section 12 en remplaçant le numéro de phase. Chaque phase se
> termine par des critères d'acceptation vérifiables : tant qu'ils ne sont pas
> tous verts, la phase n'est pas finie.
>
> **Avancement au 2026-10-14** — le détail vit dans le graphe
> `capabilities/graphs/nightshift.json`, les preuves sous chaque phase.
>
> | Phase | État |
> |---|---|
> | 0 — Préparation | ✅ faite à la main |
> | 1 — Le veilleur | ✅ validée : six critères sur six, prouvés sur GitHub |
> | 2 — Une tâche IA par nuit | ✅ réalisée, en dry run : 2 critères prouvés, 4 attendent tes tests GitHub |
> | 3 — Mémoire et backlog | ✅ réalisée : 3 critères vérifiés par les tests, à confirmer sur une vraie PR |
> | 4 — Mesurer si JARVIS s'améliore | ✅ réalisée : indicateurs mesurés sur le vrai dépôt |
> | 5 — PC réveillé, modèles locaux | ✅ réalisée, facultative : inactive tant qu'elle n'est pas planifiée sur le PC |
> | 6 — Orchestration multi-modèles | ⏸ pas commencée : le plan la réserve à plus tard (voir sa section) |
>
> **À tester à la fin** : (1) Actions → nightly → Run workflow sur `main`
> (dry_run auto) — premier vrai appel à Groq ; relire le journal de l'étape
> « Appel au modèle » : la clé ne doit y apparaître nulle part ; (2) sur
> `test/bug-plante` avec dry_run = false — une PR doit s'ouvrir vers cette
> branche ; (3) le relancer — « PR déjà ouverte », pas de doublon ; (4) après
> dix nuits sans surprise, `NIGHTLY_DRY_RUN=false` ; (5) facultatif : sur le PC
> à RTX 3060, `python -m nightshift.local_worker --install-help`.

---

## 1. L'objectif, en une phrase

Chaque matin, JARVIS me dit ce qui s'est passé dans son code pendant la nuit :
ce qui a été vérifié, ce qui a régressé, et — quand c'est possible — une seule
Pull Request qui corrige un vrai problème, prouvée par les tests. Moi, je
relis et je décide.

### Principes non négociables

1. **Gratuit.** Aucun service payant tant que je n'en décide pas autrement.
   Additionner des quotas gratuits plutôt que dépendre d'un seul.
2. **L'humain fusionne.** Rien n'est jamais écrit sur `main` par une machine.
   Aucune fusion automatique.
3. **Des preuves, pas des avis.** Un correctif n'est « réussi » que si les
   suites du projet le prouvent. Une hypothèse reste marquée comme hypothèse.
4. **Le temps de relecture est la vraie ressource rare.** Au plus une PR
   ouverte à la fois. Pas de PR de style, pas de refactorisation gratuite.
5. **Le dépôt a ses règles, la Night Shift aussi** : contrat V2, `actions/`
   figé, graphes à jour, Windows et Linux (section 2.3).
6. **Commencer petit.** Chaque phase doit être utile seule. Si une phase ne
   rapporte rien après deux semaines, on s'arrête là.

---

## 2. L'état réel du projet (analyse du 2026-10-13)

### 2.1 Architecture

~51 600 lignes de Python, dépôt **public**
`TeddyDARKVADOR/Jarvis_replacement_new`, branche par défaut `main`, aucune CI
(`.github/` n'existe pas), Python 3.11+ (le code utilise `BaseExceptionGroup`).

| Dossier | Lignes | Rôle |
|---|---|---|
| `main.py` | 2 156 | `JarvisLive` : session Gemini Live (voix native), outils, briefing, reconnexion. Cœur protégé. |
| `actions/` | 10 100 | 18 outils historiques (`TOOL` + handler). **Figé** : les nouveautés vont dans `plugins/`. |
| `core/` | 6 773 | Chargeurs d'actions et de plugins, `confirm` (confirmation par le HUD), `undo`, audio, mot d'éveil, **Code Studio** (`code_workspace/`). |
| `cognition/` | 6 291 | Mémoire avec provenance, affect, objectifs approuvés, modèle de soi, autonomie bornée, chaîne de fournisseurs gratuits (`providers.py`), onglet USAGE. |
| `client_desktop/` | 6 157 | HUD PyQt6 + page web (`hud/web/js/*.js`), relié à Oracle ou en local. |
| `eval_lab/` | 5 763 | Laboratoire de scénarios générés, mutés, jugés contre le vrai code de contexte et de routage. |
| `server/` | 5 244 | Le même JarvisLive, sans écran, sur la VM Oracle ; routage des outils vers le bon appareil. |
| `context/` | 1 696 | Contexte de l'utilisateur, politique de prise de parole. |
| `offline/` | 1 152 | Relais hors-ligne : Whisper + Ollama + voix système quand Gemini ou Oracle tombe. |
| `capabilities/` | 933 | Les graphes `jarvis.graph/1` (la documentation) + `verify.py` (sondes réelles des outils). |
| `dashboard/`, `memory/`, `plugins/` | ~2 300 | Serveur HTTP local, mémoire V1 et config, 9 plugins. |
| `extensions/`, `resilience/`, `reminders/` | ~1 700 | MCP + skills, modèles de secours Gemini, rappels. |
| `jarvis-preprod/`, `client-android/` | — | Banc Android sur émulateur, client Android. |

### 2.2 Ce que le dépôt fournit déjà à la Night Shift

C'est ce qui rend le projet réaliste pour un développeur seul : **la moitié de
l'infrastructure existe**.

**Un juge déjà écrit : 306 contrôles en 12 suites**, chacune lancée par
`python -m <suite>`, sans clé ni réseau (sauf mention) :

| Suite | Contrôles | Sous Linux — mesuré le 2026-10-14 (runner GitHub `ubuntu-24.04`, Python 3.12.15) |
|---|---|---|
| `capabilities.selftest` | 11 | **oui** : 11/11 |
| `context.selftest` | 33 | **oui** : 33/33 |
| `server.selftest` | 35 | **oui** : 35/35 |
| `server.routing_selftest` | 35 | **oui** : 35/35 |
| `client_desktop --selftest` | 28 | **oui, avec les bibliothèques de Qt** (17 paquets, mesurés avec `ldd`) : 28/28 au run 38560540611, une fois réparé le n°25 qu'un commentaire de la route NUIT avait cassé |
| `cognition.selftest` | 64 | **oui** : 64/64 |
| `eval_lab.selftest` | 28 | **oui** : 28/28 |
| `core.code_workspace.selftest` | 34 | **33/34** : seul le n°2 échoue, à cause du nettoyage du test (`os.rmdir` sur un lien symbolique), pas de la sandbox ; le n°33 passe sans Pyright |
| `reminders.selftest` | 4 | **oui** : 4/4 |
| `resilience.selftest` | 13 | **oui** : 13/13 |
| `extensions.selftest` | 11 | **oui** : 11/11 |
| `offline.selftest` | 10 (+4 avec `--real`) | **oui** : 10/10 |

S'y ajoutent `core.token_usage_selftest`, `actions.repository_review_selftest`
et `nightshift.selftest` (14 contrôles) : 15 suites en tout, la liste de
`nightshift/suites.py`.

Plus `python -m capabilities.verify --quick` (sondes réelles des 35 outils :
19 réelles, 11 « à vide »), qui demande des clés pour certaines sondes.

**Attention, règle cachée importante :** `server`, `client_desktop` et
`eval_lab` vérifient avec `git status` que `main.py`, `core/`, `actions/`,
`plugins/`, `dashboard/`, `memory/` et `requirements.txt` n'ont **aucune
modification non commitée**. Un correctif appliqué puis testé sans commit les
fait échouer. Le job de validation doit donc **commiter le patch localement**
(dans le runner, jamais poussé) avant de lancer les suites.

**Une liste de tâches déjà tenue : les graphes.** Chaque nœud a un état
vérifié. Aujourd'hui : 200 `ok`, 26 `a_tester`, 12 `partiel`, 39 non évalués,
0 `bug`. `capabilities` sait déjà les lister (`action=issues`). C'est le
backlog de la phase 3, avec fichiers et explications.

**Une chaîne de fournisseurs gratuits déjà écrite :** `cognition/providers.py`
(`ProviderChain.complete(prompt, system=…, max_tokens=…, prefer=…)`) — Ollama
local → Groq → Cerebras → Gemini → OpenRouter → Mistral, avec pauses sur
quota et sur clé refusée. Elle lit `config/api_keys.json` ; en CI il faudra un
petit adaptateur qui la construit depuis les variables d'environnement
(`ProviderChain.from_config(cfg)` accepte un dict).

**Des idées déjà éprouvées dans Code Studio** (`core/code_workspace/`) :
journal écrit par les outils et jamais par le modèle, verdict prouvé par un
test qui porte le nom du critère, checkpoints, `diagnose.py` (trace d'erreur →
fichier/ligne, détection de boucle). Code Studio **refuse par conception** de
travailler sur le dépôt JARVIS : la Night Shift est un paquet séparé, qui peut
réutiliser ces idées et du code de `diagnose.py`.

**Le point d'arrivée du rapport existe :**
- le briefing du matin (`JarvisLive._send_startup_briefing`, main.py) injecte
  déjà « hier, on a parlé de… » via `pop_last_session()` ;
- le panneau ESPRIT du HUD a des onglets alimentés par `cognition/hud.py`
  (`cognition.usage`, `cognition.overview`…) et `client_desktop/hud/web/js/mind.js`.

### 2.3 Les règles du dépôt que la Night Shift doit respecter

- **Contrat V2** (graphe `contrat`) : une fonctionnalité se retire sans casser
  V1. Ordre : plugin → Android seul → paquet racine avec hook d'une ligne →
  cœur en dernier recours. La Night Shift sera un **paquet racine
  `nightshift/`** + un workflow ; ses hooks dans main.py/HUD tiennent en une
  ligne chacun.
- **La documentation, ce sont les graphes** (`CLAUDE.md`) : pas de README ni de
  nouveau `.md` de doc. La Night Shift aura son sous-graphe
  `capabilities/graphs/nightshift.json`, mis à jour à chaque phase, et
  `python -m capabilities.selftest` doit passer.
- **Windows et Linux** : tout le code de `nightshift/` est multiplateforme ; le
  workflow tourne sous Linux (et Windows en option, section 8).
- **Dépôt public** : les PR, les artefacts et les rapports sont **visibles par
  tous**. Aucun secret, aucune donnée personnelle (mémoire, `memory/*.db`,
  `config/api_keys.json`, conversations) ne doit jamais entrer dans un prompt,
  un journal ou un rapport. Ces fichiers sont ignorés par Git : ils ne seront
  pas dans le runner, et la Night Shift doit refuser de les lire s'ils l'étaient.

### 2.4 Les ressources gratuites, mesurées

| Ressource | Ce qu'on a vraiment | Conséquence |
|---|---|---|
| GitHub Actions | Dépôt public → minutes gratuites sur les runners standards, Linux **et Windows** | Le calcul distant ne coûte rien. |
| Planification | `schedule` accepte `timezone: "Europe/Paris"` ; retards possibles en début d'heure ; **désactivée après 60 jours sans activité** sur un dépôt public | 03 h 17 Paris ; un commit par mois suffit à la garder active. |
| Groq gratuit | Clé valide (renouvelée le 2026-10-13). Modèles utiles : `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` (contexte 131 k). **Mesuré : 1 000 requêtes/jour et 8 000 tokens/minute par modèle** ; la doc indique 200 k tokens/jour | **8 000 tokens/minute est la contrainte qui dimensionne tout** : un appel ≤ ~6 000 tokens (prompt + réponse). Aider avec sa carte du dépôt dépasse facilement : à mesurer avant de l'adopter. |
| Gemini gratuit | Clé valide, mais quota texte épuisé pendant les tests du 2026-10-13 (429) | Secours seulement ; `resilience/` gère la bascule. |
| Ollama local | RTX 3060 12 Go. `gemma4:12b` (7,6 Go, 1,2–1,8 s/réponse) tient en VRAM ; `qwen3.6` (23 Go) et `qwen3:30b` (18 Go) non (41,6 s) ; `qwen3:4b-instruct` absent | Bon pour trier et classer, **pas pour écrire des correctifs multi-fichiers**. Seulement si le PC est réveillé (phase 5). |
| VM Oracle | En panne (inactive trop longtemps) | Hors du plan. Le relais hors-ligne couvre déjà le HUD sans Oracle. |
| WSL2 sur ce PC | Plante au démarrage (`Wsl/Service/CreateInstance/E_FAIL`) depuis le 2026-10-13 | Les tests Linux se font sur le runner GitHub (phase 1, fait). |
| Portable sous Fedora 44 (2026-10-14) | Pas de Windows, donc pas de WSL ; un conteneur toolbox Ubuntu 24.04 (`toolbox run -c ubuntu-toolbox-24.04 …`) reproduit le runner ; GPU de 4 Go | Les suites Linux se lancent aussi ici, comme sur le runner. Pour la phase 5, ce PC ne peut pas faire tourner `gemma4:12b`. |

### 2.5 Le point de départ

- **Analyse statique** (`ruff 0.15`) : 194 remarques, presque toutes de style
  (106 E701, 28 E402…) ; les 3 « noms indéfinis » (F821 `BaseExceptionGroup`)
  sont de fausses alertes faute de `target-version = "py311"`. **Il n'y a
  presque pas de « bugs gratuits » à trouver par l'analyse statique** : les
  vraies tâches viendront des tests, des régressions et des graphes.
  *Mis à jour le 2026-10-14 :* ruff 0.17 a changé ses règles par défaut (1 277
  remarques sans configuration). `pyproject.toml` fixe une liste explicite et
  le workflow épingle `ruff==0.17.0` : **4 remarques, toutes de vrais suspects**
  (3 F841, 1 F811), dont 2 dans la zone autorisée de la phase 2
  (`eval_lab/mutate.py:97`, `offline/selftest.py:217`).
- **Tests** : tous verts sous Windows le 2026-10-13 après le commit `e302e07`.
  L'état sous Linux est à établir (phase 1, premier livrable).

---

## 3. L'architecture cible (fin de la phase 3)

```
03:17 Europe/Paris — GitHub Actions (dépôt public, gratuit)
│
├─ Job 1 « veille »      lecture seule, aucun secret
│    suites testables sous Linux + ruff + capabilities.selftest
│    → baseline.json (artefact)
│
├─ Job 2 « proposition » lecture seule ; secret GROQ seulement à l'étape d'appel
│    choisit UNE tâche (régression > test rouge > nœud bug/partiel)
│    contexte ciblé (ast + ripgrep, ≤ 6 000 tokens) → chaîne gratuite
│    → patch.diff + task.json (artefacts). N'exécute RIEN de ce que dit le modèle.
│
├─ Job 3 « validation »  aucun secret, aucun jeton d'écriture
│    checkout propre du même commit → filtre des chemins → git apply → commit
│    local → mêmes suites → comparaison avec baseline.json → validation.json
│
├─ Job 4 « publication » contents/pull-requests: write, aucun secret Groq
│    seulement si validation OK et aucune PR Night Shift déjà ouverte
│    → branche automation/jarvis-nightly-AAAA-MM-JJ + PR
│
└─ Job 5 « rapport »     toujours exécuté (if: always())
     report.json (jarvis.nightly/1) + report.md + GITHUB_STEP_SUMMARY
     → poussé sur la branche orpheline `nightshift-data` (jamais main)

Le matin — JARVIS lit raw.githubusercontent.com/…/nightshift-data/latest.json
(dépôt public : aucun jeton côté PC) → une phrase au briefing + onglet NUIT.
```

Pourquoi une branche `nightshift-data` plutôt que les artefacts : télécharger un
artefact par l'API GitHub exige une authentification, même sur un dépôt
public ; un fichier sur une branche se lit sans jeton. La branche est
orpheline, ne contient que des rapports, et sert aussi de mémoire (phase 3).

---

## 4. Les phases

### Phase 0 — Préparation (30 min, à la main)

1. **Secret** : GitHub → Settings → Secrets and variables → Actions →
   `GROQ_API_KEY` (la clé renouvelée). Optionnel : `GEMINI_API_KEY`.
2. **Variables de dépôt** : `GROQ_MODEL=openai/gpt-oss-120b`,
   `NIGHTLY_DRY_RUN=true` (on passera à `false` en phase 2).
3. **Permissions** : Settings → Actions → General → « Workflow permissions » :
   *Read repository contents* par défaut ; cocher **« Allow GitHub Actions to
   create and approve pull requests »** (nécessaire en phase 2 seulement).
4. **WSL** : redémarrer le PC ; si `wsl -d Ubuntu -- echo ok` échoue encore,
   `wsl --update` dans un terminal administrateur. (Confort seulement : la
   phase 1 n'en dépend pas.) *Sans objet sur le portable Fedora :*
   `toolbox run -c ubuntu-toolbox-24.04 echo ok` en tient lieu (2026-10-14).
5. **Oracle** (facultatif) : regarder dans la console si l'instance est arrêtée
   (redémarrable) ou supprimée. Rien dans ce plan n'en dépend.

**Fini quand** : le secret et les deux variables existent ; la case PR est cochée.

**État au 2026-10-14** : pas encore faite. La phase 1 n'en a eu besoin pour
rien (aucun secret, aucune variable) ; la phase 2 en dépend.

### Phase 1 — Le veilleur, sans IA (un week-end, 0 token) — ✅ validée le 2026-10-14

**But** : chaque nuit, savoir si quelque chose a cassé, et l'entendre au réveil.
C'est la phase qui rapporte le plus pour le moins d'effort.

**Livrables**

1. `pyproject.toml` minimal : section `[tool.ruff]` avec
   `target-version = "py311"` (supprime les 3 fausses alertes) et les règles
   de style bruyantes ignorées (E701, E702, E402, E741) — ruff ne doit
   remonter que ce qui peut être un vrai bug (F821, F811, F841, E9…).
2. `nightshift/` (paquet racine, multiplateforme, sans dépendance nouvelle) :
   - `suites.py` — la liste des suites, avec pour chacune : commande,
     plateformes où elle a un sens, contrôles connus pour échouer sous Linux
     (code_workspace n°2 et n°33) ;
   - `run.py` — lance les suites avec un délai par suite, capture la sortie,
     extrait `N/M` et les lignes FAIL/ÉCHEC, ne plante jamais ;
   - `report.py` — produit `report.json` (schéma section 7), `report.md`, et
     écrit dans `$GITHUB_STEP_SUMMARY` quand il existe ;
   - `compare.py` — compare avec le rapport de la nuit précédente :
     **régression** (vert hier, rouge aujourd'hui, avec le commit suspect),
     **préexistant**, **réparé** ;
   - `fetch.py` — côté PC : lit `latest.json` sur `nightshift-data`, sans
     jeton, avec cache et délai court ; renvoie `None` hors-ligne ;
   - `selftest.py` — sans réseau (rapports simulés : tout vert, régression,
     suite qui plante, suite qui dépasse son délai, rapport précédent absent).
3. `.github/workflows/nightly.yml` :
   - `on: schedule` (`cron: '17 3 * * *'`, `timezone: "Europe/Paris"`) +
     `workflow_dispatch` ;
   - `permissions: contents: read` au niveau du workflow ;
   - `concurrency: { group: nightshift, cancel-in-progress: false }` ;
   - `timeout-minutes` par job ; actions tierces épinglées par SHA ;
   - job « veille » (ubuntu-latest, Python 3.12, `deployment/requirements-server.txt`
     + `pyyaml ruff`, `QT_QPA_PLATFORM=offscreen` si PyQt6 est testé) ;
   - job « rapport » (`if: always()`, seul job avec `contents: write`) : pousse
     `runs/<date>-<run_id>.json` et `latest.json` sur `nightshift-data`.
4. Côté JARVIS (hooks d'une ligne, contrat V2) :
   - **briefing** : si un rapport de la nuit existe et n'a pas encore été
     annoncé, une phrase de plus dans la phase 1 du briefing (« Cette nuit,
     11 suites sur 12 vertes ; cognition n°21 a régressé depuis le commit
     abc123 »). Rien si tout est vert depuis plusieurs nuits ;
   - **onglet NUIT** dans ESPRIT (ou une section de l'onglet USAGE) : dernière
     exécution, suites, régressions, lien vers l'exécution GitHub ;
   - un outil n'est pas nécessaire en phase 1 ; `capabilities` peut lister le
     dernier rapport si on le souhaite.
5. Graphe : `capabilities/graphs/nightshift.json` + nœud dans `general.json`
   (domaine `dom.tests`), `updated` à la date du jour.

**Critères d'acceptation**
- [x] `workflow_dispatch` lancé à la main : le run passe au vert, `latest.json`
      apparaît sur `nightshift-data`, le résumé s'affiche dans l'onglet Actions.
      *Preuve : run 38532442387 (main, `85114b2`), vert en 54 s ; la branche
      contient `latest.json`, `latest.md`, `baseline.json` et `runs/` ; le PC
      relit le rapport sans jeton (ETag, puis 304).*
- [x] **Premier vrai livrable : la liste des suites qui passent sous Linux**,
      inscrite dans `nightshift/suites.py` et dans le graphe.
      *Preuve : tableau de la section 2.2 ; 14 suites vertes sur 15 au run
      38535001267, code_workspace n°2 compris comme échec connu.*
- [x] Une régression simulée (commit qui casse un contrôle, sur une branche de
      test lancée à la main) est classée « régression » avec le bon commit.
      *Preuve : run 38535049698 (branche `test/regression-simulee`, supprimée
      ensuite) : run rouge, « reminders n°1 » depuis `17e0f85`, dernier vert
      `dd5f268`, ce commit seul suspect ; seul `runs/` a été écrit.*
- [x] Le briefing annonce le rapport une fois, pas deux.
      *Preuve : la vraie `JarvisLive._send_startup_briefing` exécutée deux fois
      (deux processus, session factice, sans Gemini) : la phrase « 13/15 suites
      vertes ; régression : reminders n°1 depuis le commit 17e0f85 » part dans
      la phase 1 du briefing au premier démarrage, rien au second.*
- [x] `python -m nightshift.selftest` et `python -m capabilities.selftest` passent.
      *Preuve : 14/14 et 11/11, en local et sur le runner.*
- [x] Aucun secret dans le workflow de phase 1.
      *Preuve : aucun `secrets.` dans `nightly.yml` (nightshift.selftest n°12).*

**Réalisé — ce qui diffère du plan**
- `runs-on: ubuntu-24.04` et non `ubuntu-latest`, qui passe à Ubuntu 26.04
  entre le 19 octobre et le 19 novembre 2026 : un changement d'image
  passerait pour des régressions.
- ruff : liste de règles explicite et version épinglée (voir 2.5).
- 15 suites et non 12 (voir 2.2).
- La référence de comparaison est `baseline.json` sur `nightshift-data`
  (dernière nuit mesurée) ; `latest.json` et `baseline.json` ne viennent que
  de `main`, un essai sur une autre branche ne laisse sa trace que dans `runs/`.
- Un échec que la veille ne permet pas de dater (suite qui tourne pour la
  première fois) est « sans référence », pas « déjà rouge » : le format est
  passé à `jarvis.nightly/1.2`.
- client_desktop : le workflow installe les 17 paquets système que Qt charge en
  mode offscreen (le runner n'avait pas `libEGL.so.1`).
- Actions épinglées par SHA : checkout v7.0.1, setup-python v7.0.0,
  upload-artifact v7.0.1, download-artifact v8.0.1.

### Phase 2 — Une seule tâche IA par nuit (Groq gratuit) — ✅ réalisée le 2026-10-14, en dry run (4 critères à tester sur GitHub)

**But** : au réveil, au plus une PR qui corrige un vrai problème, ou un rapport
qui dit honnêtement pourquoi il n'y en a pas.

**Choix de la tâche** (dans cet ordre, une seule par nuit) :
1. une régression détectée par la phase 1 ;
2. un contrôle rouge sous Linux qui n'est pas dans la liste des échecs connus ;
3. une remarque ruff de la catégorie « vrai bug » (F821, F811, F841, E9) ;
4. *(phase 3)* un nœud `bug` ou `partiel` du graphe dont les fichiers sont dans
   la zone autorisée.
S'il n'y a rien : pas d'appel IA, rapport « rien à corriger ». C'est un bon
résultat.

**Zone autorisée** (liste blanche des chemins qu'un patch peut toucher) :
`context/`, `cognition/`, `eval_lab/`, `capabilities/` (code et graphes),
`reminders/`, `resilience/`, `offline/`, `extensions/`, `nightshift/`
**sauf** son propre workflow et ses règles de sécurité, `skills/`.
**Refusé d'office** : `main.py`, `core/`, `actions/`, `dashboard/`, `memory/`,
`plugins/` existants, `server/` (production), `client_desktop/`, `.github/`,
`deployment/`, `config/`, tout `requirements*`, `pyproject.toml`, tout fichier
de secrets, tout fichier binaire. Un patch qui sort de la zone est rejeté
entier, sans exception.

**Contexte ciblé** (budget : ≤ 6 000 tokens par appel, à cause de la limite de
8 000 tokens/minute) :
- symptôme exact (sortie du contrôle rouge, trace) ;
- `ast` : fonctions, classes, signatures, imports du fichier fautif ;
- `ripgrep` : où le symbole est défini et utilisé ;
- le contrôle du selftest qui échoue, en entier ;
- seulement les fonctions concernées, jamais un fichier entier s'il dépasse
  le budget ; élargir seulement si le premier diagnostic manque de preuves.

**Moteur d'édition** — décider sur mesure, pas sur la réputation :
- **2a. Minimal, d'abord** : le modèle renvoie un **diff unifié** ; le code le
  vérifie (`git apply --check`), rien d'autre. Fournisseurs via un adaptateur
  de `cognition/providers.py` construit depuis l'environnement :
  `gpt-oss-120b` → `qwen3.8-27b` → `gpt-oss-20b` → Gemini (sautés s'ils n'ont
  pas de clé). Gestion de 429 avec `Retry-After`, délais, 2 tentatives.
- **2b. Aider ensuite, si 2a plafonne** : l'évaluer sur 5 tâches simulées avec
  `--map-tokens` réduit et un seul fichier en contexte. L'adopter seulement
  s'il tient sous la limite de tokens/minute et fait mieux que 2a.
- OpenHands, SWE-agent, PR-Agent : écartés (section 10).

**Les jobs** (section 3) : proposition (secret Groq à une seule étape, aucune
exécution de ce que produit le modèle), validation (aucun secret, patch
commité localement avant les suites, comparaison à la baseline), publication
(aucun secret Groq ; si une PR Night Shift est déjà ouverte, on ne publie pas
et le rapport le dit).

**La PR** : branche `automation/jarvis-nightly-AAAA-MM-JJ`, titre court,
description générée **depuis les résultats**, pas depuis l'avis du modèle :
problème, preuve (contrôle rouge avant, vert après), fichiers, suites
relancées et leurs scores, limites, ce qu'il faut relire. Étiquette
`nightshift`. Une PR créée avec `GITHUB_TOKEN` ne déclenche pas d'autres
workflows : c'est acceptable puisque la validation a déjà tourné dans le run.

**Budgets par nuit** (variables de dépôt) : `NIGHTLY_MAX_TASKS=1`,
`NIGHTLY_MAX_LLM_CALLS=12`, `NIGHTLY_MAX_TOKENS=60000` (moins d'un tiers des
200 k tokens/jour), `NIGHTLY_MAX_RUNTIME_MINUTES=45`, `NIGHTLY_DRY_RUN`
(`true` : tout sauf la publication).

**Critères d'acceptation**
- [x] Tests sans token qui simulent : réponse du modèle vide, diff invalide,
      diff hors zone, 429 avec `Retry-After`, délai dépassé, réussite à la 2e
      tentative, échec définitif après 2 tentatives, aucune tâche disponible.
      *Preuve : `python -m nightshift.selftest`, contrôles 15 à 22, tous verts
      (22/22) ; faux transport et fausse horloge pour le client du modèle, vrai
      petit dépôt Git pour la proposition et la validation.*
- [ ] Un vrai bug planté exprès dans une branche de test (dans la zone
      autorisée) est corrigé et la PR est ouverte — en `workflow_dispatch`.
      *Fait en local le 2026-10-14 avec un faux modèle : régression
      « reminders n°1 » choisie, contexte de 2 209 tokens, patch accepté,
      suites relancées (3/4 → 4/4), verdict validé.*
      *Premier vrai run (2026-10-14, run 38560540611) : la régression est
      choisie et Groq (gpt-oss-120b) répond deux fois — 5 836 tokens —, mais ses
      deux diffs ont des en-têtes `@@` sans numéros : git apply les refuse
      (« No valid patches in input »). Corrigé le jour même par le réancrage
      (`nightshift/reanchor.py`, selftest n°31) et rejoué en local avec une
      réponse de ce format : patch accepté, verdict validé.*
      *Deuxième vrai run (run 38562012866) : gpt-oss-120b produit un patch
      applicable à la 2e tentative ; la validation relance tout : le n°1 passe,
      aucune régression. Mais le patch est rejeté à tort pour « 4 nouvelles
      remarques ruff » : avec `--repo .`, ruff ne gardait que le nom des
      fichiers. Corrigé (dépôt résolu en absolu) et rejoué à l'identique :
      VALIDÉ. Aucune PR n'a donc encore été ouverte : à relancer.*
- [x] Un patch qui touche `main.py` est rejeté avant toute exécution.
      *Preuve : selftest n°15 (17 patchs refusés entiers) et n°19 (rejeté par
      la proposition, dépôt intact) ; la validation refait le contrôle avant
      d'appliquer quoi que ce soit.*
- [ ] Une 2e nuit avec une PR déjà ouverte ne crée pas de doublon.
      *Écrit : la publication cherche une PR ouverte sur
      `automation/jarvis-nightly-*` avant d'en créer une. À vérifier sur GitHub.*
- [ ] Le secret Groq n'apparaît dans aucun journal (vérifier les logs du run).
      *Écrit : la clé ne vit que dans une étape, n'est jamais affichée, et le
      selftest n°18 vérifie qu'elle n'entre dans aucun journal ni message
      d'erreur. Reste à relire les journaux d'un vrai run.*
- [ ] Dix nuits en `NIGHTLY_DRY_RUN=true` avant la première publication réelle.
      *Commence avec la première nuit après la fusion.*

**Réalisé — ce qui diffère du plan**
- Le choix de la tâche et la construction du contexte se font dans le job
  « veille », sans secret : le job « proposition » ne fait qu'appeler le modèle
  (clé dans une seule étape) et vérifier son diff, sans rien exécuter.
- Zone autorisée plus stricte : les fichiers de test et le corpus d'eval_lab
  sont refusés (un correctif qui réécrit sa preuve ne prouve rien), et
  `nightshift/` l'est en entier (chacun de ses fichiers fixe ou applique une
  règle).
- Pas d'adaptateur de `cognition/providers.py` : il importe le cœur
  (`core.token_usage`) et `cognition.selftest` n°2 fige ses importeurs. Un petit
  client autonome (`nightshift/llm.py`, bibliothèque standard) le remplace.
- Le contexte d'une régression inclut le diff des commits suspects, limité à
  la zone : c'est souvent la pièce décisive.
- Faits vérifiés le 2026-10-14 : les modèles et limites Groq du plan tiennent ;
  `qwen/qwen3.8-27b` est en *preview* (peut disparaître sans préavis) ; Gemini
  est joignable en mode compatible OpenAI.
- Une régression ne fait plus échouer le job « veille » (sinon la nuit ne
  pourrait pas la corriger) : le run passe rouge à la fin, dans le job
  « rapport ».
- Un job « assemblage » réunit tout en un seul rapport (`jarvis.nightly/1.3`),
  pour que le job qui écrit sur `nightshift-data` n'exécute toujours aucun code.
- La publication vérifie l'empreinte SHA-256 du patch : elle publie exactement
  celui que la validation a prouvé. « Run workflow » propose une option
  `dry_run` (auto, true, false) pour les essais à la main.

### Phase 3 — La mémoire et le backlog — ✅ réalisée le 2026-10-14

**But** : ne pas recommencer à zéro chaque nuit, et choisir les tâches qui
comptent pour moi.

- **Mémoire** sur `nightshift-data` (`state.json`, écrit de façon atomique) :
  tâches vues, empreinte du contexte (une tâche identique au même commit n'est
  pas réanalysée), tentatives et leurs causes, verdict humain (PR fusionnée =
  accepté ; fermée sans fusion = rejeté, la même idée n'est pas reproposée).
- **Backlog** : les nœuds `bug`/`partiel` du graphe dont les fichiers sont dans
  la zone autorisée, plus les échecs persistants. Priorité simple et lisible :
  régression > bug > partiel ; à égalité, celui qui touche le moins de fichiers.
  Un fichier `nightshift/priorities.toml` me permet de forcer ou d'interdire
  une tâche.
- **Échec persistant** (2 tentatives) : un dossier de diagnostic dans le
  rapport — étapes, erreurs exactes, durées, hypothèses séparées des faits,
  « non disponible » plutôt qu'une valeur inventée. Pas de recherche web
  automatique (section 10).
- **Hebdomadaire** : le dimanche, un résumé de la semaine (tâches tentées,
  acceptées, rejetées, tokens consommés) au briefing.

**Critères** : une tâche déjà tentée n'est pas refaite au même commit ; une
PR fermée n'est pas reproposée ; le backlog affiché dans l'onglet NUIT
correspond aux nœuds du graphe.

- [x] Une tâche déjà tentée n'est pas refaite au même commit.
      *Preuve : selftest n°23 et 24, et à travers les vraies commandes sur le
      rapport du bug planté : la nuit suivante, au même commit, la régression
      est passée « déjà tentée au commit 475b8f2 avec le même contexte » et la
      candidate suivante est choisie.*
- [x] Une PR fermée n'est pas reproposée.
      *Preuve : selftest n°23 (fermée → refusée, même quand la PR n'est plus
      listée ; ouverte → en attente ; fusionnée → acceptée). À revoir sur la
      première vraie PR de main : la mémoire ne retient que les nuits de main.*
- [x] Le backlog affiché dans l'onglet NUIT correspond aux nœuds du graphe.
      *Preuve : selftest n°25 (exactement les 16 nœuds bug et partiel) et le
      rendu de l'onglet exécuté sous Node.*

**Réalisé — ce qui diffère du plan**
- Le backlog est affiché (rapport, onglet NUIT), pas confié au modèle : aucun
  contrôle ne prouverait le correctif d'un nœud du graphe, et le principe n°3
  exige une preuve. Le 2026-10-14, un seul des 16 nœuds est dans la zone
  autorisée (`context`, `l.voiture`).
- Une idée de correctif se nomme « check:<suite>:<n°> » ou
  « ruff:<code>:<chemin>:<message> » : c'est ce qu'attend `priorities.toml`.
- Les verdicts humains sont lus par `gh` dans le job « veille », avec un jeton
  en lecture seule (`pull-requests: read`).

### Phase 4 — Mesurer si JARVIS s'améliore vraiment — ✅ réalisée le 2026-10-14

Les tests disent « rien n'est cassé », pas « c'est mieux ». Ajouter au rapport
des indicateurs qui existent déjà dans le dépôt :
- les scénarios d'`eval_lab` (taux de réussite par cause) ;
- l'évaluation de la cognition (`cognition/evaluate.py`) ;
- le coût en contexte (`f.tokens` : caractères du prompt système, déclarations
  d'outils envoyées à chaque connexion) — une PR qui l'augmente est signalée.
Un correctif qui fait baisser un de ces indicateurs est marqué « à relire de
près », même si les tests sont verts.

**Fait** (`nightshift/metrics.py`) — chaque nuit dans le rapport, comparé à la
veille, et avant/après chaque correctif dans la validation :
- [x] eval_lab : taux de réussite par corpus et échecs par cause. *Mesuré le
      2026-10-14 : 76/77 sur le corpus de référence — un échec, cause
      `NO_DELIVERY_WITHOUT_SINK` (context/policy.py) — et 9/9 sur les
      régressions.*
- [x] Cognition : l'évaluation déterministe, architecture complète. *14 valeurs,
      identiques d'un passage à l'autre (vérifié deux fois).*
- [x] Coût en contexte. *34 outils, 23 520 caractères en mode local + 3 540 de
      prompt, ≈ 7 731 tokens par connexion — cohérent avec les ~8 200 tokens
      mesurés le 2026-10-13.*
- [x] Un correctif qui dégrade un indicateur est « à relire de près » : rapport,
      PR, briefing, onglet NUIT. *Preuve : selftest n°27 ; n°28 pour les
      mesures elles-mêmes, sur le vrai dépôt.*

*Écart* : une valeur de cognition qui change est signalée sans dire si c'est
mieux ou pire — « questions justes » doit monter, « faux souvenirs » rester
bas. Et pour mesurer sans importer JARVIS (contrat V2), cognition et coût en
contexte passent par deux sondes qui chargent le dépôt dans un processus à
part, comme `server.run_headless`.

### Phase 5 — Réveiller le PC pour les modèles locaux (facultatif) — ✅ réalisée le 2026-10-14, inactive tant qu'elle n'est pas planifiée

Seulement si les phases 1 à 4 ont prouvé leur valeur **et** qu'une tâche a
besoin du GPU ou d'Ollama (tri de nombreuses remarques, résumé de journaux).

- Planificateur de tâches Windows : déclencheur 03 h 00, case **« Réveiller
  l'ordinateur pour exécuter cette tâche »**, « Exécuter même si l'utilisateur
  n'est pas connecté », PC sur secteur. Vérifier d'abord `powercfg /a` et
  `powercfg /waketimers`, et activer les minuteurs de réveil dans les options
  d'alimentation.
- La tâche lance `python -m nightshift.local_worker` : lit la file de tâches
  sur `nightshift-data`, utilise `gemma4:12b` (tient en VRAM), écrit un
  résultat structuré par tâche (`schema_version`, `task_id`, `status` parmi
  `succeeded|failed|timed_out|skipped`, `started_at`, `finished_at`, `error`…),
  pousse les résultats, puis remet le PC en veille dès qu'il a fini ou à
  l'heure limite — en enregistrant l'erreur s'il échoue.
- Le job GitHub ne compte jamais sur ce PC : un modèle local absent est
  « indisponible », jamais « en attente ».

**Fait** (`nightshift/local_worker.py`, file et avis dans `assemble.py`) :
- [x] La file `queue.json` sur `nightshift-data` : des avis seulement — résumer
      les échecs de la nuit, trier les remarques ruff —, tirés du rapport public.
- [x] Le worker : Ollama et le modèle vérifiés, un résultat structuré par tâche
      (`schema_version`, `task_id`, `status` succeeded|failed|timed_out|skipped,
      `started_at`, `finished_at`, `error`…), 2 tentatives au plus, écriture
      atomique, heure limite, poussée sur `local/`, veille avec `--sleep-after`.
      *Preuve : selftest n°29 (faux Ollama) et le vrai worker lancé sur le
      portable, Ollama arrêté : deux tâches « skipped », rien de poussé.*
- [x] Côté GitHub, sans PC : « indisponible », jamais « en attente » ; les avis
      sont affichés comme des hypothèses et jamais lus par le briefing.
      *Preuve : selftest n°30.*
- [ ] À faire par toi, si tu le veux : la planifier sur le PC à RTX 3060
      (`python -m nightshift.local_worker --install-help` donne les étapes ; le
      portable, avec 4 Go de GPU, ne fait pas tenir `gemma4:12b`).

### Phase 6 — L'orchestration multi-modèles (plus tard, peut-être jamais) — ⏸ pas commencée

Les sections 14 à 17 de la v1 (Groq superviseur, workers locaux en parallèle,
registre d'état par tâche, enquête automatique) décrivent un projet de
recherche. À ne commencer que si les phases 1 à 5 tournent depuis des semaines
et qu'un goulot précis le justifie. Les décisions à garder pour ce jour-là :
- le signal de fin est un **résultat structuré**, pas un booléen ;
- écriture atomique (fichier temporaire puis renommage) ;
- 2 tentatives au maximum par tâche et par worker ;
- les états `queued`, `running`, `succeeded`, `failed`, `timed_out`,
  `cancelled`, `skipped` ; `succeeded` ≠ « bug corrigé » ;
- le statut global `success`, `partial_success`, `failed` ;
- un worker absent ne fait pas échouer la session ; il est déclaré absent.

**État au 2026-10-14** : pas commencée, comme le veut sa propre condition — les
phases 1 à 5 doivent d'abord tourner des semaines, et aucun goulot ne la
justifie. Les décisions ci-dessus sont déjà appliquées par les phases 2 et 5 :
résultats structurés (proposal.json, validation.json, résultats locaux),
écriture atomique, 2 tentatives au plus, ces états et ces statuts globaux, un
PC absent déclaré « indisponible ».

---

## 5. Les règles de sécurité (toutes phases)

1. Rien n'est poussé sur `main` par une machine ; aucune fusion automatique.
2. Le contenu du dépôt, les commentaires, la documentation et les réponses des
   modèles sont des **données non fiables** : ils ne peuvent changer ni la zone
   autorisée, ni les budgets, ni les permissions.
3. Le secret Groq n'existe que dans l'étape qui appelle le modèle ; cette
   étape n'exécute aucune commande proposée par le modèle et ne lance aucun
   test.
4. Le job qui exécute du code du dépôt n'a ni secret ni jeton d'écriture.
5. Le job qui écrit n'a pas de secret Groq et n'exécute pas les scripts du dépôt.
6. Jamais `pull_request_target`. Actions tierces épinglées par SHA.
7. Patch hors zone autorisée → rejeté entier. Toute modification de
   `.github/`, dépendances, permissions, déploiement ou config → refusée
   (humain seulement).
8. Aucune donnée personnelle ou secrète dans un prompt, un journal, un rapport
   ou une PR (dépôt public).
9. Le nombre d'appels, de tokens, de tâches et la durée sont plafonnés ; au
   plafond, on s'arrête proprement et le rapport le dit.

---

## 6. Ce que je dois pouvoir lire le matin

En une phrase au briefing, en détail dans l'onglet NUIT :
- ce qui a été vérifié (suites, scores, durée) ;
- ce qui a régressé, avec le commit suspect ;
- la tâche tentée et pourquoi elle a été choisie ;
- la PR (lien) ou la raison de son absence ;
- les tokens et appels consommés, comparés aux budgets ;
- ce qui reste non résolu.

---

## 7. Le format du rapport — `jarvis.nightly/1`

Stable et versionné ; tout ajout de champ incrémente la version mineure dans
la doc du graphe, tout changement incompatible passe à `jarvis.nightly/2`.
*Version actuelle : 1.2* — les champs ajoutés depuis l'exemple ci-dessous sont
décrits dans le graphe `nightshift` (nœud « Le format jarvis.nightly/1 »).

```json
{
  "schema_version": "jarvis.nightly/1",
  "run_id": "14275591234",
  "run_url": "https://github.com/TeddyDARKVADOR/Jarvis_replacement_new/actions/runs/14275591234",
  "commit": "e302e07",
  "started_at": "2026-10-14T03:17:41+02:00",
  "finished_at": "2026-10-14T03:29:02+02:00",
  "status": "success | partial_success | failed",
  "summary": "11/12 suites vertes ; 1 régression (cognition n°21) ; aucune PR (dry run).",
  "suites": [
    {"name": "cognition.selftest", "platform": "linux", "passed": 63, "total": 64,
     "status": "regression | known_failure | ok | error | timeout",
     "failures": ["21 chaîne : clé refusée -> longue pause"], "duration_s": 41.2}
  ],
  "regressions": [{"suite": "cognition.selftest", "check": "21", "since_commit": "abc1234"}],
  "task": {
    "id": "reg-cognition-21-e302e07", "source": "regression | failing_check | ruff | graph",
    "files": ["cognition/providers.py"], "attempts": 1,
    "outcome": "patch_validated | patch_rejected | no_patch | skipped",
    "reason": "…"
  },
  "pull_request_url": null,
  "usage": {"llm_calls": 3, "tokens_in": 9100, "tokens_out": 1400,
            "providers": {"groq/openai/gpt-oss-120b": 3}},
  "budgets": {"max_llm_calls": 12, "max_tokens": 60000, "max_runtime_minutes": 45},
  "limitations": ["client_desktop non testé sous Linux"]
}
```

---

## 8. Configuration

| Nom | Type | Défaut | Rôle |
|---|---|---|---|
| `GROQ_API_KEY` | secret | — | Clé Groq (jamais dans le dépôt ni les journaux). |
| `GEMINI_API_KEY` | secret | — | Secours facultatif. |
| `GROQ_MODEL` | variable | `openai/gpt-oss-120b` | Premier modèle essayé. |
| `NIGHTLY_DRY_RUN` | variable | `true` | `true` : tout sauf la publication. |
| `NIGHTLY_MAX_TASKS` | variable | `1` | Tâches IA par nuit. |
| `NIGHTLY_MAX_LLM_CALLS` | variable | `12` | Appels IA par nuit. |
| `NIGHTLY_MAX_TOKENS` | variable | `60000` | Tokens par nuit (limite Groq : 200 k/jour). |
| `NIGHTLY_MAX_RUNTIME_MINUTES` | variable | `45` | Durée maximale du run. |
| `NIGHTLY_WINDOWS` | variable | `false` | `true` : ajoute un job windows-latest (gratuit en dépôt public) pour les suites Windows. |

**Arrêt immédiat** : onglet Actions → workflow « nightly » → « Disable
workflow ». Ou mettre `NIGHTLY_MAX_TASKS=0` pour garder le veilleur sans IA.

---

## 9. Les astuces pour rester gratuit

- Dépôt public = Actions gratuit (Linux et Windows). Garder le dépôt public,
  donc ne jamais y mettre de donnée personnelle.
- Additionner les quotas : Groq (3 modèles, chacun ses limites) + Gemini +
  Cerebras/OpenRouter si j'ajoute des clés gratuites. La chaîne de
  `cognition/providers.py` le fait déjà.
- Un appel = un contexte minuscule : c'est la limite de 8 000 tokens/minute
  qui décide, pas le prix.
- Pas d'IA quand il n'y a rien à corriger (la phase 1 seule ne consomme rien).
- Le cache : ne pas réanalyser une tâche identique au même commit (phase 3).
- Un commit par mois au minimum, sinon GitHub désactive la planification
  après 60 jours d'inactivité.
- Option à comparer : si mon abonnement Claude inclut les routines planifiées
  de Claude Code (`/schedule`), un agent dans le cloud peut faire la phase 2
  sans coût supplémentaire, dans la limite du forfait.

---

## 10. Ce qui a été écarté, et pourquoi

| Idée de la v1 | Décision | Raison |
|---|---|---|
| OpenHands, SWE-agent | Écartés | Lourds (Docker, gros modèles), conçus pour des contextes que la limite de 8 000 tokens/minute ne permet pas. |
| PR-Agent | Écarté | La validation par les 306 contrôles du dépôt vaut mieux qu'une seconde opinion de modèle. |
| Recherche web automatique de solutions | Écartée | Ajoute des échecs, du risque d'injection, et du code privé pourrait fuiter dans les requêtes. |
| Plusieurs agents en parallèle dès le début | Phase 6 | Coût de maintenance supérieur au gain pour un développeur seul. |
| Modèles locaux la nuit | Phase 5 | Faibles pour écrire des correctifs ; utiles pour trier. |
| Oracle | Hors plan | VM en panne ; rien ne doit en dépendre. |
| Plusieurs PR par nuit | Refusé | Le temps de relecture est la ressource rare. |

---

## 11. Risques et garde-fous

| Risque | Garde-fou |
|---|---|
| Une PR convaincante qui casse ailleurs | Validation par toutes les suites testables, zone autorisée, fusion humaine, indicateurs de la phase 4. |
| Les tests Linux ne couvrent pas le HUD, l'audio, les actions Windows | Ces zones sont hors de la zone autorisée ; job Windows optionnel pour les suites qui s'y prêtent. |
| Injection de consignes via le code ou un commentaire | Règle de sécurité n°2 ; le modèle n'exécute rien ; diff filtré par le code. |
| Quota Groq épuisé | Chaîne de secours, budgets, rapport « partiel » plutôt qu'échec. |
| Fuite de secret dans un journal public | Secret à une seule étape, masqué par GitHub, test de non-présence dans les logs. |
| Planification retardée ou désactivée | Minute 17, `workflow_dispatch`, un commit par mois. |
| Je ne relis plus les PR | Une PR à la fois ; si trois PR de suite sont fermées sans fusion, la Night Shift repasse en `NIGHTLY_DRY_RUN=true` d'elle-même. |

---

## 12. Prompt de démarrage (à copier pour chaque phase)

```
Lis meilleur.idee.md (plan Night Shift) et CLAUDE.md. Réalise la PHASE 1,
uniquement elle.
- Respecte les sections 2.3 (règles du dépôt) et 5 (sécurité).
- Vérifie chaque fait du plan qui date (modèles Groq, syntaxe GitHub Actions,
  versions d'actions) avant de t'en servir ; signale ceux qui ont changé.
- Commence par établir l'état initial (suites, ruff) ; ne confonds pas un échec
  préexistant avec une régression.
- Termine par : les critères d'acceptation de la phase cochés un par un avec
  la preuve (commande et résultat), le graphe nightshift mis à jour avec la
  date du jour, python -m capabilities.selftest vert, et la liste de ce qui
  reste à faire à la main.
- Ne déclare rien de réussi qui n'a pas été exécuté.
```
