# `avatar/gestures/` — les mouvements installables

**Ce dossier est volontairement vide dans git** : les clips Mixamo sont soumis
aux conditions d'Adobe et pèsent quelques centaines de kilo-octets pièce. Le
manifeste, qui dit lesquels installer, est versionné.

## Ce qui marche déjà sans rien installer

Quinze gestes sont **calculés**, pas chargés — tout ce qu'un cou et un buste
peuvent faire :

```
idle · look_at_user · look_away · look_around · nod · shake_head ·
tilt_head · blink_slow · lean_in · lean_back · sigh · shrug ·
turn · bow · stretch · think
```

C'est la grammaire de l'écoute, celle qui sert en permanence, et il serait
absurde de faire attendre un téléchargement à JARVIS pour qu'il puisse acquiescer.

Les clips gagnent pour **tout ce qui a des bras** : `wave`, `point`, `present`,
`explain`, `thumbs_up`, `cross_arms`, `facepalm`, `salute`, `type`, `count_off`,
et pour les déplacements : `walk`, `sit`, `stand`, `step_aside`.

## Installer un geste

### 1. Récupérer l'animation

Sur [mixamo.com](https://www.mixamo.com) : téléverser son propre personnage (ou
utiliser l'un des leurs), choisir l'animation, régler ses paramètres, puis
exporter en **FBX sans peau** (*Without Skin*) — seule la courbe d'animation
nous intéresse, le mesh est déjà là.

### 2. Convertir en glTF

Mixamo n'exporte pas en GLB. Au choix :

```bash
# FBX2glTF (binaire autonome, le plus rapide)
FBX2glTF --binary --input Waving.fbx --output wave

# ou Blender : importer le FBX, puis Fichier → Exporter → glTF 2.0 (.glb)
#   cocher « Animation », décocher le reste
```

Déposer le `.glb` obtenu ici.

### 3. Le déclarer

Dans `../manifest.json` :

```jsonc
"gestures": {
  "wave":      { "clip": "wave.glb" },
  "point":     { "clip": "pointing.glb" },
  "facepalm":  { "clip": "facepalm.glb", "index": 0 },

  // Ou, si le clip est DÉJÀ dans le modèle (fréquent sur un export Mixamo
  // complet), il suffit de le nommer :
  "think":     { "animation": "Thinking" }
}
```

Le nom du geste doit être l'un des **30 du vocabulaire**
(`presence/model.py`, `Gesture`) — c'est ce que JARVIS peut demander, et rien
d'autre ne sera jamais envoyé. `python -m presence.selftest` refuse un nom
inconnu dans le manifeste plutôt que de le laisser passer silencieusement.

### 4. Vérifier

```bash
python -m presence.selftest
```

Puis ouvrir `../lab.html` : les clips du modèle apparaissent en boutons, et le
panneau « Gestes » les joue à la demande.

## Le repli, et pourquoi on peut ne rien installer

Un geste non installé n'est pas une erreur. `presence/catalog.py` le remplace
par le plus proche que ce corps sait faire :

```
facepalm  →  shake_head  →  look_away  →  idle
salute    →  nod         →  look_at_user
count_off →  explain     →  present    →  point  →  turn
```

Chaque étape garde l'**intention** et n'abandonne que le moyen. La `Performance`
transporte `requested_gesture` quand une substitution a eu lieu, donc on peut
toujours lire *« a demandé facepalm, a joué shake_head »* — ce qui est la
manière la plus rapide de savoir quel clip vaut le coup d'être installé
ensuite.

## La pose de repos

Un humanoïde téléchargé arrive en T-pose ou A-pose : c'est à ça que sert une
pose de bind. Sans clip d'attente, il resterait bras écartés.

`gestures.js` abaisse donc les bras et fait respirer les épaules par défaut. Ce
n'est **pas** un remplacement d'un vrai *idle* Mixamo — celui-là déplace le
poids, les hanches, les doigts — mais c'est la différence entre un personnage
debout et un épouvantail. Si les bras tombent de travers sur un rig particulier,
`rig.armRest` dans le manifeste corrige les angles sans toucher au code :

```jsonc
"rig": { "armRest": { "shoulder": 1.13, "elbow": 0.16, "forward": 0.10 } }
```

## Un clip `idle` installé devient le fond

Installé comme n'importe quel geste (`"idle": { "clip": "idle.glb" }`), un clip
`idle` n'est pas joué une fois : il tourne **en boucle** comme couche de fond
dès le chargement (en `"motion": "full"` seulement). Chaque autre geste se fond
par-dessus (0.25 s) et le corps revient à l'idle en fondu à la fin (0.35 s), au
lieu de retomber en pose de bind. La pose de repos et la respiration des épaules
s'effacent alors devant le clip.

C'est ce qui rend l'ordre voulu — `idle` d'abord, puis `wave`, `point`,
`explain` — praticable sans toucher au code ni au cerveau : les intentions
demandent déjà ces gestes et se rabattent tant qu'ils manquent.

Et la calibration de la pose de repos (`rig.armRest`) appartient au modèle : elle
est rangée dans son profil quand on change de visage (voir `../models/README.md`).
