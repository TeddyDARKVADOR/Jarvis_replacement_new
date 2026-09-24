# `avatar/checks/` — ce que le selftest ne peut pas vérifier

`python -m presence.selftest` tourne **sans GPU, sans navigateur, sans écran**,
et c'est une propriété qu'on garde : c'est ce qui permet de le lancer partout,
tout le temps, y compris sur le VPS. Le prix est qu'il ne peut pas exécuter le
JavaScript ni mesurer un mouvement : il lit les tables et compare les sources.

Deux familles comblent le reste.

**Sous Node** (≥ 18) — rapides, déterministes, sans GPU. Le vrai moteur tourne
sur un corps sans rendu (`js/body_null.js`) avec une graine fixée :

```bash
node   avatar/checks/engine_test.mjs        # le moteur, mesuré par sa sortie (42)
python avatar/checks/director_parity.py     # le labo décide-t-il comme le panneau (3 432 + 85 pas)
python avatar/checks/chain_test.py          # de set_presence au modèle, 44 chaînes + le temps + l'écoute
python avatar/checks/model_swap.py          # changer de visage sans toucher au cerveau
node   avatar/checks/situations.mjs         # les 12 moments du labo, chacun mesuré (12)
node   avatar/checks/scenario_matrix.mjs    # 1 632 situations, 9 invariants, intentions indiscernables
node   avatar/checks/naturalness.mjs [min] [graine]   # 20 min de conversation, ce qui s'y répète (10)
node   avatar/checks/host_test.mjs          # le cote telephone : etat, fraicheur, pas de rejeu (9)
```

**Dans QtWebEngine** (`pip install PyQt6-WebEngine`) — le vrai modèle, la vraie
page, le vrai GPU :

```bash
python avatar/checks/adapter_contract.py    # le modèle est-il interchangeable
python avatar/checks/affect_parity.py       # Python et JS dérivent-ils pareil
python avatar/checks/behaviour.py           # le labo joue-t-il ce que le panneau jouerait
python avatar/checks/facial_performance.py  # le temps d'un visage, sur les écritures réelles
python avatar/checks/idle_motion.py         # le repos bouge-t-il, sans aucune décision
python avatar/checks/face_first.py          # le corps tient-il vraiment en place
python avatar/checks/asset_robustness.py    # 15 modèles incomplets, aucun ne tombe
python avatar/checks/lab_situations.py      # les situations cliquées dans le vrai labo
python avatar/checks/capture_faces.py [nom] # neuf visages photographiés -> checks/shots/
python avatar/checks/framing.py [--shots]   # portrait centré, sans bras : chaque visage jarvis/, 3 tailles
```

`model_swap.py` tourne aussi dans le selftest (il n'a besoin de rien).
Les autres ne sont **pas** un garde-fou permanent : rien ne les lance à votre
place. Ils sont à relancer après avoir touché ce qu'ils couvrent.

`fixtures.py` n'est pas un contrôle : il fabrique les GLB et VRM synthétiques
dont les autres ont besoin — un triangle, et exactement ce qu'un fichier
DÉCLARE (noms de formes, os, extensions).

## Ce que chacune prouve

### `engine_test.mjs` — le moteur, par sa sortie

Quarante-deux mesures sur ce que le corps a REÇU, pas sur les tables : qu'un visage
part à vitesse nulle, que la surprise est vive et retombe, que l'ironie se
compose, que les yeux partent avant la tête et se posent, qu'ils tiennent
l'utilisateur pendant un hochement, qu'un regard décidé n'est jamais déplacé,
que la tête ne s'accumule pas, que rien ne bouge sous la nuque en mode visage,
que le sourire survit à la parole et que la parole ferme une mâchoire ouverte
par l'émotion, que la bouche se referme sans claquer, les priorités, les NaN,
l'identité des gestes, les seize intentions discernables **à la sortie**, et
qu'une séance se rejoue à l'identique.

Et, depuis la passe « comportement » (voir `avatar/BEHAVIOUR.md`) : une
décision nouvelle efface l'accent de la précédente sans le couper net ; la même
décision renvoyée ne relance pas le visage ; ce qui quitte le visage part à son
rythme, sauf devant une alerte ; le résidu d'une surprise s'efface ; `blink_slow`
ferme vraiment les paupières ; la tête marque les syllabes appuyées ; `explain`
amplifie ces appuis ; le regard s'échappe en début de phrase (jamais un regard
décidé, jamais sous `warn`/`reassure`) ; un clignement suit souvent la fin d'une
phrase ; JARVIS hoche la tête aux pauses de l'utilisateur et nulle part
ailleurs ; la surprise retient le clignement ; les clignements sont
irréguliers ; six visages restent lisibles en parlant ; les réflexes d'une
conversation s'usent. Chacun a d'abord été écrit rouge contre le moteur d'avant.

### `director_parity.py` — le labo décide comme le panneau

`js/director.js` refait `presence/director.py` pour le labo. Confrontés sur
3 432 décisions — directives de toutes formes, onze états, intention vivante, à
mi-retombée (15 s) et expirée, corps visage et complet — même JSON, champ par
champ (les nombres au millième, l'arrondi du fil). Puis cinq **séquences** (85
pas) : ce qu'une résolution isolée ne peut pas voir — le tour de parole, une
humeur qui retombe deux minutes, une intention qui en remplace une autre.

### `scenario_matrix.mjs` — chaque intention, dans chaque situation

17 intentions (et aucune) × 4 états × parole ou silence × 3 regards × urgence ×
interruption : 1 632 situations par le vrai directeur et le vrai moteur. Neuf
invariants sur la sortie (valeurs, corps tenu, **regard écrit tenu dans le
monde** — l'œil plus la tête —, visage décidé qui arrive, bouche qui parle ou se
tait, interruption qui l'emporte, geste honnête), puis la recherche des
intentions qui se jouent pareil dans un contexte donné. Elle a trouvé, à sa
première exécution, quatre pannes qu'aucun cas choisi ne voyait.

### `naturalness.mjs` — ce qui se répète en vingt minutes

Une séance simulée (écoute, réflexion, parole, silences, interruptions,
surprises, erreurs, absences), à la cadence de l'hôte. Clignements (rythme,
irrégularité, autocorrélation, causes), gestes (amplitudes, trajectoires
identiques), échappées du regard (chacune arrivée aux yeux ou reprise par une
décision — aucune perdue), appuis et hochements, micro-expressions répétées,
visages, état final, coût, mémoire. Elle ne dit pas que c'est naturel ; elle
dit que ça ne tombe dans aucun piège connu.

### `situations.mjs` et `lab_situations.py` — le labo dit vrai

Les douze moments de `js/situations.js` (quelqu'un arrive, résultat
inattendu, remerciement, plaisanterie, interruption…) : sous Node, chacun mesuré
contre ce qu'il annonce ; dans le vrai labo, chacun cliqué, sans erreur, et sa
ligne « Pourquoi » confrontée à ce que le moteur a joué.

### `chain_test.py` — de l'appel d'outil au modèle

`set_presence` → événement → trame `/ws` → `JarvisClient` → store →
`set_intent_json` → Director → repli → JSON → moteur → sortie effective, à
travers le VRAI code de chaque maillon. C'est le contrôle qui aurait vu que
**aucune intention n'atteignait le visage** : chaque maillon avait son test,
aucun ne regardait la couture.

### `model_swap.py` — le visage est interchangeable

Féminin → masculin (autre convention de noms, autres os, visèmes) → féminin →
masculin, dans un répertoire temporaire : 136 décisions identiques, calibration
rangée et rendue, préférences intactes, empreinte vérifiée.

### `asset_robustness.py` — un modèle incomplet coûte une capacité

Sans blendshapes, partiel, quatre conventions de noms, sans yeux, humanoïde sans
tête, tête sans os, squelette Mixamo, texture introuvable, animations, VRM, VRM
non conforme, GLB compressé : chacun chargé dans le vrai labo, joué, et relu —
profil honnête, zéro valeur invalide, aucune erreur JavaScript imprévue.

### `facial_performance.py` — le temps d'un visage, dans le navigateur

Intercepte l'écriture du rig — la dernière ligne avant la géométrie — et
reconstitue chaque image (le rig n'écrit que ce qui change). Chaque forme
demandée arrive (jugée au pic : la surprise retombe d'elle-même), aucune ne
saute, `amused` et `thinking` se composent, `surprised` arrive d'un bloc,
`hold_s` relâche.

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

### `behaviour.py` — le labo joue ce que le panneau jouerait

Pilote `lab.html` comme une personne — la demande collée, « Exécuter » — sur le
modèle installé, et confronte au vrai Director Python : la décision (visage,
intensité, regard et sa provenance, posture, tempo), le **repli** (le geste joué,
pas demandé : `greet` → `wave → nod`), l'accent, et la **sortie** que le moteur
rapporte (geste joué, pas gelé ni absent). Six situations, seize intentions, et
les raffinements explicites.

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
