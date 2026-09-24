# Redéployer Oracle depuis une machine de secours

Ce document suffit pour remettre JARVIS à jour sur Oracle **depuis n'importe
quel PC** qui a ce dépôt, sans la machine principale et sans GitHub. Il complète
`ORACLE.md` (installation initiale) sans le remplacer : c'est le même
`install.sh`, le même service, la même copie git sur le serveur.

```text
dépôt → vérification → paquet → sauvegarde Oracle → déploiement
      → redémarrage → selftest → vérification de la version
```

Tout cela est `deployment/redeploy.sh`. Les étapes sont détaillées en tête du
script.

---

## 1. Ce qu'il faut sur la machine de secours

| Quoi | Pourquoi | Vérifier |
|---|---|---|
| Ce dépôt (clone ou copie) | le code déployé est **exactement** son `HEAD` | `git log -1` |
| Git Bash (Windows) ou un shell POSIX | le script est en bash | `bash --version` |
| Python 3.11+ avec `requirements.txt` | selftests locaux avant de déployer | `python -m server.selftest` |
| Tailscale, **connecté au même compte** qu'Oracle | Oracle n'est joignable que par le tailnet | `tailscale status` montre `jarvis-vps` |
| `ssh` / `scp` (fournis avec Git) | Tailscale SSH : **aucune clé, aucun mot de passe** | `ssh ubuntu@jarvis-vps true` |
| Le modèle actif, s'il doit être (re)copié | il n'est **pas** dans git (voir §3) | `avatar/models/jarvis/male/male.glb` |

La première connexion SSH d'une nouvelle machine peut demander une validation
dans le navigateur (« Tailscale SSH requires an additional check ») : ouvrir le
lien, se connecter, relancer.

## 2. La procédure

```bash
cd Jarvis_replacement_new
bash deployment/redeploy.sh --dry-run      # 1) tout vérifier : rien n'est modifié
bash deployment/redeploy.sh --backup-only  # 2) (facultatif) une sauvegarde seule
bash deployment/redeploy.sh                # 3) déployer ; demande confirmation
```

Le script **s'arrête sans rien écraser** si :

- l'arbre local a des fichiers suivis modifiés (on déploie un commit, pas un brouillon) ;
- un selftest local échoue ;
- la copie git d'Oracle a des modifications locales, ou un commit que ce PC n'a pas
  (jamais de réécriture d'historique : récupérer ce commit d'abord) ;
- `sudo` sans mot de passe n'est pas disponible, ou il reste moins de 500 Mo.

Options : `--yes` (sans confirmation), `--no-restart` (le service garde l'ancien
code jusqu'au prochain redémarrage), `--skip-tests`. Variables :
`JARVIS_SSH` (défaut `ubuntu@jarvis-vps`), `JARVIS_REMOTE_REPO`
(`Jarvis_replacement_new`, relatif au `$HOME` d'Oracle), `JARVIS_BACKUPS`
(`/var/backups/jarvis`), `JARVIS_KEEP_BACKUPS` (10).

**Le code voyage en `git bundle`** (≈ 6 Mo, tout l'historique jusqu'à `HEAD`),
appliqué par `git merge --ff-only`. Rien n'est poussé sur GitHub, rien n'est
récupéré de GitHub : un commit présent seulement sur ce PC se déploie.

Chaque déploiement ajoute une ligne à `~/jarvis-deploys.log` sur Oracle
(date, commit, sauvegarde).

## 3. Ce qui est versionné, généré, secret

| Élément | Où | Statut | Si on le perd |
|---|---|---|---|
| Code, `deployment/`, `avatar/manifest.json`, profils `*.model.json` | git | versionné | — |
| `.venv`, `__pycache__` | `/opt/jarvis` | généré par `install.sh` | relancer `install.sh` |
| `config/device_credentials.json` | Oracle `/opt/jarvis/config` | **secret**, généré au 1er démarrage | nouveau jeton → **ré-appairer le téléphone** |
| `config/api_keys.json` (clé Gemini) | Oracle `/opt/jarvis/config` | **secret**, posé à la main | nouvelle clé sur Google AI Studio |
| `config/certs/`, `config/whatsapp_web/` | Oracle | **secret** (s'ils existent) | refaire le jumelage concerné |
| `memory/long_term.json` | Oracle `/opt/jarvis/memory` | **donnée personnelle** | mémoire à long terme perdue |
| `uploads/` | Oracle `/opt/jarvis/uploads` | donnée | fichiers reçus perdus |
| Modèle 3D `male.glb` + `LICENSE.md` | `avatar/models/jarvis/male/` (ignoré par git) | **artefact non versionné**, 46 Mo | le retélécharger (ci-dessous) |
| Clé de signature debug Android | `%USERPROFILE%\.android\debug.keystore` sur **ce PC** | **secret** | l'app du téléphone ne se met plus à jour sans désinstallation |
| Sauvegarde des préférences du téléphone | `%USERPROFILE%\jarvis-backup\` sur ce PC | **secret** (contient le jeton) | ré-appairer à la main |
| Sauvegardes d'Oracle | Oracle `/var/backups/jarvis/` (root, 600) | secret (contiennent `config/`) | — |

**Aucun secret n'est dans git.** Vérifié le 2026-09-24 : les seules occurrences
de `AIza` dans l'historique sont des textes d'exemple (`"AIza…"`) ; `.gitignore`
couvre `config/api_keys.json`, `*credentials*.json`, `config/certs/`,
`config/whatsapp_web/`, `memory/long_term.json`, `.env*`, `avatar/models/**`.

**Le modèle** n'est copié vers Oracle que si son SHA-256 correspond à son profil
**et** si sa licence permet de le distribuer (`PHONE_LICENCES` dans
`server/avatar_api.py` : MIT, CC0, CC-BY). Le visage féminin (licence
« inconnue ») n'est jamais envoyé. Pour reconstituer le masculin sur une machine
qui ne l'a pas : la source est dans son profil
(`avatar/models/jarvis/male/male.model.json`, champ `provenance.source` —
Microsoft Rocketbox, MIT), puis `python -m presence.install_model` ; le SHA-256
doit redonner celui du profil, sinon ce n'est pas le même fichier.

## 4. Revenir en arrière

Chaque déploiement commence par une sauvegarde complète de `/opt/jarvis`
(`config/`, `memory/`, `uploads/` compris, sans `.venv`), et le commit d'avant
est noté à côté (`.commit`). Pour la restaurer, sur Oracle :

```bash
sudo ls -lt /var/backups/jarvis/                       # choisir la sauvegarde
B=/var/backups/jarvis/jarvis-AAAAMMJJ-HHMMSS-abcdef0.tgz
sudo systemctl stop jarvis
sudo mv /opt/jarvis /opt/jarvis.echec-$(date +%s)      # rien n'est effacé
sudo tar xzf "$B" -C /opt                              # recrée /opt/jarvis
sudo mv /opt/jarvis.echec-*/.venv /opt/jarvis/         # l'environnement Python
sudo chown -R jarvis:jarvis /opt/jarvis
cd ~/Jarvis_replacement_new && git checkout --detach "$(sudo cat "$B.commit")"
sudo systemctl start jarvis && curl -s http://127.0.0.1:8000/health
```

La copie git reste alors détachée sur l'ancien commit ; revenir sur `main`
(`git checkout main`) avant le prochain `redeploy.sh`.

## 5. Ce qui dépend encore d'autre chose que ce PC

- **Oracle lui-même** : les secrets et leurs sauvegardes sont sur la même VM.
  Si la VM est perdue, la clé Gemini se refait, le téléphone se ré-appaire, mais
  `memory/long_term.json` est perdu. Copier ces sauvegardes hors d'Oracle ferait
  sortir des secrets du serveur : c'est une décision, pas un réglage, et elle
  n'est pas prise ici.
- **Le compte Tailscale** : sans lui, Oracle n'est joignable que par son IP
  publique et une clé SSH qui n'est **pas** sur ce PC (`ssh ubuntu@<ip>`,
  `ORACLE.md` §2).
- **La tour** : elle a sa propre clé debug Android. L'app du téléphone est
  désormais signée par celle de **ce PC** (2026-09-24) ; la tour ne peut plus la
  mettre à jour sans désinstallation, sauf à lui copier `debug.keystore`.

## 6. Le labo Android sur ce PC

`jarvis-preprod` détecte seul le SDK, le JDK et les AVD (`config/lab.env`) : plus
besoin de `D:/DEV/android-toolchain` ni d'exporter `LAB_SDK`/`LAB_JDK`. Deux
réglages restent utiles ici :

```bash
export LAB_ALLOW_OTHER_DEVICES=1        # quand le vrai téléphone est branché
export LAB_EMULATOR_GPU=host LAB_EMULATOR_MEMORY=4096   # le visage 3D en rendu logiciel fige l'AVD
./preprod deps && ./emulator/start.sh --headless && ./preprod smoke
```
