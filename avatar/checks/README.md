# `avatar/checks/` — ce que le selftest ne peut pas vérifier

`python -m presence.selftest` tourne **sans GPU, sans navigateur, sans écran**,
et c'est une propriété qu'on garde : c'est ce qui permet de le lancer partout,
tout le temps, y compris sur le VPS.

Le prix est qu'il ne peut ni exécuter le JavaScript, ni rendre une image, ni
mesurer un mouvement. Il lit les tables et il compare les sources. Ces cinq
vérifications-là comblent le reste, et demandent `PyQt6-WebEngine`.

```bash
pip install PyQt6-WebEngine

python avatar/checks/adapter_contract.py    # le modèle est-il interchangeable
python avatar/checks/affect_parity.py       # Python et JS décident-ils pareil
python avatar/checks/behaviour.py           # le labo dérive-t-il comme Python
python avatar/checks/idle_motion.py         # le repos bouge-t-il vraiment
python avatar/checks/face_first.py          # le corps tient-il vraiment en place
```

Elles ne sont **pas** un garde-fou permanent : rien ne les lance à votre place.
Elles sont à relancer après avoir touché ce qu'elles couvrent, et le code le dit
à l'endroit concerné plutôt que de vous laisser le deviner.

## Ce que chacune prouve

### `adapter_contract.py` — le modèle est interchangeable

Construit un rig humanoïde **en mémoire** — os nommés comme Ready Player Me,
zéro blendshape — lui envoie des poids ARKit, et vérifie que les os des yeux ont
tourné.

C'est la couture qui compte : le moteur comportemental écrit `eyeLookInLeft`,
l'adaptateur décide que ce modèle-ci n'a pas cette forme et fait tourner
`LeftEye` à la place. Douze vérifications, dont les trois qui font la
différence :

- les deux yeux partent du **même** côté (« in » à gauche, « out » à droite)
- le retour au repos est **exactement** zéro, pas « presque »
- un modèle qui a les formes ne bascule **pas** sur les os — elles sont plus
  fines, et leur auteur les a réglées lui-même

Aucun modèle téléchargé n'est nécessaire, et c'est voulu : la couture doit être
vérifiable avant d'avoir choisi le corps.

### `affect_parity.py` — les deux cerveaux décident pareil

`presence/affect.py` et `avatar/js/affect.js` dérivent le même comportement. Le
selftest compare leurs **tables** ; celui-ci compare leurs **résultats**, sur
20 000 points de l'espace affectif — 140 000 comparaisons, égalité exacte
attendue.

C'est en écrivant cette vérification qu'on a découvert 6 300 désaccords d'un
millième, dus à l'arrondi : `round()` de Python arrondit au pair, `Math.round`
vers le haut, et multiplier par 100 avant d'arrondir introduit sa propre erreur.
Corrigé à la racine — l'arrondi est sorti du calcul et ne vit plus qu'au bord du
fil. **Le remettre dans une dérivation casse cette vérification**, ce qui est
exactement ce qu'on veut.

### `behaviour.py` — le labo dit la vérité

Décrit six situations au laboratoire comportemental, et compare sa dérivation à
celle de Python, champ par champ. Un labo qui montre autre chose que ce que le
panneau jouera est pire qu'absent.

Puis les **seize intentions**, une par une. Le selftest compare les deux tables
`INTENTS` au repos ; celui-ci les confronte **en marche** — et il vérifie le
geste et le regard, qui ne se dérivent pas de l'affect. Ce sont eux qui prouvent
que c'est bien la table du JavaScript qui a parlé, et pas une dérivation tombée
juste par hasard.

### `idle_motion.py` — le repos est vivant

Échantillonne la rotation de la tête pendant neuf secondes, à deux états
intérieurs opposés, et mesure l'amplitude. Un repos ne se juge pas sur une image
fixe.

Attendu : chaque axe croît avec l'agitation, et le tempo suit.

| | Rx | Ry | Rz | immobilité | tempo |
|---|---|---|---|---|---|
| calme | ~1.4° | ~1.9° | ~0.1° | 0.94 | 0.76 |
| agité | ~2.5° | ~3.0° | ~0.9° | 0.40 | 1.59 |

Les valeurs bougent d'un run à l'autre — le bruit est aléatoire par
construction. C'est l'**écart entre les deux lignes** qui est la mesure, pas les
chiffres eux-mêmes.

### `face_first.py` — le corps tient vraiment en place

`rig.motion: "face"` gèle tout ce qui est sous la nuque. La moitié Python se
vérifie sans navigateur — `presence/selftest.py` prouve que le vocabulaire
rétrécit aux huit mouvements de tête, donc que les gestes de corps ne sont plus
*demandés*.

L'autre moitié ne se vérifie qu'ici. Quatre couches écrivent encore sur les
mêmes os — la posture, le regard, le repos, et le labo qui peut tout jouer à la
main. Un gel qui n'attraperait que les gestes laisserait le buste dériver et les
hanches se balancer, ce qui est exactement ce qu'on a décidé de ne pas faire.

Donc on mesure les os, pas les intentions :

| | tête | corps | épaule |
|---|---|---|---|
| `"full"` | 2.98° | 2.22° | 1.37° |
| `"face"` | 2.92° | **0.00°** | 1.38° |

Trois assertions, et la deuxième est celle qui mérite d'exister : le corps doit
être immobile en mode visage, **et** avoir bougé en mode complet. Sans elle, le
contrôle passerait tout aussi bien sur un corps qui ne bougeait de toute façon
jamais — c'est-à-dire précisément le bug qu'on vient de corriger dans
`gestures.js`.

La troisième vérifie que la tête, elle, vit encore : geler le corps en gelant
tout serait la façon la plus simple de faire passer les deux premières.

> Un seul chargement, et le drapeau `faceOnly` basculé en cours de route. Deux
> pages voudraient dire deux modèles chargés et deux bruits aléatoires
> différents ; ici le corps, l'état et la graine sont les mêmes des deux côtés,
> et le seul facteur qui change est celui qu'on teste.
