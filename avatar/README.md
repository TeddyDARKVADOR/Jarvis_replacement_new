# `avatar/` — le corps de JARVIS

Un moteur three.js qui prend un **vrai personnage 3D** et lui fait jouer les
décisions que `presence/` produit. Rien ici ne fabrique le personnage : tout ici
le fait vivre.

```
avatar/
  index.html            le corps, tel que le panneau l'affiche (fond transparent)
  lab.html              le labo : demande → décision → sortie effective
  manifest.json         QUEL modèle, QUELS gestes — le seul point d'extension
  models/               les personnages et leurs profils (binaires non versionnés)
  gestures/             les clips Mixamo (non versionnés)
  checks/               ce que le selftest ne peut pas vérifier — voir checks/README.md
  vendor/               three.js + KTX2 + DRACO + meshopt + VRM, en local
  js/
    engine.js           LE moteur : une Performance entre, un visage sort
    performance.js      le temps d'un visage : décalages, montée, maintien, relâche
    rig.js              les couches, leur priorité, et l'écriture dans le modèle
    gaze.js             le regard : les yeux d'abord, la tête ensuite
    states.js           le comportement de fond de chaque état (écoute, parole…)
    accents.js          le bref geste facial d'une intention
    lipsync.js          la bouche qui suit la voix
    conversation.js     ce que la voix appelle : appuis, regard qui s'echappe, hochement d'ecoute
    voice.js            lire une voix dans un niveau : syllabes, appuis, pauses
    blink.js            les paupieres, et POURQUOI elles se ferment
    gestures.js         la tête et le corps : clips Mixamo OU arithmétique
    idle.js             le repos : respiration, dérive, micro-expressions
    rng.js              le hasard, avec une graine — rejouable
    recorder.js         enregistrer une séance, la rejouer à l'identique
    stage.js            la scène : lumière, caméra à hauteur des yeux, cadrage
    profile.js          ce que le modèle chargé sait RÉELLEMENT faire
    catalog.js          miroir de presence/catalog.py (gestes, repli)
    director.js         miroir de presence/director.py, pour le labo
    body_gltf.js        un .glb/.gltf, ses blendshapes, ses visèmes, ses os
    body_vrm.js         un .vrm, ses expressions, son lookAt
    body_procedural.js  le dernier recours, quand aucun modèle n'est installé
    body_null.js        un corps sans rendu : tests et rejeu
    main.js             le panneau : manifeste, corps, moteur, boucle
    lab.js              le labo, sur le même moteur
    bridge.js           le transport : perform() / speak() / viseme()
    affect.js           la dérivation (générée depuis presence/affect.py)
    situations.js       douze moments ordinaires, pour le labo ET le test
    explain.js          pourquoi le visage fait ce qu'il fait, en huit lignes
    expressions.js      les 12 visages (générés depuis presence/vocabulary.py)
    arkit.js · visemes.js · xhr_loader.js
```

## Démarrer

```bash
python -m presence.install_model --demo      # une tête humaine, 52 blendshapes ARKit
python -m presence.selftest                  # 60 contrôles, sans navigateur
node avatar/checks/engine_test.mjs           # le moteur, mesuré par sa sortie
```

Puis, dans le client de bureau, activer `avatar_enabled` dans les réglages.
Le cœur 2D reste le défaut : l'avatar est un processus Chromium et un contexte
GPU pour toute la durée de la session.

## Qui décide quoi

C'est la règle qui rend le visage remplaçable, et chaque fichier la respecte :

| | décide | ne connaît jamais |
|---|---|---|
| **JARVIS** (le LLM) | *pourquoi* : une intention, un état, un visage nommé, un regard | un os, une forme ARKit, un maillage |
| **`presence/`** | *quoi* : le visage, son intensité, le regard, le geste et son repli, l'accent | le fichier chargé |
| **le moteur** (`js/`) | *comment* : quand chaque forme part, à quelle vitesse, qui gagne, la vie entre deux décisions | le nom du modèle |
| **le modèle** | *ce qu'il peut* : ses formes, ses visèmes, ses os — mesurés | rien : c'est un fichier |

Le test qui le prouve : `avatar/checks/model_swap.py` installe un visage
masculin à côté du féminin et compare les 136 décisions de JARVIS avant et
après — **identiques**. Changer de visage est une installation, pas un chantier.

## Le chemin d'une intention

```
JARVIS appelle set_presence(intent="investigate")        plugins/presence.py
  → {"type":"avatar","ts":…,"directive":{…}}  sur /ws    validé par presence.parse
  → client_desktop/net.py       trop vieux (>25 s) ? déjà vu ? plus ancien que le
                                dernier joué ? → jeté. Sinon, avec son âge.
  → avatar_view.set_intent_json  parse(), le MÊME ; l'intention est datée de
                                 la décision, pas de l'arrivée du paquet
  → Director.resolve             réflexe → affect → intention ; repli du geste
  → Performance.as_json          + state, gaze_source, gesture_id, accent
  → window.JARVIS.perform        bridge.js
  → AvatarEngine.perform         engine.js
  → sortie effective             ce que le modèle reçoit
```

`avatar/checks/chain_test.py` suit 44 intentions sur tout ce chemin, à travers
le vrai code de chaque maillon, et vérifie le bout : le geste réellement joué,
le regard réellement tenu, l'accent réellement joué, et que rien ne bouge sous
la nuque en mode visage.

> **Le maillon qui manquait.** Jusqu'à cette révision, *aucune* intention
> envoyée par JARVIS n'atteignait le visage : le store émettait la directive
> nue, le widget y cherchait une clé `directive`, et jetait tout en silence.
> Chaque moitié avait son test, et chacune passait.

## Le moteur

`engine.js` est le seul moteur : le panneau et le labo construisent le même
objet sur le corps qu'ils ont chargé. Il ne connaît pas three.js — le corps est
une interface (`setMorph`, `nodes`, `update`, `capabilities()`) — et tourne donc
tel quel sous Node, sur `body_null.js`, pour les tests et le rejeu.

Une image, dans cet ordre :

```
1. état de présence   les paramètres de fond (states.js), fondus entre deux états
2. lip-sync           la couche bouche, et « est-il en train de parler »
   conversation       ce que la voix appelle : appuis de tête, regard qui s'échappe
                      en début de phrase, clignement en fin, hochement aux pauses
                      de l'utilisateur (conversation.js — voir avatar/BEHAVIOUR.md)
3. accent             le geste facial de l'intention en cours
4. gestes             la tête et le corps — posture + part de tête du regard
                      + inclinaison d'état + geste + accent + repos, SOMMÉS
5. rig                les couches du visage, leur priorité, les yeux
6. corps              ce qui en dépend (yeux à os, VRM)
```

### Les couches, et qui gagne

| priorité | couche | ce qu'elle possède |
|---|---|---|
| 1 | **sûreté** | `Gaze.CLOSED` ferme les paupières quoi qu'il arrive |
| 2 | **explicite** | `setOverride` — un curseur du labo, un test |
| 3 | **parole** | la mâchoire et les lèvres, pendant qu'il parle |
| 4 | **intention** | l'accent (`accents.js`) |
| 5 | **émotion** | l'expression, telle que `performance.js` la déroule |
| 6 | **repos** | les micro-expressions |

Dans une même priorité, `max()` : deux couches qui lèvent un sourcil ne
dépassent pas 1. Les yeux et les paupières sont à part : le contrôleur de regard
possède les huit `eyeLook*`, les paupières valent le max de l'expression, du
clignement et de la paupière qui suit un regard vers le bas.

Mesuré (`engine_test.mjs`) : pendant un flash de sourcils, `browInnerUp` est
gagné par l'accent ; un curseur explicite gagne sur tout ; les yeux fermés
gagnent sur le curseur ; un clignement ne gagne jamais sur la bouche ; sous un
visage triste à 0.8, le repos tombe à **17 %** de son amplitude et n'écrase
aucune forme de l'expression.

`rig.explain(nom)` rend la contribution de chaque couche à une forme et la
gagnante — c'est ce que le labo et le rejeu appellent pour répondre à « pourquoi
cette forme vaut ça ».

### Le temps d'un visage — `performance.js`

Trois niveaux : la **cible** (ce que `presence/` a décidé), la **performance**
(comment y aller), la **sortie** (ce qui est écrit après les autres couches).
Ce fichier ne change aucune valeur ; il décide la trajectoire de chacune.

**Qui part quand** — le décalage par groupe de formes, lu dans le nom ARKit :

```jsonc
amused:    { mouth: 0.13, cheek: 0.15, nose: 0.13 }   // l'ironie monte dans les yeux
thinking:  { eye: 0.05, mouth: 0.16, jaw: 0.16 }      // les sourcils mènent
surprised: { eye: 0.01, mouth: 0.03, jaw: 0.03 }      // d'un bloc — et c'est juste
```

**À quel rythme** — montée, relâche, maintien naturel et résidu, par visage.
La surprise est la plus brève des émotions (montée < 0.2 s, rarement tenue plus
d'une seconde) ; un sourire spontané monte en un demi-seconde et repart plus
lentement. Le défaut n'est pas inventé : c'est l'ancien lissage, converti.

**En courbe en S** — chaque forme est un ressort critiquement amorti, résolu
exactement sur le pas de temps. L'ancien lissage exponentiel partait à vitesse
maximale : son plus grand pas était le premier, un coin que l'œil lit comme un
tressaillement. Le ressort part à vitesse nulle et conserve sa vitesse si la
cible change en route.

Mesuré sur la sortie :

| | 90 % atteint | 1re image → 2e | plus grand pas |
|---|---|---|---|
| `surprised` | 0.13 s | +0.092 → +0.172 | 0.17 |
| `amused` | 0.47 s | | 0.02 |
| `thinking` | 0.57 s | | 0.02 |

La surprise retombe d'elle-même : 0.90 → 0.32 en 2.6 s sans nouvelle décision.
`amused` se compose : sourcils à mi-course à 0.18 s, bouche à 0.30 s.

> **`hold_s` n'était lu par personne**, puis il l'était pour tous. Le moteur ne
> le lisait pas (WAKING tenait sa surprise indéfiniment) ; une fois lu, une
> intention arrivée pendant WAKING héritait des 1.2 s du réflexe et relâchait son
> visage. `hold_s` appartient désormais au seul visage réflexe.

### Le regard — `gaze.js`

Un regard humain vers une cible latérale : les yeux partent seuls, la tête suit
~80 ms plus tard et plus lentement, et pendant qu'elle arrive les yeux reviennent
vers le centre de l'orbite. C'est ce que fait le contrôleur, et le dernier temps
est le réflexe vestibulo-oculaire — il sert aussi quand la tête hoche ou dérive :
JARVIS garde le contact visuel au lieu de balayer la pièce à chaque respiration.

Mesuré (`screen`) : les yeux à mi-course en **17 ms**, la tête en **250 ms** ;
les yeux montent à 0.84 puis se posent à 0.50.

Les règles :

- la **cible** vient de la Performance — donc de JARVIS, déjà tranchée en Python
  (explicite > intention > affect > réflexe) ;
- un regard **décidé** (`gaze_source` explicite, intention, sûreté) n'est jamais
  déplacé par un comportement de fond : pas de coup d'œil, et les yeux tiennent
  leur cible même quand un geste emporte la tête (`investigate` + `gaze: user` :
  tête à −18.6°, yeux sur l'utilisateur) ;
- les micro-saccades de fixation restent toujours — un œil parfaitement immobile
  est un œil de verre ;
- un grand déplacement du regard s'accompagne souvent d'un clignement.

Le regard était auparavant noyé dans l'expression : il attendait que la bouche
de `thinking` ait fini de se composer, et partait en même temps que la tête.

### La tête — `gestures.js`

Tout ce qui tourne la tête est **sommé**, pas priorisé : posture, part de tête
du regard, inclinaison de l'état (écoute : +2.5° de roulis), geste, accent,
repos. C'est ce qui permet d'écouter, de regarder et de hocher en même temps.
Mesuré : 60 gestes enchaînés, jamais plus de 18° ; retour à l'inclinaison
d'écoute ensuite — rien ne s'accumule.

Un geste ne rejoue que pour une **nouvelle décision** (`gesture_id`), plus pour
un nouveau nom. En mode visage, sept intentions sur seize se rabattent sur `nod`,
comme le réflexe SPEAKING : pendant la parole, le hochement d'`agree` était
avalé parce qu'il portait le même nom que ce qui venait de jouer.

`play()` dit ce qu'il a fait : `procedural`, `clip`, `frozen` (membre tenu par
`rig.motion`), `absent` (le modèle n'a pas l'os), `none`. Le labo affiche cela,
pas la demande.

### La parole — `lipsync.js`

Une seule entrée, le niveau de la voix (25 Hz) — Gemini Live ne donne pas de
phonèmes alignés. Le lip-sync choisit un visème par syllabe depuis la forme de
l'enveloppe, et les **fond** entre eux (coarticulation) au lieu de sauter. Il
produit une couche bouche et un signal « il parle », qui tient à travers les
pauses entre les mots.

La bouche et l'émotion sont deux couches (voir le tableau) :

| mesuré | |
|---|---|
| sourire `amused` pendant 3 s de parole | 0.55 → jamais sous 0.44 ; mâchoire jusqu'à 0.49 |
| mâchoire ouverte par la surprise (0.45) | descend à 0.02 pendant la parole |
| phrase coupée en pleine voyelle | refermée en 217 ms, pire image −0.08 |
| `amused` → `concerned` en pleine phrase | plus grand pas de la couche émotion : 0.056 |

Un modèle qui porte les **visèmes Oculus** (le modèle installé en a 14) est
piloté par eux, sculptés par son auteur ; l'approximation ARKit n'est alors pas
écrite (sinon deux mâchoires s'additionnent). Un VRM parle par ses cinq visèmes.
`viseme(nom, poids)` accepte une vraie source de phonèmes — nos huit noms ou les
quinze Oculus — le jour où il y en a une.

### Les états de présence — `states.js`

Le LLM ne gère pas ces micro-états ; il ne sait même pas qu'ils existent. Le mot
d'état arrive dans chaque Performance, et ce fichier en tire le fond :

| état | clignements/min | coups d'œil | tête | micro-expressions |
|---|---|---|---|---|
| idle | 17 | 6–12 s | — | normales |
| listening | 13 | rares | inclinée, un peu relevée | un peu moins |
| thinking | 21 | — (le regard part déjà) | inclinée de l'autre côté | plus |
| speaking | 25 | 3–7 s, latéraux | — | moins |
| reacting / unavailable / loading | 10 / 6 / 17 | | | |

(Bentivoglio et al. 1997 pour les fréquences ; Kendon 1967 pour le regard qui
s'échappe en parlant.) Mesuré sur 2 min : 55 clignements en parlant, 27 en
écoutant, dont 4 doubles. Les paramètres se fondent en 0.4 s ; l'état rapporté
vaut `transitioning` pendant ce temps.

### Les accents — `accents.js`

En mode visage, trois paires d'intentions jouaient *exactement* la même chose
(`greet`/`report_success`, `farewell`/`agree`, `acknowledge`/`explain`). Un
humain immobile les distingue par un bref signal facial :

| accent | intentions | ce qu'il fait |
|---|---|---|
| `brow_flash` | greet, farewell | les deux sourcils montent un tiers de seconde |
| `chin_up` | report_success | menton relevé, bouche retenue |
| `beat` | explain | petits appuis de tête au rythme de la phrase |
| `head_down` | apologise | tête basse, que `bow → nod` ne disait plus |

JARVIS ne les nomme jamais : c'est le *comment* de l'intention. Ils jouent aussi
en mode complet (on salue de la main **et** des sourcils).

## Le labo

Ouvrir `avatar/lab.html` (servi par le client sous `jarvis://`, ou par
`python -m http.server -d avatar 8777`).

Le labo ne joue rien lui-même. Il construit une **demande** — ce que JARVIS
enverrait — la fait lire et résoudre par `director.js`, miroir exact de
`presence/director.py` (`director_parity.py` : **3 432 décisions et 85 pas de séquence identiques**),
et donne la Performance au **même** moteur que le panneau. Puis il affiche ce que
le moteur rapporte avoir fait :

```
DEMANDE           {"intent":"greet"}
RÉSOLU            intention greet · affect v+0.76 a0.64 · regard user (intent)
DÉCISION          happy 0.68 · regard user · posture attentive · geste wave → nod · accent brow_flash
SORTIE EFFECTIVE  nod ✓ joué · accent brow_flash ✓ · 11 formes · état speaking · attack
                  tête rx −1.2° ry 0.3° · yeux x 0.02 y −0.01 · parle 0.84 · visèmes natifs aa,O
TRACE             INTENT greet · GAZE user (intent) · MODEL CAPABILITY face · BODY ACTION wave
                  FALLBACK nod · FACIAL TARGET happy 0.68 · ACCENT brow_flash
```

L'ancien labo dérivait avec une règle à lui : `greet` y affichait `wave → idle`
pendant que le panneau hochait la tête, un visage forcé y prenait la posture
dérivée, et `angry` y montait à 0.9 au lieu d'être plafonné à 0.45.

On y trouve aussi : les états machine, les seize intentions, les visages forcés,
les regards, les gestes, un curseur par blendshape (la couche explicite), la voix,
des **scénarios** qui dépendent du temps (phrase courte, longue, interruption,
émotion en parlant, intention sur intention, regard explicite en réflexion,
urgence, réveil puis veille), les **mesures** (i/s, ms par image, coût de chaque
sous-système, écritures par image) et l'**enregistrement**.

## Rejouer une séance

`recorder.js` enregistre les **entrées** du moteur — chaque Performance, niveau
de voix, visème, valeur imposée — avec l'image où elles sont arrivées, le pas de
temps de chaque image et la graine du hasard. Le moteur tire tout son hasard de
`rng.js` (le selftest refuse un `Math.random()` dans le moteur) : rejouer ces
entrées reproduit chaque coefficient **à la dernière décimale**, et le rejeu le
prouve en comparant ses échantillons de sortie à ceux de l'enregistrement.

Labo : « Enregistrer » (moteur neuf, graine neuve), « Exporter », « Rejouer… » —
le rejeu vérifie d'abord l'exactitude hors écran, puis rejoue image par image.
Panneau : `JARVIS_AVATAR_DEBUG=1` fait enregistrer la séance
(`window.JARVIS.recording()`), 20 minutes au plus.

## Diagnostic

`JARVIS_AVATAR_DEBUG=1` (côté client) écrit, pour chaque décision poussée :

```
[avatar] 12:42:11 WIRE = {"intent": "investigate"} (decide il y a 84 ms)
[avatar] 12:42:11 STATE = SPEAKING
[avatar] 12:42:11 INTENT = investigate
[avatar] 12:42:11 AFFECT = thinking 0.44 (v+0.00 a0.55 att0.30 conf0.55 urg0.15)
[avatar] 12:42:11 GAZE = screen (intent)
[avatar] 12:42:11 MODEL CAPABILITY = face
[avatar] 12:42:11 BODY ACTION = turn
[avatar] 12:42:11 FALLBACK = look_away
[avatar] 12:42:11 FACIAL TARGET = thinking 0.44
[avatar] 12:42:11 ACCENT = -
[avatar] 12:42:11 RENDER = success (look_away procedural)
```

`RENDER` vient du moteur, relu dans la page : `frozen` ou `absent` y
apparaissent quand le corps n'a pas pu jouer. Sans la variable, rien n'est écrit.
Dans la page : `window.JARVIS.trace()` (les 50 dernières décisions),
`output()`, `explain(forme)`, `metrics()`, `profile()`.

## Mesures

Panneau, modèle installé (36 Mo, 52 ARKit, 14 visèmes), 320×520 :

| | |
|---|---|
| cadence | 63–64 i/s |
| image complète (moteur + rendu), en parlant | 1.03 ms (féminin et masculin) |
| moteur seul, en parlant | 0.19–0.24 ms (rig 0.06, gestes 0.04–0.07, bouche 0.05) |
| écritures de formes par image | ~4.5 (52+ avant : chaque forme réécrite, identique) |
| moteur sous Node, en parlant | 15 µs par image ; 11 µs en moyenne sur 20 min simulées |
| mémoire sur 20 min simulées | tas +4 Mo |

(Mesuré le 23/09/2026 après la passe « comportement » : conversation,
clignements, habituation — aucune régression.)

Ce qui a été retiré de la boucle : la réécriture des 52 formes à chaque image,
l'objet cible recréé par image, l'accumulateur de gestes recréé par image, la
micro-expression recréée par image.

## Le visage masculin — installé, calibré, pas actif

`jarvis/male/male.glb` : **Microsoft Rocketbox `Male_Adult_01`** (licence MIT,
© 2020 Microsoft, `LICENSE.md` à côté du fichier), 52/52 ARKit, 15 visèmes
Oculus, yeux et tête à os (`Bip01_*`), quatre membres. Converti depuis son FBX
par three.js r169 (FBXLoader → GLTFExporter) ; son profil dit tout — empreintes
de l'original et des textures, transformations, restrictions.

Deux choses qu'il a apprises au pipeline, et qui servent à tout modèle :

- la convention Biped de 3ds Max (`Bip01_Head`) : l'inspecteur ne la connaissait
  pas, le moteur si — l'inspecteur annonçait une tête absente. Les deux tables
  d'os sont désormais comparées dans les deux sens (`presence/selftest.py`) ;
- la **calibration d'amplitude** (`model.morphGain`, dans le profil) : son
  sourire à 1.0 déplace les coins de 4,4 mm (le visage féminin : 27 mm), et
  `happy 0.7` y était invisible. Gain ×2.5 sur le sourire, ×1.8 sur les coins
  abaissés, appliqué au bord du fichier par `body_gltf.js`, borné à 2.5.

Toute la batterie passe avec lui actif. Il n'est pas le visage livré : ce choix
appartient à Teddy, et tient en une commande —
`python -m presence.install_model --use jarvis/male`.
`avatar/checks/capture_faces.py` photographie les neuf états des deux visages
(`checks/shots/comparaison_feminin_masculin.png`).

## Le modèle, et le suivant

Le moteur ne demande jamais « as-tu un os `LeftEye` ? », seulement « sais-tu
regarder ? ». `profile.js` pose ces questions au corps chargé et affiche la
réponse **mesurée** — le labo, le journal et le client la montrent :

```
MODEL      JARVIS (visage feminin)
FORMAT     GLB
FACE       52/52 ARKit
EYES       ✓ formes
HEAD       ✓
VISEMES    ✓ natifs (14 Oculus)
BODY       arms/head/legs/torso — tenus : arms, legs, torso
MOTION     face
ANIMATIONS 0
GESTES     8 : idle, look_at_user, look_away, look_around, nod, shake_head, tilt_head, blink_slow
MUET       Humantongue01
```

Rien n'y est supposé : un os de tête absent donne `HEAD ✗` (et `nod` rapporte
`absent`), deux visèmes isolés ne font pas `oculus`, un VRM sans noms ARKit
affiche ses expressions et non « 27 formes ARKit ».

Installer et changer de visage : voir `models/README.md`. La calibration de
chaque modèle vit dans **son** profil ; changer de visage la range et la rend.

`checks/asset_robustness.py` charge 15 modèles incomplets dans le vrai labo —
sans blendshapes, partiels, quatre conventions de noms, sans yeux, humanoïde sans
tête, tête sans os, squelette Mixamo, texture introuvable, animations, VRM,
VRM non conforme, GLB compressé KTX2 + meshopt — et exige pour chacun un profil
honnête et zéro valeur invalide écrite. Un VRM refusé par three-vrm (os exigés
absents) est relu en glTF simple au lieu d'être perdu.

## Le manifeste

Le seul fichier que lisent le moteur et le directeur. **Écrit par
`presence.install_model`** : la calibration vient du profil du modèle actif, les
préférences ne bougent jamais.

```jsonc
{
  "version": 2,
  "model": {
    "file": "jarvis/female/jarvis.glb",          // dans models/
    "profile": "jarvis/female/jarvis.model.json",
    "morphAliases": {},                          // calibration : ce que CE mesh appelle chaque forme
    "muteMeshes": ["tongue01"],                  // calibration : défauts de l'asset
    "scale": 1.0, "position": [0, 0, 0]
  },
  "rig": {
    "parts": ["arms","head","legs","torso"],     // mesuré : ce que le corps A
    "motion": "face",                            // préférence : ce qu'on lui fait FAIRE
    "bones": { "head": "Head" },                 // mesuré, ou forcé à la main
    "armRest": { "shoulder": -0.45 },            // calibration, mesurée à la main
    "gaze": { "signY": 1 }                       // calibration, si des yeux à os s'inversent
  },
  "camera": { "fov": 24, "frame": "portrait" },  // préférence : face | portrait | bust | full
  "look": { … },                                 // préférence
  "gestures": {}                                 // voir gestures/README.md
}
```

`rig.parts` dit ce que le modèle **a** ; `rig.motion` ce qu'on **bouge**.

**`muteMeshes` mérite un mot.** Sur le modèle installé, le maillage `tongue01`
porte son propre `jawOpen` — qui pousse la langue **hors de la bouche**. C'est
un défaut d'asset : il se corrige dans la calibration de ce modèle, jamais dans
le moteur. La comparaison se fait par suffixe (three.js retire les points :
`Human.tongue01` devient `Humantongue01`), et c'est aussi pourquoi cette
sourdine ne doit JAMAIS s'appliquer à un autre modèle — ce qui arrivait quand on
en installait un nouveau.

## Le cadrage et la lumière — `stage.js`

**La caméra est à hauteur des yeux.** Elle était au centre de la zone cadrée ;
en cadrage `bust`, c'est la poitrine : JARVIS regardant « l'utilisateur »
regardait droit devant, donc au-dessus de la caméra. Il ne regardait jamais la
personne en face. Le cadrage est maintenant obtenu par un décentrement optique
(la chambre photographique, pas une inclinaison) : les verticales restent
droites, et un regard droit devant arrive dans l'objectif.

**`portrait`** (tête et épaules) est le cadrage d'un humanoïde en mode visage :
en `bust`, un corps debout montrait sa taille et ses bras en pose A, et le visage
occupait un dixième d'un panneau de 300 px. Les yeux sont placés au tiers
supérieur de l'image, quelle que soit sa forme — colonne étroite ou bandeau.

La lumière : une clé légèrement chaude pour le relief, une douce froide côté
opposé pour que l'ombre de la clé ne soit pas un mur (un visage à moitié noir se
lit comme hostile), un liseré pour détacher du fond, et l'environnement
procédural à 0.45 pour que la peau ait de la matière. Exposition 0.72, mesurée.

## Le mode visage — le corps est là, on ne le bouge pas

C'est le réglage livré (`"motion": "face"`), et ce n'est pas un repli. Un
humanoïde téléchargé arrive avec des bras et rien pour les animer : offrir
`wave` sans clip, c'est saluer avec la nuque.

| | en mode `face` |
|---|---|
| visage, bouche | actifs — expressions, visèmes, micro-expressions, accents |
| yeux, regard | actifs — saccades, coups d'œil, compensation de tête |
| tête, nuque | actives — gestes, inclinaison d'état, dérive du repos |
| torse, hanches | **tenus** — aucune dérive, aucun report du poids |
| bras, mains, jambes | **tenus** à la pose de repos (`rig.armRest`) |
| épaules | une respiration, et rien d'autre |

Deux moitiés : `presence/catalog.py` retire les gestes du corps du vocabulaire
(ils ne sont plus demandés), `gestures.js` remet les canaux sous la nuque à zéro
et **refuse** de jouer un geste de corps (`frozen`, et il le dit). Mesuré : corps
2.22° → **0.00°**, tête vivante (`checks/face_first.py`).

## Préparer `"motion": "full"`

Rien n'est à changer dans le cerveau le jour où les clips arrivent :

- les intentions demandent déjà le geste d'un corps complet (`greet` → `wave`),
  et `FALLBACK_CHAIN` le rabat tant que le corps ne peut pas ;
- installer un clip = un `.glb` dans `gestures/` + une ligne dans le manifeste ;
- un clip `idle` installé devient la **couche de fond en boucle** ; chaque geste
  se fond par-dessus et le corps revient à lui à la fin (sinon : pose de repos) ;
- les accents faciaux restent et s'ajoutent au geste du corps.

`chain_test.py` joue déjà les seize intentions en mode complet : le visage et le
regard sont identiques à ceux du mode visage, sept intentions gagnent un geste de
buste. L'ordre voulu pour les clips : `idle` d'abord, puis `wave`, `point`,
`explain` ; ensuite `thinking`, `facepalm`, `nod`, `shake_head`.

## Et le téléphone

Ce qui est déjà portable tel quel dans une `WebView` :

| | portable | pourquoi |
|---|---|---|
| `engine.js` et tout le moteur | **oui** | ni DOM, ni three.js, ni transport |
| `stage.js`, `body_*.js`, `profile.js` | **oui** | three.js + WebGL, disponibles dans une WebView |
| `bridge.js` | **oui** | `window.JARVIS.perform()` ou `postMessage` |
| `main.js` | oui, à la marge | lit `window.JARVIS_MANIFEST` injecté par l'hôte |
| `lab.html` | inutile sur téléphone | outil de poste |
| `client_desktop/ui/avatar_view.py` | **non** | l'hôte Qt : injection du manifeste, `runJavaScript` |
| `client_desktop/ui/avatar_scheme.py` | **non** | le schéma `jarvis://` ; `WebViewAssetLoader` joue ce rôle sur Android |
| le Director | à décider | côté client en Python aujourd'hui ; sur téléphone, soit le serveur résout, soit `director.js` (déjà confronté au Python) |

La mise en page est calculée depuis la boîte englobante du modèle et la forme du
canevas, pas depuis des points de rupture.

## Trois choses apprises en construisant ça

**Les noms sont tout le problème.** `facecap.glb` est une vraie tête
photoscannée avec les 52 blendshapes ARKit. Le premier matcher en reconnaissait
16, parce qu'elle les appelle `browDown_L`. La règle vit dans `normalise()`, des
deux côtés, testée sur 13 conventions d'export réelles.

**Les assets du commerce sont compressés.** KTX2, DRACO, meshopt : sans les trois
transcodeurs, un `.glb` acheté échoue comme un fichier manquant. Ils sont
vendorisés dans `vendor/`.

**Sous `jarvis://`, `fetch` ne marche pas.** QtWebEngine 6.7 n'en garde aucun
drapeau ; `xhr_loader.js` remplace le chargeur de three.js par une
implémentation XHR.
