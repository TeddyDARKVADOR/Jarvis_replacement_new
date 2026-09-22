# `presence/` — le cerveau du corps de JARVIS

Paquet autonome. **Il n'importe rien de `main.py`, `ui.py`, `core/`, `dashboard/`,
`actions/`, `server/` ou `client_desktop/`** — vérifié automatiquement par le test.
Supprimer le dossier rend JARVIS identique à ce qu'il était : une voix avec un
cœur 2D.

```
python -m presence.selftest                      26 contrôles, sans clé, sans micro, sans GPU
python -m presence.install_model --demo          installer un modèle qui marche
python -m presence.install_model --list          où trouver un vrai personnage
python -m presence.inspect                       ce qu'il y a réellement dans le modèle
```

## L'idée en une phrase

**JARVIS choisit en mots, jamais en sommets.** Il dit `surprised, 0.7,
look_at_user` ; le reste du système traduit ça en 52 coefficients ARKit, puis en
ce que *ce mesh-là* appelle ces 52 formes. C'est la seule raison pour laquelle on
peut changer de modèle 3D sans toucher à une ligne de décision.

```
       situation reçue
              │
              ▼
      JARVIS analyse ─────────────► directive en mots (facultative)
              │                      {"expression":"amused","gesture":"tilt_head"}
              ▼
        director.py  ◄──── réflexe : ce que l'état implique, toujours disponible
              │
              ▼
        Performance  ─── expression · intensité · geste · regard · posture
              │           + les 52 poids ARKit déjà résolus
              ▼
      avatar/ (three.js)
              │
     ┌────────┼────────┐
     ▼        ▼        ▼
  visage    corps    bouche
```

## Les six fichiers

| Fichier | Rôle | Dépendances |
|---|---|---|
| `model.py` | le vocabulaire : `Expression`, `Gesture`, `Gaze`, `Posture`, `Performance` | aucune |
| `vocabulary.py` | expression + intensité → 52 coefficients ARKit | `model` |
| `catalog.py` | ce qui est **réellement** installé, lu dans `avatar/manifest.json` | `model` |
| `director.py` | état + intention → une `Performance` jouable | `model`, `catalog`, `vocabulary` |
| `inspect.py` | ce qu'un `.glb` / `.vrm` contient vraiment, et le manifeste qui en découle | `model`, `vocabulary` |
| `install_model.py` | installer un personnage en une commande | `inspect` |

## Réflexe et intention

Deux sources de comportement, dont **une est toujours disponible**.

| | d'où ça vient | coût | ce que ça apporte |
|---|---|---|---|
| **réflexe** | l'état machine seul (`LISTENING`, `THINKING`, …) | nul | jamais faux, jamais intéressant |
| **intention** | JARVIS, en mots, parce que la phrase le mérite | quelques jetons | le corps a l'air d'avoir compris |

L'intention prime pendant `INTENT_TTL_S` (25 s), puis rend la main. La plupart
des tours n'en produisent pas, et c'est voulu : un modèle qui décrit son visage
à chaque phrase dépense ses jetons en grimaces.

Le bloc que JARVIS peut ajouter en fin de réponse :

````
```jarvis-presence
{"expression": "amused", "intensity": 0.35, "gesture": "tilt_head",
 "gaze": "user", "reason": "il a relancé la même commande"}
```
````

`director.strip()` le retire du texte avant qu'il soit lu à voix haute —
c'est le seul vrai risque de ce format.

## Le vocabulaire est plus large que ce qui est installé

`Gesture` liste **30 gestes** : ce que JARVIS a le droit de *vouloir*.
`catalog.py` dit ce que ce corps-ci peut faire aujourd'hui, et `FALLBACK_CHAIN`
rend l'écart inoffensif :

```
facepalm  →  shake_head  →  look_away  →  idle
```

Chaque étape garde l'*intention* et n'abandonne que le moyen. `facepalm →
shake_head` garde le « non » ; `facepalm → wave` ne garderait rien.

La `Performance` transporte `requested_gesture` quand une substitution a eu
lieu, donc la fenêtre de debug peut dire *« a demandé facepalm, a joué
shake_head, facepalm n'est pas installé »* — la ligne la plus utile qui soit
quand on ajoute des clips.

## Installer un personnage

```bash
python -m presence.install_model --demo                    # tête humaine, 52 ARKit
python -m presence.install_model --readyplayerme <id>      # corps entier + ARKit + visèmes
python -m presence.install_model C:/Downloads/aven.glb     # un asset acheté
python -m presence.install_model mon_avatar.vrm            # un VRM (VRoid Studio)
```

La commande télécharge, inspecte le fichier **lui-même**, et écrit dans
`avatar/manifest.json` ce qu'elle y a trouvé : les alias de blendshapes, les
membres réellement présents, les os. Ce qu'un humain garde la main dessus —
caméra, couleurs, échelle — n'est pas touché.

> Les modèles ne sont **pas** versionnés (`.gitignore`) : ils appartiennent à
> quelqu'un d'autre et pèsent plusieurs mégaoctets. Le manifeste, lui, l'est.

## Ce qui est vérifié, et pourquoi

Le test ne regarde pas seulement le Python. Cinq tables existent **des deux
côtés** de la frontière JavaScript, parce que le navigateur doit fonctionner
sans Python — c'est toute la raison d'être du labo. Une copie que personne ne
vérifie dérive, donc `selftest.py` **analyse les fichiers `.js`** et échoue à la
moindre différence :

- les 52 noms ARKit (`avatar/js/arkit.js`)
- les 8 visèmes (`avatar/js/visemes.js`)
- les 12 expressions, 6 regards, 7 familles de courbe (`avatar/js/expressions.js`)
- les 30 gestes et 5 postures (`avatar/js/gestures.js`, `avatar/js/lab.js`)
- **la règle de normalisation des noms**, sur 13 conventions d'export réelles

Cette dernière est la plus importante : c'est elle qui décide si un modèle
acheté est exploitable ou pas. `browDown_L` et `browDownLeft` sont la même
forme, et un matcher qui l'ignore jette 36 blendshapes sur 52 — à cause d'un
tiret bas.
