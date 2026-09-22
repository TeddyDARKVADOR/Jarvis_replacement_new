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

### Ready Player Me, pas à pas

C'est le chemin recommandé pour le corps définitif de JARVIS : gratuit, corps
entier, jeu ARKit complet, et il coche tout le barème sauf parfois les
animations.

**1. Créer l'avatar**

Aller sur [readyplayer.me](https://readyplayer.me), créer un avatar **corps
entier** (*full body*, pas *half body* — sans jambes, JARVIS ne se verra jamais
proposer les gestes qui en demandent). À la fin, le site donne une URL de la
forme :

```
https://models.readyplayer.me/68f1c0a4b2e5d70012345678.glb
```

L'identifiant est la partie avant `.glb`.

**2. Installer**

```bash
python -m presence.install_model --readyplayerme 68f1c0a4b2e5d70012345678
```

Ou, si on préfère coller l'URL entière :

```bash
python -m presence.install_model "https://models.readyplayer.me/68f1c0a4b2e5d70012345678.glb"
```

Les deux formes sont sûres. **La chaîne de requête compte** : sans
`morphTargets=ARKit`, le même avatar arrive avec **8 blendshapes au lieu de
52** — il se charge, il s'affiche, et son visage ne bouge pas. Rien ne le
signale. L'installateur l'ajoute donc lui-même, y compris quand on colle une
URL nue, et il le dit dans sa sortie.

**3. Vérifier**

La commande affiche le barème toute seule. On veut voir :

```
    [x] corps entier            arms, head, legs, torso
    [x] rig facial              52/52 blendshapes ARKit
    [ ] visemes                 aucun — lip-sync approxime depuis les formes ARKit
    [x] yeux pilotables         os ou blendshapes de regard
    [ ] animations embarquees   aucune — installer un idle Mixamo
```

Les deux `[ ]` sont normaux et sans gravité : le lip-sync fonctionne par
approximation ARKit, et l'idle procédural évite la T-pose en attendant un clip
Mixamo (`../gestures/README.md`).

Puis :

```bash
python -m presence.selftest     # doit rester à 39/39
```

et ouvrir `../lab.html` : bouger les cinq curseurs d'état et vérifier que le
visage, le regard et la posture suivent.

> **Les yeux de Ready Player Me sont des os, pas des blendshapes.** L'adaptateur
> (`../js/body_gltf.js`) le détecte seul et convertit les huit formes ARKit de
> regard en rotations. Si le regard part du mauvais côté sur un rig particulier,
> `rig.gaze.signY` / `signX` dans le manifeste inversent les axes — même
> principe que `rig.armRest`, et le labo est où on le vérifie en trois clics.

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

Le rapport se termine par un **barème** : ce modèle fait-il l'affaire comme
corps définitif de JARVIS ?

```
  Ce modele comme corps definitif de JARVIS

    [ ] corps entier            head
    [x] rig facial              52/52 blendshapes ARKit
    [ ] visemes                 aucun — lip-sync approxime depuis les formes ARKit
    [x] yeux pilotables         os ou blendshapes de regard
    [x] animations embarquees   4 clip(s)
```

Rien n'y est bloquant : un « non » coûte une capacité, jamais le chargement. Et
un modèle à qui il ne manque qu'une animation d'attente est à deux minutes
d'être parfait — le barème le dit plutôt que de laisser repartir en chercher un
autre.

Le rapport complet dit ce qu'il y a **vraiment** dedans : combien de blendshapes ARKit
sont atteignables et sous quels noms, quels membres le squelette porte, quelles
animations sont embarquées. Si le compte est bas, la liste des morphs non
reconnus est affichée — l'un d'eux est souvent un ARKit sous un autre nom, à
déclarer dans `model.morphAliases`.

Puis ouvrir `../lab.html` et bouger les curseurs.
