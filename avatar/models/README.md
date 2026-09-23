# `avatar/models/` — les personnages

```
models/
  jarvis/
    female/   jarvis.glb           le visage actuel (actif)
              jarvis.model.json    son profil — versionné
    male/     README.md            la place du prochain visage
  test/       facecap.glb          tête de test, 52 ARKit, KTX2 + meshopt
              Michelle.glb         corps Mixamo sans visage, 2 animations
              *.model.json
```

**Les binaires ne sont jamais versionnés** : un modèle appartient à quelqu'un
d'autre et pèse de 3 à 40 Mo. **Les profils le sont** (`<modèle>.model.json`,
quelques Ko) : c'est ce qui dit quel fichier réinstaller, d'où il vient, sous
quelle licence, et comment il a été calibré. Voir `.gitignore`.

## Le profil d'un modèle

Écrit par `presence.install_model` à côté du fichier, jamais à la main sauf la
calibration :

| | |
|---|---|
| `id`, `name`, `version` | identité ; une nouvelle version dès que l'empreinte change |
| `sha256`, `size` | l'empreinte — le selftest refuse un profil qui ne décrit plus son fichier |
| `license`, `provenance` | d'où il vient, ce qu'on a le droit d'en faire (`--license`, `--source`) |
| `format`, `compression` | GLB / glTF / VRM, et KTX2 · DRACO · meshopt |
| `capabilities` | **mesurées** : formes ARKit, visèmes, yeux (formes/os), os de tête, membres, animations |
| `calibration` | ce qui n'est vrai que de CE fichier : alias de formes, `muteMeshes`, `armRest`, signes du regard, os forcés, échelle |

La différence qui compte : **préférences** (caméra, couleurs, `rig.motion`) et
**calibration** (tout ce qui est mesuré sur un fichier) ne vivent plus au même
endroit. Installer un modèle recopiait tout le manifeste en gardant le reste —
la sourdine `tongue01` du visage actuel s'appliquait au suivant, et sa pose des
bras aussi.

## Installer le visage masculin

```bash
python -m presence.install_model C:/Downloads/jarvis_male.glb --slot jarvis/male \
    --label "JARVIS (visage masculin)" --license "CC-BY 4.0" --source "https://…"
```

Ce que fait la commande :

1. copie le fichier dans `models/jarvis/male/` ;
2. le **mesure** (formes ARKit sous tous leurs noms, visèmes, yeux, os, membres,
   animations) et affiche le barème ;
3. écrit son profil ;
4. range la calibration du visage actuel dans **son** profil ;
5. active le nouveau : sa calibration entre dans le manifeste, les préférences
   ne bougent pas (`rig.motion` reste `face`, le cadrage reste `portrait`).

Rien d'autre. Aucun fichier de `presence/` ni du moteur ne change — c'est le
test `avatar/checks/model_swap.py` : 136 décisions de JARVIS identiques avant et
après.

Puis ouvrir `avatar/lab.html` : le profil **mesuré** du modèle s'affiche
(visage, yeux, tête, visèmes, membres). S'il faut calibrer — une sourdine, la
pose des bras, les signes du regard — l'écrire dans le manifeste : elle sera
rangée dans le profil du modèle au prochain changement de visage.

```bash
python -m presence.install_model --installed          # les modèles profilés, * = actif
python -m presence.install_model --use jarvis/female  # revenir au visage précédent
python -m presence.install_model --use jarvis/male
python -m presence.install_model --adopt <fichier>    # profiler un fichier déjà en place
                                                      # avec la calibration du manifeste
python -m presence.install_model <fichier> --no-activate   # installer sans porter
```

## Ce qu'il faut chercher

Quel que soit le site, ce sont ces mots-clés qui décident si un asset est
exploitable ici :

```
humanoid · rigged · PBR · 52 ARKit blendshapes · visemes ·
morph targets · eye gaze · GLB / GLTF / VRM · compatible Mixamo
```

Un personnage avec **50+ morph targets faciaux** est incomparablement plus utile
qu'un beau mesh sans rig facial. Les **visèmes Oculus** (`viseme_aa`, `viseme_PP`…)
sont un vrai plus : quand un modèle les a, la bouche parle avec les formes que
son auteur a sculptées au lieu d'une approximation ARKit.

## Les sources, par ordre d'intérêt

| Source | Prix | Ce qu'on obtient |
|---|---|---|
| **Ready Player Me** | gratuit | corps entier, 52 ARKit **+ visèmes Oculus**, GLB direct |
| **VRoid Studio** | gratuit | un VRM complet en un après-midi. Style anime |
| **Mixamo** | gratuit | surtout pour les **animations** — voir `../gestures/README.md` |
| **Fab** (Aven, Inori) | payant | 128 à 165 blendshapes dont 52 ARKit |
| **ArtStation** (James, Casual Man) | payant | rig facial dédié, 52 ARKit, lip-sync préparé |
| **MetaHuman** | gratuit | le plus réaliste, mais passe par Unreal puis FBX → GLB |
| **MPFB / MakeHuman** | libre | humains paramétriques ; le modèle actuel en a les noms de maillages |

```bash
python -m presence.install_model --readyplayerme <id> --slot jarvis/male
```

L'installateur ajoute lui-même `morphTargets=ARKit,Oculus Visemes` à une URL
Ready Player Me : sans lui, le même avatar arrive avec **8 blendshapes au lieu
de 52**, et rien ne le signale.

> **Les yeux de Ready Player Me sont des os, pas des blendshapes.** L'adaptateur
> le détecte et convertit les huit formes de regard en rotations. Si le regard
> part du mauvais côté, `rig.gaze.signY` / `signX` dans le manifeste — c'est de
> la calibration, rangée avec le modèle.

## Vérifier ce qu'on vient d'installer

```bash
python -m presence.inspect jarvis/male/jarvis_male.glb    # ce qu'il y a vraiment dedans
python -m presence.selftest                               # dont : le profil décrit-il le fichier
python avatar/checks/asset_robustness.py                  # 15 modèles incomplets, aucun ne tombe
```

## Un mot sur Iron Man / JARVIS

Les assets « Iron Man » sont techniquement peu intéressants ici — souvent non
riggés, sans rig facial — et plusieurs sont explicitement en usage éditorial
seulement. Un personnage original avec une esthétique JARVIS vaut mieux qu'une
copie, et c'est ce que `--license` et `--source` permettent de garder vérifiable.
