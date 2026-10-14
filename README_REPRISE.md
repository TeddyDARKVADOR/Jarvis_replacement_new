# Reprise — CI, e2e et fusion automatique

> État au 2026-10-14, soir. Fichier de passation **temporaire**, demandé exprès
> (CLAUDE.md interdit les nouveaux `.md` de documentation) : à supprimer une fois
> le travail repris, son contenu ira dans les graphes (`ci.json`,
> `nightshift.json`…) et dans `meilleur.idee.md`.
>
> **Tout est sur la branche `feature/ci-e2e-automerge`**, poussée mais **pas fusionnée**
> (main n'a pas bougé). Le push y a lancé la première CI : son résultat (Actions → CI)
> est la première chose à lire.

## Où on en est

| Étape demandée | État | Vérifié |
|---|---|---|
| 1 — CI unitaires + build, Linux et Windows | `ci.yml` écrit, préalables faits | en local seulement ; **jamais lancée sur GitHub** |
| 2 — tests e2e | paquet `e2e/` écrit, 25 scénarios | serveur 18/18 (toolbox Ubuntu, Python 3.12), HUD 7/7 (Chromium, hôte Fedora, Python 3.14) ; **pas encore sous Windows** |
| 3 — fusion auto des branches | conçue (plus bas) | rien d'écrit |
| 4 — autres clés API + relecteur | code et selftests faits | **nightly.yml pas encore branché** |
| 5 — `NIGHTLY_AUTO_MERGE` (PR de la nuit) | conçue (plus bas) | rien d'écrit |

## Ce qui est fait

**Préalables de la CI**
- `core/code_workspace/selftest.py` n°2 corrigé (le nettoyage faisait `os.rmdir` sur un
  lien symbolique) : 34/34 sous Linux, plus aucun échec connu dans `nightshift/suites.py`.
- ruff : 0 remarque. `actions/code_helper.py:481` (F841) reste en exception dans
  `pyproject.toml` : `actions/` est figé.
- Versions épinglées (`==`) dans `requirements.txt` et `deployment/requirements-server.txt`
  (résolues le 2026-10-14 pour Python 3.12 ; paquets Windows : dernière version PyPI).
- `python -m nightshift.run` gagne `--strict` (tout ce qui n'est pas « ok » est rouge,
  sauf « sans objet »), `--no-metrics` et `--except`.

**La CI — `.github/workflows/ci.yml`**
- Déclenchée par `push` sur toute branche (sauf `nightshift-data`) et `workflow_dispatch`.
- Jobs :
  - `linux` et `windows` : `python setup.py` (le vrai build d'un PC), `compileall`, les
    suites en strict sauf l'e2e, puis `python -m e2e --strict`.
  - `serveur` : `deployment/install.sh` sur une Ubuntu propre, `systemd-analyze verify`,
    `--pairing`, puis `server.selftest` sous l'utilisateur `jarvis`.
  - `verdict` : vert seulement si les trois le sont. **C'est LE contrôle à exiger dans la
    règle de branche.**
- Aucun secret, jeton en lecture seule, actions épinglées par SHA (reprises de `nightly.yml`).

**Les tests e2e — paquet racine `e2e/` (retirable)**
- Un faux Gemini Live parle le vrai protocole (websocket TLS). Le vrai SDK y arrive par
  `GOOGLE_GEMINI_BASE_URL` et `SSL_CERT_FILE`, sans changer une ligne de `main.py`.
- Le vrai `python -m server.run_headless` tourne dans un bac à sable :
  - une copie des fichiers suivis par Git, jamais `config/` ni `memory/` réels ;
  - un HOME à part, et le réseau sortant coupé ;
  - de faux `systemctl`, `nmcli` et `systemd-run` sous Linux.
- 18 scénarios serveur :
  - briefing ;
  - commande tapée, voix et transcription ;
  - rappel, mémoire, graphe, compétences ;
  - porte CONFIRMER (mauvais id ignoré, ANNULER, CONFIRMER) ;
  - routage vers le téléphone et INTERRUPT ;
  - rotation avec reprise, coupure 1011 ;
  - `/status`, « au revoir » et SIGTERM.
- 7 scénarios HUD : la vraie page dans Chromium, avec les vrais graphes, l'onglet NUIT
  servi par `nightshift/fetch.py`, et les réglages.
- L'e2e est la 16e suite de la nuit. Il reste hors de la zone de la nuit : un échec e2e
  n'est jamais confié au modèle.
- Commandes : `python -m e2e`, `--only serveur|hud`, `--strict`, `--keep`, `--workdir DIR`.

**Quatre vrais bugs trouvés par l'e2e, corrigés**
1. **SIGTERM ignoré sur Oracle.** Chaque `systemctl restart` attendait 20 s puis tuait
   JARVIS, et le résumé de session était perdu. Corrigé par `_supervise()` dans
   `server/run_headless.py` : arrêt en 0,5 s, code 0.
2. **Conversation perdue à la 2e coupure.** `main.py` classait toute erreur après une
   reprise en « handle refusé », parce que « un**handle**d errors in a TaskGroup »
   contient « handle ». Corrigé par une condition de `main.py` (bug critique, autorisé
   par le contrat) : `re.search(r"\bhandle\b", …)`.
3. **Rappel jamais dit à voix haute dès qu'un téléphone est connecté.** La notification
   armait le silence IMPORTANT avant la décision vocale. `reminders/__init__.py` décide
   maintenant de la voix d'abord.
4. **Rappel refusé sur un serveur sans planificateur (Oracle).** `reminders.ReminderFallback`
   enveloppe le registre d'actions, puisque `actions/` est figé ; il est couvert par le
   selftest reminders n°5.

**Étape 4, la partie faite**
- `nightshift/llm.py` :
  - le fournisseur OpenRouter (`OPENROUTER_API_KEY`, et `OPENROUTER_MODELS`, dont le défaut
    est 3 modèles `:free` lus le 2026-10-14) ;
  - `role="review"` : Gemini, puis OpenRouter, puis Groq, sans la famille du modèle auteur.
- `nightshift/review.py` : le relecteur, avec un verdict `ok`, `bloque` ou `indisponible`.
  Il ne peut que bloquer une fusion automatique, jamais l'autoriser.
- Le verdict et la fusion automatique sont portés partout :
  - `assemble.py` : option `--review`, champs `task.review` et `task.auto_merge` ;
  - `briefing.py` : la phrase « fusion ce soir à 19 h sauf… » ;
  - `night.js` : les lignes RELECTURE et FUSION AUTO.
- Selftest nightshift :
  - n°32 (relecteur) et n°33 (CI stricte) ajoutés ;
  - n°2 adapté : l'e2e importe nightshift, mais de façon gardée.

## Ce qu'il reste, dans l'ordre

1. **Lire la première CI** de la branche `feature/ci-e2e-automerge` (Actions → CI) et
   corriger, sur cette même branche, jusqu'au vert sous Linux et Windows. Windows n'a
   jamais été essayé (suites, e2e, `setup.py`) : il faut s'attendre à des surprises.
   Reprendre en local : `git switch feature/ci-e2e-automerge`.
2. **Fusionner dans main à la main**, une seule fois. C'est l'amorçage : `fusion-auto`
   n'agit qu'une fois présent sur main, et aucune règle de branche n'existe encore.
3. **Écrire `.github/workflows/fusion-auto.yml`** (conception plus bas) et
   `.github/dependabot.yml`. Puis tester avec une petite branche.
4. **Brancher `nightly.yml` :**
   - un job `relecture` : la clé dans une seule étape, le code de main seulement, rien du
     correctif exécuté ;
   - `OPENROUTER_API_KEY` et `OPENROUTER_MODELS` dans l'étape de proposition ;
   - `--review` à l'assemblage ;
   - la publication :
     - lance `gh workflow run ci.yml --ref "$BRANCH"` (permissions `actions: write` et
       `issues: write`) ;
     - pose l'étiquette `nightshift:fusion-auto` si `NIGHTLY_AUTO_MERGE=true`, relecture
       `ok`, aucun indicateur bougé et 40 lignes au plus ;
   - les dépendances du HUD de la veille et de la validation, épinglées (contraintes
     tirées de `requirements.txt`) ;
   - **mettre à jour le selftest nightshift n°12** : 7 jobs, clés dans 2 jobs.
5. **Écrire `.github/workflows/nightshift-fusion.yml`**, à 19 h (Europe/Paris). Il fusionne
   les PR `automation/jarvis-nightly-*` qui remplissent toutes ces conditions :
   - étiquetées `nightshift:fusion-auto` ;
   - sans l'étiquette `garder` ;
   - CI `verdict` verte ;
   - à jour avec main.
6. **Mettre à jour les graphes**, avec `updated` à la date du jour et des statuts selon ce
   qui est vérifié :
   - créer `capabilities/graphs/ci.json` ;
   - `general.json` : `f.ci`, `dom.tests`, `f.nightshift` ;
   - `nightshift.json` : la suite e2e (ce qui répare le selftest n°14), le relecteur,
     OpenRouter, la fusion du soir ;
   - `server.json` : SIGTERM ;
   - `f.rappels` et l'outil `reminder`.

   Puis le reste de la documentation :
   - `CLAUDE.md` : ajouter le sous-graphe `ci`, et `python -m e2e` dans « Vérifier » ;
   - `meilleur.idee.md` : une section « le niveau suivant » ;
   - `suites.py` : reminders passe de 4/4 à 5/5.
7. **Supprimer ce fichier, et refaire `readme.md`** (voir la dernière section).

## À faire de ton côté, sur GitHub

- ✅ « Allow auto-merge » (déjà coché).
- **Après la première CI verte**, créer la règle de branche : Settings → Rules → Rulesets →
  New branch ruleset, nommée `main`.
  - Statut : Active ; cible : la branche par défaut.
  - Cocher : Restrict deletions, Block force pushes, Require a pull request (0 approbation),
    Require status checks → `verdict`.
  - Aucun contournement : sinon un push direct sur main échapperait à la CI.
- Settings → General → cocher « Automatically delete head branches ».
- Facultatif :
  - secret `GEMINI_API_KEY`, avec une clé distincte de celle de JARVIS ;
  - secret `OPENROUTER_API_KEY` ;
  - variable `OPENROUTER_MODELS`.
  - **Laisser `NIGHTLY_AUTO_MERGE` absente** pour l'instant : on l'allume après quelques
    vraies PR de la nuit relues à la main.
- Sur le portable : `sudo dnf install gh` puis `gh auth login`. Une fois la règle posée,
  on ne pousse plus sur main : on pousse une branche, et la CI décide.

## La fusion automatique, telle que conçue

**`fusion-auto.yml` (étape 3)**
- `ouvrir`, à chaque push d'une branche autre que `main`, `nightshift-data`, `wip/**`,
  `automation/**` et `dependabot/**` : crée la PR vers main si elle n'existe pas. Il
  n'utilise que `gh`, et n'exécute aucun code du dépôt.
- `fusionner`, sur `workflow_run` quand la CI se termine en succès. Il est défini sur main,
  donc une branche ne peut pas réécrire sa propre règle de fusion. Il vérifie :
  - que la tête de la PR est bien le SHA testé ;
  - que la branche est à jour avec main. Sinon : `PUT pulls/N/update-branch`, puis
    `gh workflow run ci.yml --ref`, parce qu'un push du `GITHUB_TOKEN` ne relance rien.

  Ensuite : `gh pr merge --merge --match-head-commit SHA`, puis la branche est supprimée
  par l'API.

**Pièges vérifiés**
- Une PR créée par le `GITHUB_TOKEN` ne lance `pull_request` qu'« en attente
  d'approbation ». C'est pourquoi la CI écoute `push` et `workflow_dispatch`, et pas
  `pull_request`.
- Une fusion faite par le `GITHUB_TOKEN` ne relance aucun workflow sur main. Ce n'est pas
  grave : la nuit repasse chaque jour.

**Dependabot** : chaque semaine, mises à jour mineures et correctifs seulement, groupées
(les majeures restent pour un humain). Les actions GitHub aussi.

**Les PR de la nuit (étape 5)**
- Elles ne passent jamais par `fusion-auto`.
- La publication pose `nightshift:fusion-auto` seulement si toutes les conditions de
  l'étape 4 sont réunies.
- Le briefing du matin l'annonce.
- `nightshift-fusion` fusionne à 19 h, sauf si tu as fermé la PR ou ajouté l'étiquette
  `garder`.

## Environnement local (portable Fedora)

- **Suites et e2e serveur**, en Python 3.12 comme la CI :
  `toolbox run -c ubuntu-toolbox-24.04 ~/.venvs/jarvis-ubuntu/bin/python -m e2e --only serveur`.
  Pour code_workspace n°33, faire d'abord `export PROTO_NODE_VERSION=24.21.0`, sinon
  Pyright n'a pas de node.
- **e2e HUD** : Chromium ne démarre pas dans la toolbox (libnss3, libatk… absentes, et pas
  de sudo). Le lancer depuis l'hôte : `~/.venvs/jarvis-host/bin/python -m e2e --only hud`
  (venv Python 3.14 + Playwright 1.64, créé aujourd'hui).
- **Échecs attendus en local** (les deux premiers disparaissent dans un arbre commité,
  donc en CI) :
  - `server.selftest` et `eval_lab.selftest` (« cœur non modifié » : `main.py`, `core/` et
    `requirements.txt` ont changé) ;
  - le selftest nightshift n°14 : le graphe nightshift ne cite pas encore la suite e2e.

## À refaire : le `readme.md` à la racine

Le `readme.md` (la page du dépôt) vient de la version d'origine : « Teddy-foundation (53) …
By FatihMakes », un mélange d'anglais et de français. Il ne dit presque rien de ce qui
existe aujourd'hui.

Il faudra le réécrire, court, en renvoyant aux graphes. Il devra couvrir :
- ce qu'est JARVIS : le cerveau sur Oracle, le HUD sur le PC, le mode local ;
- l'installation : `python setup.py`, avec des versions épinglées ;
- comment contribuer : pousser une branche, la CI Linux et Windows tourne, la fusion se
  fait seule si le verdict est vert ;
- le veilleur de nuit ;
- où est la documentation : `capabilities/graphs/`.
