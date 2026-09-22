# `avatar/` — le corps de JARVIS

Un moteur three.js qui prend un **vrai personnage 3D** et lui fait jouer les
décisions que `presence/` produit. Rien ici ne fabrique le personnage : tout ici
le fait vivre.

```
avatar/
  index.html          le corps, tel que le panneau l'affiche (fond transparent)
  lab.html            le labo comportemental : état → décision → exécution
  manifest.json       QUEL modèle, QUELS gestes — le seul point d'extension
  models/             le personnage (non versionné)
  gestures/           les clips Mixamo (non versionnés)
  checks/             ce que le selftest ne peut pas verifier
                      (adaptateur, parite Python/JS, repos, labo)
  vendor/             three.js + KTX2 + DRACO + meshopt + VRM, en local
  js/
    main.js           amorçage, cadrage, boucle de rendu
    lab.js            le labo, qui importe exactement les mêmes modules
    bridge.js         la seule porte d'entrée : perform() / speak() / viseme()
    rig.js            les 52 coefficients, plus la vie qui n'est pas dedans
    lipsync.js        la bouche qui suit la voix
    gestures.js       clips Mixamo OU arithmétique sur trois os
    body_gltf.js      charge un .glb/.gltf, reconnaît ses blendshapes
    body_vrm.js       charge un .vrm, traduit ARKit → expressions VRM
    body_procedural.js  le dernier recours, quand aucun modèle n'est installé
    idle.js           le repos : respiration, dérive, report du poids,
                      micro-expressions — paramétré par l'état intérieur
    affect.js         la dérivation du comportement (généré depuis presence/affect.py)
    expressions.js    les 12 visages (généré depuis presence/vocabulary.py)
    arkit.js          les 52 noms
    visemes.js        les 8 formes de bouche
    xhr_loader.js     un correctif de transport, expliqué plus bas
```

## Démarrer

```bash
python -m presence.install_model --demo     # une tête humaine, 52 blendshapes ARKit
python -m presence.selftest                 # 37 contrôles
```

Puis, dans le client de bureau, activer `avatar_enabled` dans les réglages.
Le cœur 2D reste le défaut : l'avatar est un processus Chromium et un contexte
GPU pour toute la durée de la session, ce qui est un prix juste pour un visage
et un mauvais prix pour une machine qui compile.

### Qui décide de ce que ce corps fait

Deux sources, et la première suffit.

**Le réflexe** est résolu sur le client, à chaque changement d'état — écoute,
réflexion, parole — par `presence.Director`, sans que le serveur y soit pour
quoi que ce soit. Un JARVIS branché sur un Oracle qui n'a jamais entendu parler
de `presence/` a donc déjà un visage qui vit.

**L'intention** est ce que JARVIS choisit pour *cette phrase-là*. Elle arrive du
serveur en un événement `avatar` sur `/ws`, parce qu'il l'a demandée par un
appel d'outil — `plugins/presence.py`. Le chemin complet, et la raison pour
laquelle ce n'est pas le bloc clos que `presence/README.md` décrit, sont dans
ce même fichier, section « Par où la directive arrive vraiment ».

### Le labo comportemental

Ouvrir `avatar/lab.html`. Il ne montre pas seulement ce que le moteur a exécuté,
mais **ce que JARVIS a décidé**, et le chemin de l'un à l'autre :

```
SITUATION         « J'ai trouvé quelque chose d'intéressant. »
ÉTAT INTÉRIEUR    valence +0.35 · arousal 0.55 · attention 0.92 · confiance 0.70
DÉCISION          amused 0.56 · regard user · posture attentive
                  tempo 0.97 · immobilité 0.66        ← dérivé de l'état
EXÉCUTION         ✓ 8 formes · ✓ regard · ✓ posture · ✓ geste tilt_head
```

Cinq curseurs pour l'état, un sélecteur de registre, et tout le reste se
calcule. Forcer un visage court-circuite la dérivation — le bandeau le dit.

Il répond aussi aux questions que le panneau cache :

- ce fichier se charge-t-il, et en combien de temps
- quels morph targets a-t-il, et lesquels ont été reconnus comme ARKit
- quelles animations porte-t-il
- que fait la forme `browInnerUp` — **un curseur par blendshape**
- à quoi ressemble *cette* décision JARVIS — coller le JSON, les deux formes

La ligne **exécution** est celle qui se gagne le plus : c'est elle qui dit
`✗ facepalm injouable → shake_head` quand un clip manque. Sans elle, ce genre de
substitution est parfaitement invisible.

On peut y **glisser un `.glb`, `.gltf` ou `.vrm`** pour le juger avant de
l'installer. Rien n'est écrit sur le disque : installer est une décision, et
c'est `presence.install_model` qui la prend.

> Ouvert directement en `file://`, le labo ne démarrera pas : les modules ES
> sont bloqués sur une origine opaque. Servir le dossier —
> `python -m http.server -d avatar 8777` — ou l'ouvrir depuis le client, qui
> utilise le schéma `jarvis://`.

## Le manifeste

Le seul fichier à connaître. **Écrit par `presence.install_model`**, pas à la
main — sauf les champs que seul le goût décide.

```jsonc
{
  "model": {
    "file": "jarvis.glb",         // dans models/ ; "" = corps procédural
    "scale": 1.0,
    "morphAliases": {             // dérivé : ce que CE mesh appelle chaque forme
      "browDownLeft": "browDown_L"
    },
    "muteMeshes": ["tongue01"]    // à la main : défauts de l'asset, voir plus bas
  },
  "rig": {
    "parts": ["arms","head","legs","torso"],  // dérivé
    "bones": { "head": "Head" },              // dérivé
    "armRest": { "shoulder": -0.45 },         // à la main, mesuré
    "gaze": { "signY": 1 }                    // à la main, si les yeux s'inversent
  },
  "camera": { "fov": 24, "frame": "bust" },   // "face" | "bust" | "full"
  "gestures": {}                              // voir gestures/README.md
}
```

### Les trois champs qu'aucune inspection ne peut deviner

`morphAliases`, `parts` et `bones` sont **dérivés** du fichier. Les trois
suivants sont des jugements, et ils existent parce qu'un modèle réel n'est
jamais parfait :

| champ | quand s'en servir |
|---|---|
| `model.muteMeshes` | un maillage porte une forme correctement nommée et **mal transférée** |
| `rig.armRest.shoulder` | les bras ne tombent pas naturellement (valeur négative si le rig est livré en pose A) |
| `rig.gaze.signY` / `signX` | les yeux partent du mauvais côté, sur un modèle dont les yeux sont des os |

**`muteMeshes` mérite un mot**, parce que le symptôme est déroutant. Sur
l'avatar installé, le maillage `tongue01` porte son propre `jawOpen` — mais il
ne fait pas suivre la langue à la mâchoire, il la pousse **hors de la bouche**.
Le moteur écrit `jawOpen` pour parler, et JARVIS tire la langue à chaque phrase.

Rien dans le système n'est fautif : le nom est bon, la forme existe, elle
déforme bien quelque chose. C'est l'asset qui est mal fait — et un défaut
d'asset se corrige dans le manifeste, jamais dans le moteur, qui n'a pas à
connaître l'existence d'un maillage appelé `tongue01`. Les dents, elles, ont
aussi `jawOpen` et le font correctement : d'où une liste et non une règle.

> La comparaison se fait par **suffixe**, parce que three.js supprime les points
> des noms (ils sont réservés dans sa syntaxe de liaison d'animation) : le nœud
> glTF `Human.tongue01` arrive comme `Humantongue01`. Le rapport du labo affiche
> ce qui a réellement été mis en sourdine — un maillage muet que personne ne
> sait muet est l'heure suivante passée à chercher pourquoi une forme n'a aucun
> effet.

`rig.parts` est ce qui décide du vocabulaire offert à JARVIS. Un corps sans bras
ne se verra jamais proposer `wave` — et ne pourra donc jamais le choisir et ne
rien faire.

## Installer un geste : deux étapes, zéro code

1. Déposer un `.glb` contenant une animation dans `gestures/`.
2. Ajouter son nom dans `gestures` du manifeste.

```jsonc
"gestures": {
  "wave":  { "clip": "wave.glb" },
  "think": { "animation": "Thinking" }   // ou un clip déjà présent dans le modèle
}
```

C'est délibérément aussi petit : l'intérêt de cette conception est le *nombre*
de gestes installables, donc installer le centième doit coûter ce que coûte le
premier.

Quinze gestes marchent **sans aucun fichier** — tout ce qu'un cou et un buste
peuvent faire est calculé (`PROCEDURAL_GESTURES`). Les clips gagnent pour tout
ce qui a des bras.

## Le repos, qui est 95 % du temps

JARVIS passe l'essentiel de sa vie **entre** deux décisions. Un corps qui ne
bouge qu'aux ordres est figé 95 % du temps, et les utilisateurs rapportent ça
comme « il a planté » — pas comme « il est calme ».

`idle.js` n'est donc pas une banque de clips (elle boucle, et elle ne sait rien
de l'état). C'est une **somme de couches continues**, chacune paramétrée par
l'affect, aux fréquences incommensurables — elle ne boucle jamais :

| couche | pilotée par |
|---|---|
| respiration | l'immobilité ralentit *et* creuse |
| micro-mouvements | dérive lente de la tête et du buste |
| report du poids | seulement si le modèle a des jambes |
| dérive du regard | le regard **revient**, il ne se verrouille pas |
| micro-expressions | brèves, < 0.12 d'amplitude — au-delà c'est une grimace |

Mesuré sur le vrai moteur, neuf secondes par état :

| | Rx | Ry | Rz | immobilité | tempo |
|---|---|---|---|---|---|
| calme | 1.42° | 1.88° | 0.12° | 0.94 | 0.76 |
| agité | 2.48° | 3.03° | 0.85° | 0.40 | 1.59 |

Les clignements ne sont **pas** ici : `rig.js` possède les paupières, avec sa
propre horloge, parce qu'un clignement lissé n'est plus un clignement.

## Trois choses apprises en construisant ça

**Les noms sont tout le problème.** `facecap.glb` est une vraie tête humaine
photoscannée avec les 52 blendshapes ARKit. Mon premier matcher en reconnaissait
16, parce qu'elle les appelle `browDown_L` et pas `browDownLeft`. Un tiret bas
rendait un asset parfait inutilisable. La règle vit maintenant dans
`normalise()`, des deux côtés, et le test la vérifie sur 13 conventions
d'export réelles.

**Les assets du commerce sont compressés.** KTX2 pour les textures, DRACO et
meshopt pour la géométrie : c'est ce que « game ready » veut dire en pratique.
Sans les trois transcodeurs, un `.glb` acheté échoue avec un message qui se lit
comme « fichier manquant ». Ils sont vendorisés dans `vendor/`.

**Sous `jarvis://`, `fetch` ne marche pas.** QtWebEngine 6.7 accepte
l'enregistrement d'un schéma personnalisé et n'en garde aucun drapeau : origine
`jarvis://` sans hôte, `isSecureContext` faux, donc jamais CORS. Les modules ES
et `XMLHttpRequest` passent, `fetch` échoue — et sur certains chemins d'appel
fait tomber le processus. `xhr_loader.js` remplace le `FileLoader` de three.js
par une implémentation XHR : un correctif, un fichier, et toute la chaîne de
chargement fonctionne.

## Et le téléphone

Le renderer est une page web. Le panneau de bureau l'héberge dans un
`QWebEngineView`, un téléphone l'hébergera dans une `WebView` — même code, même
protocole (`bridge.js`), et `WebViewAssetLoader` y joue exactement le rôle du
schéma `jarvis://`. La mise en page est déjà responsive : le cadrage est calculé
depuis la boîte englobante du modèle, pas depuis une liste de points de rupture,
donc une colonne de 240 px et une bande de 600×240 montrent toutes deux un
JARVIS entier et centré.
