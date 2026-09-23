# `avatar/BEHAVIOUR.md` — pourquoi le visage fait ce qu'il fait

Ce fichier consigne la passe « comportement » du 23/09/2026 : chaque signal
ajouté ou corrigé, la situation qui le justifie, comment il est testé **sur la
sortie**, et ce qui a été volontairement rejeté. Il complète `README.md` (le
moteur) et `checks/README.md` (les contrôles).

La règle suivie partout : **situation → ce qu'un humain fait → signal perçu →
durée → conflits → test sur la sortie → décision (garder, améliorer, rejeter)**.
Aucune mesure ici ne prouve qu'un visage est naturel ; elles prouvent qu'il ne
tombe pas dans les pièges qu'on sait reconnaître. Le naturel se juge au labo.

## Qui décide quoi (inchangé, et vérifié)

| | décide |
|---|---|
| JARVIS (le LLM) | pourquoi : intention, tonalité, cible du regard |
| `presence/` | quoi : visage, intensité, regard, geste et repli, accent, tour de parole |
| le moteur | comment : timing, transitions, clignements, appuis, échappées, habituation, résidus |
| le profil du modèle | ce que CE fichier sait faire, et sa calibration (noms, os, gains) |

Rien de ce qui suit n'a ajouté un mot au vocabulaire de JARVIS, ni touché
`main.py`, `core/`, `plugins/`.

## Les fiches

### Conversation — `js/conversation.js`, `js/voice.js`

| | début de phrase | pendant la phrase | fin de phrase | pause de l'utilisateur |
|---|---|---|---|---|
| **humain** | le regard s'échappe pour formuler (Kendon 1967) | la tête marque les syllabes appuyées (Munhall 2004), parfois les sourcils (Cavé 1996) | clignement fréquent (Nakano & Kitazawa 2010) | petit hochement « je suis » (Ward & Tsukahara 2000) |
| **signal** | coup d'œil latéral-haut 0.6–1.3 s | tangage 0.3–2.2°, un peu de lacet/roulis ; sourcils 0.1 | clignement causé | hochement 2.3–3.5° |
| **probabilité** | 0.5 | appui ≥ 1.12× la moyenne des sommets ; réfractaire 0.45 s | 0.55 | 0.4, réfractaire 2.5–4.5 s |
| **jamais** | regard écrit, sûreté, `warn`, `reassure`, `greet` | en silence | à coup sûr | hors écoute, pendant qu'il parle, sur un bruit stable |
| **contexte** | `explain` : permis même sous le regard de l'intention | `explain` ×1.6 ; calme (immobilité) → plus petit | — | — |
| **mesuré** | 21/40 phrases ; explain 16/30 ; warn 0, reassure 0 | 4 appuis >1° sur 6 s, tous sur une syllabe appuyée | 17/40 | 12 hochements / 36 pauses, tous dans une pause ; 0 au bruit |

La voix de l'utilisateur est son **niveau de micro** (15 Hz), transmis par
l'hôte seulement quand il a la parole (`LISTENING`, `CONFIRM`) — la voix de
JARVIS fuit dans le micro quand il parle. Le plancher de bruit suit un minimum
glissant ; une pause doit être une **chute**, pas un plancher qui rattrape un
ventilateur. Les seuils sont à l'échelle de `pcm_level()` (RMS, 60–2 600) et
**n'ont pas été calibrés sur un vrai micro** : voir « incertitudes ».

L'accent `beat` d'`explain` était une horloge (trois appuis à 0.62 s) qui
tombait à côté de la voix ; quand il parle, cette horloge se tait et ce sont
ses syllabes, amplifiées, qui marquent.

### Clignements — `js/blink.js`

- **Pourquoi** : intervalle uniforme 0.4–1.6× la moyenne → CV 0.45, jamais un
  regard qui tient plus de 1.75× la moyenne. Les intervalles humains sont
  étirés vers la droite, et beaucoup de clignements ont une cause.
- **Signal** : log-normal (σ 0.5), durée 120–170 ms, causes déclarées et
  comptées : spontané, double, changement de regard, fin de phrase, pause de
  l'utilisateur, après une surprise, geste `blink_slow`.
- **Surprise** : pas de clignement pendant 0.7 s (yeux grands ouverts), puis un
  clignement probable à la détente (7/12).
- **Mesuré** : CV 0.60, plus long intervalle 3.4× la moyenne ; sur 20 min,
  20/min, autocorrélation max 0.03 (aucune horloge).

### Habituation — `engine.js`

- **Pourquoi** : chaque tour jouait `look_at_user 4° → tilt 10° → nod 9°`, à
  l'identique, huit fois sur huit. Ce sont les réflexes des états, pas des
  décisions.
- **Règle** : une familiarité par geste (+1, demi-vie ~21 s). Un réflexe
  familier rétrécit et saute parfois un tour ; une **décision** ne descend
  jamais sous 70 %. Chaque jeu : amplitude ×0.9–1.1, durée ×0.92–1.08.
- **Mesuré** : hochement 9.6 → 4.9° (2 tours sautés sur 8) ; tilt 13.3 → 5.2° ;
  `agree` ×3 : 8.7 / 7.9 / 6.3° ; retour à 8.7° après 2 min ; 0 trajectoire
  identique sur 47 gestes en 20 min.

### Continuité émotionnelle — `performance.js`, `affect.py`

| situation | avant | après |
|---|---|---|
| un sourire s'en va vers le neutre | au rythme du neutre (0.55 s) | au sien (0.95 s) |
| un sourire sous une erreur | traîne 0.75 s pendant que l'inquiétude monte | effacé en 0.37 s |
| une humeur retombe après `warn` | concerned → serious → **thinking (yeux levés, 16 s)** → neutral | concerned (pâlit) → neutral |
| résidu de surprise, décision tenue | sourcils 0.25 pendant 20 s | 0.25 → 0.07 en ~4 s |
| la même décision renvoyée | la surprise remonte 0.27 → 0.78 | reste à 0.27 |

### Regard — `gaze.js`, `gestures.js`, `vocabulary.py`

- Un regard **décidé** (écrit, intention, sûreté) n'est plus déplacé par les
  yeux de l'expression (`think` + `gaze: user` : yeux à y = +0.17 avant).
- Les mouvements de tête faits **en regardant** (hocher, se détourner avec un
  regard décidé, accents, appuis de parole) sont compensés à 96 % (réflexe
  vestibulo-oculaire) ; la dérive lente du repos garde 75 % pour la vie.
  `investigate` + `gaze: user` manquait l'utilisateur de 5.7°.
- Pour un regard décidé, l'**attitude** de la tête (posture, inclinaison
  d'écoute) est compensée aussi ; un regard par défaut la suit.
- Une échappée acceptée **arrive aux yeux** : sous `explain`, 12 commandées et
  0 arrivée avant (`_glance` les remettait à zéro).

## Défauts trouvés et corrigés (tous écrits rouges d'abord)

| famille | défaut | trouvé par |
|---|---|---|
| déclaré, jamais relié | `Accents.stop()` jamais appelé : la tête de l'excuse restait basse sous `warn` | sonde |
| déclaré, jamais relié | `blink_slow` « joué » et vide (0/20 clignements lents) | ligne « Pourquoi » du labo |
| déclaré, jamais lu | `ENVELOPES.*.release` inutilisé pour la forme qui s'en va | sonde |
| calculé, jamais montré | expiration d'intention et décroissance d'affect invisibles entre deux changements d'état | sonde + chaîne temporelle |
| annoncé, inexécutable | `{"gaze": "screen"}` seul rejeté par le parseur, et l'outil répondait `ok` | matrice |
| écrasé | un regard écrit déplacé par les yeux de l'expression | sonde |
| écrasé | aversion comptée et annulée à l'image suivante | matrice + comptage à l'œil |
| replay involontaire | la même décision renvoyée relançait la surprise | sonde |
| état ancien | l'intention d'une réponse couvrait la suivante (25 s) | suite de 20 min |
| couture Python/JS | l'inspecteur ignorait `Bip01_Head`, le moteur non | vrai modèle masculin |
| test faux positif | « regard user » vérifié sur x seulement | sonde |
| test faux positif | la bouche « sans claquer » mesurée sur le chemin ARKit, pas celui du modèle installé | situation « interrompu » |
| tic | 18 % de micro-expressions répétant la précédente | sonde |
| tic | les joues du sourire sincère aplaties par la parole | test des six visages |

## Rejeté, et pourquoi

| idée | raison |
|---|---|
| micro-saccades plus réalistes | 0.3 px à la taille du panneau : invisible |
| asymétrie G/D de timing (~30 ms) | < 2 images, invisible ; l'asymétrie utile existe déjà dans `amused`, `thinking`, `confused` |
| « brief freeze » avant une alerte | la dérive du repos amortie 0.3 s déplace le nez de < 1 px |
| fondu plus lent vers les fermetures de bouche | fausse alerte de ma mesure ; aucun gain mesurable (+33 ms) |
| respiration du buste en mode visage | contredit le choix de Teddy (corps tenu) ; à rouvrir avec lui |
| nouveau mot d'intention (« merci ») | `acknowledge` + `valence` le couvre déjà ; le vocabulaire de JARVIS ne change pas |
| règle de noms « jeu + numéro » (Rocketbox) | collisions internes (SRanipal ↔ ARKit) : renommé à la conversion |
| clignements incomplets | invisibles à la taille du panneau |

## Les nuances sociales, et d'où elles viennent

Aucune nouvelle expression n'a été créée : chaque nuance demandée est une
combinaison de ce qui existe, vérifiable au labo.

| nuance | comment elle se joue |
|---|---|
| warm, friendly, pleased | `happy`/`amused` à 0.15–0.35 (affect ou intensité), regard tenu |
| attentive, patient | `listening` + écoute (hochements aux pauses), `wait` |
| reassuring | `reassure` : regard tenu, clignement lent voulu, tête calme |
| grateful | `acknowledge` + valence positive : hochement + demi-sourire |
| apologetic | `apologise` : tête basse, sourcils au centre, regard bas (ou tenu si écrit) |
| curious, uncertain, hesitant | `thinking`/`confused` + confiance basse → regard qui cherche |
| skeptical | `disagree` à faible urgence ; `confused` |
| cautious, alert | `warn`/`confirm` : inquiétude, regard stable, aucune échappée |
| relieved | concerned → happy : les sourcils d'inquiétude partent plus lentement que le sourire n'arrive |
| proud | `report_success` : menton relevé, sourire retenu |
| playful | `amuse` : asymétrie, sourire après les yeux |
| focused, serious | `serious`, posture `focused`/`formal` |

Colère et tristesse extrêmes n'existent pas : `angry` est plafonnée à 0.45, le
registre plafonne tout le reste.

## Les quarante situations quotidiennes

Couvertes par un test sur la sortie : arrivée (réveil + salut), remerciement,
demande, attente de réponse, recherche, résultat trouvé ou inattendu,
incertitude, erreur, avertissement, réassurance, plaisanterie, silence de
l'utilisateur, longue explication de l'utilisateur, interruption, changement de
sujet (tour de parole), confirmation, urgence, fin de phrase, événement pendant
la parole, intentions rapprochées, rien à signaler — dans `situations.mjs`,
`scenario_matrix.mjs`, `naturalness.mjs`, `chain_test.py`.

Non couvertes, faute de capteur : l'utilisateur **quitte l'écran** ou
**revient** (aucune caméra), **répète** ou **hésite** au sens du contenu
(aucun accès aux mots côté client), **bruit inhabituel** (le niveau ne dit pas
ce qu'est un son). Ces situations demandent une entrée que le client n'a pas.

## Incertitudes restantes

- Les seuils de voix (appuis, pauses, plancher) sont réglés sur des voix de
  synthèse. À vérifier sur la vraie voix de Gemini et le vrai micro, au labo
  (`JARVIS_AVATAR_DEBUG=1` enregistre une séance rejouable).
- Les amplitudes (appui 2.2°, hochement d'écoute 3°, échappée 0.2–0.32) sont
  des jugements appuyés sur la littérature, pas des mesures sur JARVIS.
- Le visage masculin reste plus discret que le féminin même calibré ; seuls le
  sourire et le froncement ont été compensés, sur mesure. Le reste est à
  l'œil de Teddy.
- La licence du visage féminin reste inconnue ; FaceCap et Michelle sont à
  usage de test local (voir leurs profils).

## `motion: "full"`

Rien de ce qui précède ne suppose le mode visage : les appuis, les échappées,
les hochements d'écoute et l'habituation s'ajoutent à la somme de la tête,
qu'un clip de corps joue ou non ; `chain_test.py` rejoue les seize intentions en
mode complet.
