# `presence/` — le cerveau du corps de JARVIS

Paquet autonome. **Il n'importe rien de `main.py`, `ui.py`, `core/`, `dashboard/`,
`actions/`, `server/` ou `client_desktop/`** — vérifié automatiquement par le test.
Supprimer le dossier rend JARVIS identique à ce qu'il était : une voix avec un
cœur 2D.

```
python -m presence.selftest                      37 contrôles, sans clé, sans micro, sans GPU
python -m presence.install_model --demo          installer un modèle qui marche
python -m presence.install_model --list          où trouver un vrai personnage
python -m presence.inspect                       ce qu'il y a réellement dans le modèle
```

## L'idée en une phrase

**JARVIS décrit un état ; le comportement en découle.** Il ne nomme pas un
visage et ne touche jamais un sommet. Il dit *où il en est* — six nombres — et
le visage, le regard, la posture, la vitesse des gestes et jusqu'à l'immobilité
du repos se calculent. C'est la seule raison pour laquelle on peut changer de
modèle 3D sans toucher à une ligne de décision.

```
       situation reçue
              │
              ▼
      JARVIS analyse ──────► état intérieur (préféré)
              │              {"emotion":{"valence":0.45,"arousal":0.25},
              │               "attention":0.91,"confidence":0.76,"urgency":0.12}
              │
              │              ou, quand il veut CE visage-là et aucun autre :
              │              {"expression":"serious","intensity":0.8}
              ▼
         affect.py  ─── valence · arousal · attention · confidence · urgency
              │          ↓ dérive
              │         visage · intensité · regard · posture
              │         tempo · immobilité · tenue du regard
              ▼
        director.py  ◄── réflexe : ce que l'état machine implique,
              │           toujours disponible, jamais intéressant
              ▼
        Performance  ─── tout ce qui précède, + les 52 poids ARKit résolus
              │
              ▼
      avatar/ (three.js)
              │
     ┌────────┼────────┬────────┐
     ▼        ▼        ▼        ▼
  visage    corps    bouche   repos
                              (idle.js — respiration, dérive,
                               report du poids, micro-expressions)
```

### Pourquoi un état plutôt qu'une émotion

`Expression.AMUSED` est un point. Un état intérieur est un endroit dans un
espace, et la différence n'est pas académique : deux JARVIS également « amusés »
ne se comportent pas pareil si l'un est attentif et sûr de lui et l'autre
distrait et hésitant. Avec une seule énumération, ces deux-là sont le même
visage et chaque nuance entre eux doit être écrite à la main.

Avec six nombres, le comportement est un **calcul**. En ajouter un est de
l'arithmétique, pas une branche de plus.

| Axe | de | à |
|---|---|---|
| `valence` | désagréable −1 | agréable +1 |
| `arousal` | calme 0 | activé 1 |
| `attention` | ailleurs 0 | sur l'utilisateur 1 |
| `confidence` | hésitant 0 | assuré 1 |
| `urgency` | rien ne presse 0 | il faut agir 1 |
| `social_mode` | `formal` · `professional` · `casual` · `intimate` | plafonne l'expression |

Les douze expressions sont des **ancres** dans cet espace ; choisir un visage
est une recherche du plus proche voisin pondéré. Ajouter une treizième
expression, c'est ajouter une ancre — pas rouvrir la logique.

Le registre n'est pas de la censure, c'est de la justesse : un sourire à 0.9
pendant une confirmation de suppression n'est pas « expressif », il est faux.

### Trois couches, et l'ordre compte

| | d'où ça vient | durée de vie |
|---|---|---|
| **réflexe** | l'état machine seul (`LISTENING`, `THINKING`, …) | permanent |
| **affect** | JARVIS décrit son état | **décroît** vers une base, demi-vie 22 s |
| **intention** | JARVIS nomme un visage | expire d'un coup, 25 s |

L'affect écrase le réflexe parce qu'un état intérieur en sait plus qu'un état
machine. L'intention écrase l'affect parce qu'une décision doit pouvoir
contredire une tendance — sinon JARVIS ne peut pas être sérieux au milieu d'une
bonne humeur, ce qui arrive tout le temps.

Et l'affect **décroît** au lieu d'expirer : un JARVIS qui passe de préoccupé à
parfaitement neutre en une image a l'air d'avoir redémarré ; le même qui y
revient en quarante secondes a l'air de s'être calmé.

## Les sept fichiers

| Fichier | Rôle | Dépendances |
|---|---|---|
| `model.py` | le vocabulaire : `Expression`, `Gesture`, `Gaze`, `Posture`, `Performance` | aucune |
| `affect.py` | l'état continu, et tout ce qui s'en dérive | `model` |
| `vocabulary.py` | expression + intensité → 52 coefficients ARKit | `model` |
| `catalog.py` | ce qui est **réellement** installé, lu dans `avatar/manifest.json` | `model` |
| `director.py` | état + intention → une `Performance` jouable | `model`, `catalog`, `vocabulary` |
| `inspect.py` | ce qu'un `.glb` / `.vrm` contient vraiment, et le manifeste qui en découle | `model`, `vocabulary` |
| `install_model.py` | installer un personnage en une commande | `inspect` |

## Les deux formes que JARVIS peut émettre

Le bloc est **facultatif** et doit le rester : sans lui, le corps suit l'état
machine, ce qui est correct la plupart du temps. Un modèle qui décrit son visage
à chaque phrase dépense ses jetons en grimaces.

**Forme 1 — décrire son état** (celle que le prompt présente en premier)

````
```jarvis-presence
{"emotion": {"valence": 0.45, "arousal": 0.25},
 "attention": 0.91, "confidence": 0.76, "urgency": 0.12,
 "socialMode": "professional",
 "gesture": "tilt_head",
 "reason": "il a relancé la même commande"}
```
````

**Forme 2 — nommer un visage**, quand il veut celui-là et aucun autre.

````
```jarvis-presence
{"expression": "serious", "intensity": 0.8, "gesture": "nod",
 "reason": "action irréversible"}
```
````

Le lecteur accepte aussi la forme plate (`{"valence": …}`), `social_mode` en
snake_case, `emotion` comme simple mot, et l'objet nu sans bloc — parce que les
modèles produisent tout ça. Ce qu'il ne fait jamais, c'est **inventer** un état :
rien de reconnaissable donne `None`, et JARVIS retombe sur le réflexe.

`director.strip()` retire le bloc du texte avant qu'il soit lu à voix haute —
c'est le seul vrai risque de ce format.

> Un piège que le test garde : une directive qui ne portait **que** de l'affect
> a quand même `expression=NEUTRAL` et `intensity=0.5` dans ses champs non
> renseignés. Appliqués après la dérivation, ces défauts effacent exactement ce
> que la directive exprimait — et le symptôme est « la forme état ne fait
> rien », sans erreur nulle part.

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

La commande télécharge, inspecte le fichier **lui-même**, affiche un barème
— corps entier, rig facial, visèmes, yeux, animations — et écrit dans
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
- les 12 ancres, 27 seuils et 4 registres de l'affect (`avatar/js/affect.js`)
- les 30 gestes, 16 procéduraux, 30 exigences de rig, 5 postures
- **la règle de normalisation des noms**, sur 13 conventions d'export réelles

Le test lit les tables ; il ne peut pas exécuter le JavaScript. Cet écart a été
comblé séparément : les deux dérivations ont tourné **côte à côte sur 20 000
points** de l'espace affectif — 140 000 comparaisons, aucun désaccord
(`scratchpad/bench_affect_parity.py`). C'est pour y arriver qu'aucun des deux
côtés n'arrondit plus : l'arrondi est une affaire de présentation, et l'avoir
dans le calcul rendait la comparaison exacte impossible.

Cette dernière est la plus importante : c'est elle qui décide si un modèle
acheté est exploitable ou pas. `browDown_L` et `browDownLeft` sont la même
forme, et un matcher qui l'ignore jette 36 blendshapes sur 52 — à cause d'un
tiret bas.
