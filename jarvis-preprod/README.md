# jarvis-preprod — le laboratoire

Un banc d'essai pour le client Android, séparé du dépôt applicatif par une
seule règle : **rien ici ne touche un appareil réel.** `tools/adb.sh` refuse
tout numéro de série qui ne correspond pas à `emulator-*`. Le téléphone qui
sert tous les jours n'est pas une cible de test — un `force-stop` ou un
`uninstall` parti au mauvais endroit ne se rattrape pas.

## Démarrer

```bash
cd jarvis-preprod
./preprod deps        # vérifie la chaîne d'outils, ne touche à rien
./emulator/create.sh  # une fois : crée l'AVD jarvis_lab sur D:
./preprod build       # compile l'APK debug
./preprod all         # campagne complète, puis rapport
```

| commande | ce qu'elle fait |
| --- | --- |
| `deps` | JDK, SDK, platform-tools, émulateur, image système, AVD, gradlew |
| `build` | `:app:assembleDebug` |
| `up` / `down` | serveur MARK LIII + émulateur, laissés en marche / arrêtés |
| `smoke` | installe et vérifie que l'application démarre |
| `notifications` | les quatre priorités, la déduplication, le replay |
| `network` | perte réseau et reconnexion |
| `ui` | navigation et états |
| `audio` | ce qui est déterministe ; le reste sort en `PHYSICAL_ONLY` |
| `all` | tout, dans l'ordre, puis rapport |
| `report` | régénère le rapport depuis les résultats existants |
| `clean` | efface `reports/latest` |

Chaque campagne repart d'un `reports/latest` vide. Un rapport qui mélange les
résultats de deux exécutions ment sur au moins l'une des deux.

## Les quatre verdicts

| verdict | signification |
| --- | --- |
| `PASS` | vérifié sur l'AVD |
| `FAIL` | contredit sur l'AVD — capture, logcat et état ADB joints automatiquement |
| `SKIP` | non observable ici (serveur en mode `lab`, wake word désactivé…) |
| `PHYSICAL_ONLY` | **aucun émulateur ne le prouvera jamais** : micro, haut-parleur, A2DP |

`PHYSICAL_ONLY` est affiché dans son propre bloc du rapport, et pas fondu dans
les `SKIP` : une campagne entièrement verte ne doit jamais pouvoir se lire
comme « tout est validé ».

## Les deux modes du serveur

`tools/server.sh mode` répond `full` si `config/api_keys.json` existe, `lab`
sinon.

- **`full`** — `python -m server.run_headless`, le vrai assistant. Tout est
  testable, y compris ce qui passe par Gemini. Chaque campagne consomme du quota.
- **`lab`** — `tools/lab_server.py`, mêmes routes, même hub de notifications,
  sans session Live. Gratuit et déterministe, mais incapable de tester ce qui
  dépend du modèle.

Un test qui a besoin de `full` interroge le mode et se déclare `SKIP` en `lab`.
Échouer serait faux : la fonctionnalité n'est pas cassée, elle n'est pas
observable.

## Configuration

`config/lab.env` est la seule source de vérité : chemins, ports, numéro de
série, délais. Aucun autre fichier ne code une de ces valeurs en dur. Tout est
surchargeable par l'environnement, ce qui permet une seconde campagne en
parallèle sans éditer le fichier :

```bash
LAB_AVD=jarvis_lab2 LAB_SERIAL=emulator-5556 ./preprod smoke
```

Les chemins de la chaîne d'outils (`LAB_SDK`, `LAB_JDK`, `LAB_AVD_HOME`) sont
ceux du poste de développement d'origine — c'est la première chose à ajuster
sur une autre machine, et `./preprod deps` le dit en clair.

## Fixtures audio

`fixtures/audio/` est vide dans le dépôt et le reste. Y déposer des `.wav`
fait passer la vérification de présence, mais l'injection dans le pipeline
demande `-audio-input` sur l'émulateur et n'est pas implémentée : le test le
déclare `SKIP` plutôt que de laisser croire le contraire.

Ce qui est réellement prouvé côté audio, c'est le contrat — les constantes PCM
comparées entre `Protocol.kt` et `main.py` — et le verdict de
`WakeWordSelfTest`, qui rejoue un extrait fixe à travers le vrai pipeline et
compare à la référence openWakeWord à 1e-8 près. Il ne tourne qu'au démarrage
du service et seulement si le wake word est activé, que le laboratoire
désactive pour que l'AVD ne tienne pas le micro.

## Rapports

`reports/latest/` contient `report.html`, `report.json`, `results.jsonl` et,
par suite, les preuves des échecs. Le dossier n'est pas versionné : il est
effacé au début de chaque campagne, et un `server.pid` ne désigne rien sur une
autre machine. Un verdict se rejoue, il ne se relit pas dans l'historique.

`results.jsonl` reçoit une ligne JSON par vérification, au fil de l'eau : un
test interrompu laisse quand même tout ce qu'il avait déjà prouvé, et
`./preprod report` reconstruit le rapport à partir de là.
