# `avatar/models/` — le personnage

**Ce dossier est volontairement vide dans git.** Un modèle 3D appartient à
quelqu'un d'autre et pèse 3 à 15 Mo ; le versionner mettrait une licence qu'on
n'a pas dans un dépôt public, et gonflerait l'historique pour toujours. Le
manifeste, lui, est versionné : il décrit ce qu'il faut installer et comment le
piloter.

```bash
python -m presence.install_model --demo      # remettre un modèle qui marche
python -m presence.install_model --list      # où trouver un vrai personnage
```

## Ce qu'il faut chercher

Quel que soit le site, ce sont ces mots-clés qui décident si un asset est
exploitable ici :

```
humanoid · rigged · PBR · 52 ARKit blendshapes · visemes ·
morph targets · eye gaze · GLB / GLTF / VRM · compatible Mixamo
```

Un personnage avec **50+ morph targets faciaux** est incomparablement plus utile
qu'un beau mesh sans rig facial : sans blendshapes, JARVIS a un corps et pas de
visage.

## Les sources, par ordre d'intérêt

| Source | Prix | Ce qu'on obtient |
|---|---|---|
| **Ready Player Me** | gratuit | corps entier, 52 ARKit **+ visèmes Oculus**, GLB direct. La meilleure première installation. |
| **VRoid Studio** | gratuit | crée un VRM complet en un après-midi, sans savoir modéliser. Style anime. |
| **Mixamo** | gratuit | surtout pour les **animations** — voir `../gestures/README.md` |
| **Fab** (Aven, Inori) | payant | 128 à 165 blendshapes dont 52 ARKit. Très expressif. |
| **ArtStation** (James, Casual Man) | payant | rig facial dédié, 52 ARKit, lip-sync préparé |
| **MetaHuman** | gratuit | le plus réaliste, mais passe par Unreal puis FBX → GLB |

### Ready Player Me, en pratique

Créer un avatar sur readyplayer.me, copier l'identifiant depuis l'URL du `.glb`,
puis :

```bash
python -m presence.install_model --readyplayerme 64bfa15f0e72c63d7c3934a6
```

La chaîne de requête compte. L'installateur demande
`morphTargets=ARKit,Oculus Visemes` — **sans elle, le même avatar arrive avec 8
blendshapes au lieu de 52** et le visage ne bouge presque pas.

## Un mot sur Iron Man / JARVIS

Les assets « Iron Man » sont techniquement moins intéressants ici — souvent non
riggés, ou découpés en dizaines de meshes sans rig facial — et plusieurs sont
explicitement en usage éditorial seulement, les droits appartenant aux ayants
droit de Marvel. Pour un JARVIS qu'on veut publier et faire évoluer librement,
un personnage original avec une esthétique JARVIS vaut mieux qu'une copie.

## Vérifier ce qu'on vient d'installer

```bash
python -m presence.inspect                   # tous les modèles présents
python -m presence.inspect mon_modele.glb    # un seul
```

Le rapport dit ce qu'il y a **vraiment** dedans : combien de blendshapes ARKit
sont atteignables et sous quels noms, quels membres le squelette porte, quelles
animations sont embarquées. Si le compte est bas, la liste des morphs non
reconnus est affichée — l'un d'eux est souvent un ARKit sous un autre nom, à
déclarer dans `model.morphAliases`.

Puis ouvrir `../lab.html` et bouger les curseurs.
