# `avatar/` — le corps de JARVIS

Un moteur three.js qui prend un **vrai personnage 3D** et lui fait jouer les
décisions que `presence/` produit. Rien ici ne fabrique le personnage : tout ici
le fait vivre.

```
avatar/
  index.html          le corps, tel que le panneau l'affiche (fond transparent)
  lab.html            le labo : charger, inspecter, tester au curseur
  manifest.json       QUEL modèle, QUELS gestes — le seul point d'extension
  models/             le personnage (non versionné)
  gestures/           les clips Mixamo (non versionnés)
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
    expressions.js    les 12 visages (généré depuis presence/vocabulary.py)
    arkit.js          les 52 noms
    visemes.js        les 8 formes de bouche
    xhr_loader.js     un correctif de transport, expliqué plus bas
```

## Démarrer

```bash
python -m presence.install_model --demo     # une tête humaine, 52 blendshapes ARKit
python -m presence.selftest                 # 26 contrôles
```

Puis, dans le client de bureau, activer `avatar_enabled` dans les réglages.
Le cœur 2D reste le défaut : l'avatar est un processus Chromium et un contexte
GPU pour toute la durée de la session, ce qui est un prix juste pour un visage
et un mauvais prix pour une machine qui compile.

### Le labo

Ouvrir `avatar/lab.html`. Il répond aux questions que le panneau cache :

- ce fichier se charge-t-il, et en combien de temps
- quels morph targets a-t-il, et lesquels ont été reconnus comme ARKit
- quelles animations porte-t-il
- que fait la forme `browInnerUp` — **un curseur par blendshape**
- à quoi ressemble `surprised` à 0.7
- à quoi ressemble *cette* décision JARVIS — coller le JSON
- quelle décision s'exécute en ce moment

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
    "file": "facecap.glb",        // dans models/ ; "" = corps procédural
    "scale": 1.0,
    "morphAliases": {             // dérivé : ce que CE mesh appelle chaque forme
      "browDownLeft": "browDown_L"
    }
  },
  "rig": {
    "parts": ["head"],            // dérivé : ce que JARVIS aura le droit de vouloir
    "bones": { "head": "head" },  // dérivé
    "armRest": {}                 // à la main, si les bras tombent de travers
  },
  "camera": { "fov": 24, "frame": "face" },   // "face" | "bust" | "full"
  "gestures": {}                              // voir gestures/README.md
}
```

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
