# JARVIS HUD — l'interface plein écran du poste

```
python -m client_desktop.hud              le HUD (maximisé)
python -m client_desktop.hud --mini       démarre en orbe, coin de l'écran
python -m client_desktop.hud --debug      + la fenêtre Developer / Debug
python -m client_desktop.hud --selftest   12 vérifications, sans fenêtre
pythonw client_desktop/hud/launch_hud.pyw double-clic, sans console
```

`python main.py` lance aussi le HUD : le cerveau est sur Oracle, ce PC est un
terminal. `python main.py --local` fait tourner le cerveau sur ce PC (clé Gemini
dans `config/api_keys.json`) avec **le même HUD** — voir `local_hud.py` à la racine.
En local, **le micro démarre fermé** : rien ne part vers Gemini tant que vous ne
l'ouvrez pas (bouton micro, Ctrl+Espace, clic sur le noyau). Un micro ouvert en
permanence envoyait tout ce qui se dit dans la pièce — et la langue de la pièce
devenait celle de JARVIS.
`python main.py --classic` ouvre l'ancienne fenêtre. Sur
Oracle rien ne change : `server.run_headless` *importe* `main.py`, et la
redirection ne s'exécute que lorsque `main.py` est lancé directement.

`python -m client_desktop` lance toujours l'ancien panneau, octet pour octet.

## La page Paramètres (⚙ · Ctrl+,)

| Onglet | Contenu |
|---|---|
| **Connexion** | adresse du serveur (une URL collée est nettoyée), port, TLS, **jeton d'accès** (masqué, coller, afficher), nom de l'appareil, **Tester la connexion** |
| **Audio** | micro, haut-parleurs, mot d'éveil (activer, installer, sensibilité), durée d'ouverture du micro, micro ouvert au démarrage |
| **Démarrage** | lancer avec Windows (entrée `JarvisHUD`, remplace celle de l'ancien panneau), démarrer en orbe, plein écran, notifications |
| **Interface** | couleur, sons et volume, visage 3D, performances |
| **Avancé** | identifiant de l'appareil, emplacement du fichier, fenêtre développeur |

Les réglages de connexion et d'audio sont ceux de `config.Settings`, enregistrés
par `config.save`, dans le même fichier que la fenêtre développeur
(`%LOCALAPPDATA%\JarvisDesktop\settings.json`, hors du dépôt). Ils
s'appliquent à l'enregistrement, sans redémarrer : reconnexion si l'adresse ou
le jeton change, micro relancé sur le nouveau périphérique, mot d'éveil
démarré ou arrêté.

**Tester** pose deux questions séparément, parce qu'elles n'ont pas la même
réponse : le serveur répond-il (`/health`, sans jeton) ? le jeton est-il
accepté (`/api/device-login`) ? Si le serveur est un `.ts.net` injoignable et
que l'application Tailscale n'est pas lancée, la page le dit.

Au premier lancement (PC non appairé), c'est cette page qui s'ouvre, et non la
fenêtre développeur.

---

## Le panneau ESPRIT (bouton cerveau, mode local)

Ce que l'architecture cognitive (`cognition/`) enregistre, lu dans
`memory/cognition.db` : des variables calculées et des données, pas un ressenti.
Disponible seulement en mode local ; en mode Oracle, le panneau dit pourquoi.

| Onglet | Contenu |
|---|---|
| **ÉTAT** | Les 6 variables affectives avec leur ligne de base, les consignes actives (ce que l'état change dans son comportement) et la cause nommée de chaque changement récent. |
| **OBJECTIFS** | Objectifs ouverts : **APPROUVER**, **RELANCER** (objectif bloqué), **ATTEINT**, **ANNULER** (double clic). JARVIS propose, vous disposez.<br>**Autonomie** : interrupteur global, **ARRÊT D'URGENCE**, et sur chaque objectif actif **AUTONOMIE ON/OFF** et **LANCER**. Le journal montre chaque proposition, exécutée ou **refusée**, et les questions qu'il vous pose. |
| **SOUVENIRS** | Recherche, et vues tous / non vérifiés / contestés / réflexions / épisodes / validés. ✓ valider, ✗ rejeter (plus jamais rappelé), 🗑 oublier (double clic, effacé pour de bon). |
| **MODÈLE DE SOI** | Fiabilité mesurée de chaque outil, calibration (Brier, ECE) de ses attentes et de sa confiance dans les souvenirs face à vos verdicts, limites imposées par le code, générateurs de secours. |

Un clic ici est une décision de l'utilisateur : aucun outil du modèle ne mène à
ces opérations (vérifié par `python -m cognition.selftest`, contrôle 46).
Code : [web/js/mind.js](web/js/mind.js) · [cognition/hud.py](../../cognition/hud.py).

## Ce qui a changé, et ce qui n'a pas changé

**Rien du client existant n'a été modifié.** Le contrôleur (`app.JarvisDesktop`),
le réseau, le store, le mot d'éveil, la porte micro, le tray et la fenêtre de
debug sont les mêmes objets. Une seule classe est remplacée, au lancement :

```python
app_module.JarvisPanel = HudWindow     # hud/__main__.py
```

`HudWindow` a exactement la surface de `JarvisPanel` (7 signaux, les méthodes
que le contrôleur appelle) — le selftest compare les deux et échoue si le
panneau gagne un jour un signal que le HUD n'a pas.

## Ce qu'on voit

```
┌──────────────────────────────────────────────────────────────────────┐
│ ◎ J.A.R.V.I.S        ● RÉPONSE · jarvis-vps        09:57:50  ♪ ☺ ◐ ? ⚙ │
├────────────┬───────────────────────────────────────┬─────────────────┤
│ 01 SYSTÈME │            ╭── bezel 000°…330° ──╮    │ 04 CONVERSATION │
│ CPU ▁▃▅▂   │        MIC ( spectre · couronne )VOX  │  bulles, cartes │
│ RAM ▮▮▮▯   │            (   noyau plasma 3D   )    │  de données,    │
│ 02 LIAISON │            ╰──── orbites ───────╯     │  liens, copier  │
│ 03 AUDIO   │              R É P O N S E            │ 05 JOURNAL      │
│ vu-mètres  │   « C'est noté : je vous rappellerai… »│                 │
│            │   ∿∿∿∿∿∿∿ oscilloscope ∿∿∿∿∿∿∿         │                 │
├────────────┴───────────────────────────────────────┴─────────────────┤
│   (🎙)  › Parlez, ou tapez une commande…          ENVOYER  ■ INTERROMPRE │
└──────────────────────────────────────────────────────────────────────┘
```

* **Le noyau** (three.js) : sphère de plasma déformée par bruit simplex et
  éclairée en fresnel, treillis icosaédrique, coquille de 2 400 particules,
  trois orbites. Il respire avec **votre voix** quand il écoute et avec **la
  sienne** quand il parle — jamais avec un niveau inventé.
* **Les anneaux** sont des jauges : MIC à gauche, VOX à droite, spectre radial,
  couronne qui tourne en connexion/réflexion, onde de choc au « Hey Jarvis »,
  arc de compte à rebours pendant une confirmation.
* **Une couleur par état**, la même partout (puce, noyau, anneaux, sous-titre) :
  PRÊT · ÉCOUTE (vert) · RÉFLEXION (violet) · RÉPONSE · CONNEXION (ambre) ·
  ERREUR (rouge) · HORS LIGNE (gris). Ce sont les couleurs du panneau.
* **Sous chaque état, une phrase dit quoi faire** (« Dites « Hey Jarvis » ou
  cliquez sur le noyau », « Cliquez ou Échap pour interrompre »…).
* **Le visage 3D** (bouton ☺) remplace le plasma à l'intérieur du réacteur. C'est
  la page `avatar/` en `<iframe>`, pilotée par le même `presence.Director` que
  dans le panneau, via le `postMessage` que `bridge.js` offrait déjà.
* **Le mode orbe** (Ctrl+M, ou ▭) : 260×300, toujours au-dessus, dans le coin —
  l'esprit du panneau. Double-clic pour rouvrir le HUD.

## S'adapter au PC, sans rien enlever

`web/js/perf.js` est un **gouverneur** : il mesure les FPS réels toutes les
1,5 s et ajuste le *coût* du rendu, jamais son contenu. Tous les éléments
restent à l'écran à tous les niveaux.

| Niveau | FPS max | 3D (résolution, détail, particules) | halos 2D | flou des panneaux | envois Python |
|---|---|---|---|---|---|
| ULTRA | 60 | ×2 · 40 · 2 400 | vrai flou | oui | 30 Hz |
| ÉLEVÉ | 60 | ×1,25 · 40 · 2 400 | vrai flou | non | 30 Hz |
| ÉQUILIBRÉ | 60 | ×1 · 28 · 2 000 | trait large équivalent | non | 30 Hz |
| ÉCO | 45 | ×0,8 · 20 · 1 600 | trait large équivalent | non | 24 Hz |
| MINIMAL | 30 | ×0,62 · 14 · 1 200 | trait large équivalent | non | 20 Hz |

* **Premier choix** d'après le matériel : GPU absent ou en rendu logiciel → MINIMAL ;
  GPU intégré (Intel, Vega…) → ÉQUILIBRÉ ; ≤ 4 cœurs ou ≤ 4 Go → ÉCO ; écran
  4K → un cran plus bas. Le niveau appris la fois précédente est repris.
* **Ensuite** : deux mesures sous 82 % de l'objectif → un cran plus bas ;
  12 s stables → un cran plus haut, jamais au-dessus d'un niveau qui vient
  d'échouer (pas d'oscillation). Les périodes où la fenêtre est cachée,
  déplacée ou réduite ne comptent pas.
* **Toujours, quel que soit le niveau** : la lunette graduée est dessinée une
  fois puis seulement tournée, et les ~200 traits des anneaux sont regroupés
  en une poignée de tracés. Une seule boucle d'animation pour toute la page.
* **Au lancement** (`__main__.py`) : `--ignore-gpu-blocklist
  --enable-gpu-rasterization`, pour que Chromium n'abandonne pas le GPU au
  profit d'un rendu WebGL sur le CPU. `JARVIS_HUD_SAFE_GPU=1` désactive ça si un
  pilote se comporte mal.
* **À la main** : ◐ (couleur) → PERFORMANCES : AUTO ou un niveau forcé.
  **Ctrl+Maj+F** affiche les FPS, le niveau, le GPU et le coût en ms de chaque
  couche. `window.__hudSkip = {rings: true}` (console) coupe une couche, pour
  diagnostiquer un PC précis.

Mesuré sur le PC de développement (Intel Iris Xe, 1600×900 à 125 %) : le
JavaScript coûte 4 à 5 ms par image à tous les niveaux. Le coût est côté
GPU/compositeur, et vient d'abord du canvas des anneaux, puis du fond. Le noyau
3D et le HTML pèsent peu. Le gouverneur y descend seul jusqu'à MINIMAL.

## Raccourcis

| Touche | Action |
|---|---|
| Ctrl+Espace | micro ouvert / fermé |
| Échap | interrompre JARVIS · refuser une confirmation · fermer |
| `/` ou Ctrl+K, ou simplement taper | aller à la ligne de commande |
| ↑ ↓ | historique des commandes |
| Ctrl+M | mode orbe |
| F11 | plein écran |
| Ctrl+, | réglages / appairage |
| F1 | aide |

La confirmation d'une action irréversible ne se valide **jamais** au clavier :
Entrée n'y fait rien, seul un clic sur CONFIRMER l'accepte.

## Fichiers

| | |
|---|---|
| `window.py` | `HudWindow` + `HudBridge` (QWebChannel) — pousse le snapshot à 30 Hz |
| `scheme.py` | `jarvis-hud://hud/` → `web/`, et `/vendor/` → `avatar/vendor/` (three.js) |
| `face.py` | pilote du visage dans l'iframe, réutilise les constantes de `avatar_view` |
| `metrics.py` | CPU, RAM, disque, réseau, batterie (psutil, non bloquant) |
| `prefs.py` | `%LOCALAPPDATA%\JarvisDesktop\hud.json` — couleur, visage, sons, géométrie |
| `web/` | la page : `js/reactor.js` (3D), `rings.js`, `background.js`, `main.js` |
| `web/js/sim.js` | un faux MARK LIII : ouvrir la page dans un navigateur pour régler le visuel |

## Vérifié

* `python -m client_desktop.hud --selftest` → **9/9**, dont « le client existant
  est intact » (git status sur `client_desktop/` hors `hud/`).
* `python -m client_desktop --selftest` → **50/50**, inchangé.
* Rendus capturés des états RÉPONSE, RÉFLEXION, ÉCOUTE (orbe), CONFIRMATION, et
  du visage dans le réacteur.
* Chaque bouton testé de la page jusqu'au signal Python : commande, micro,
  réglages, interruption par clic sur le noyau, confirmation, Ctrl+M, couleur.
* Lancement avec le vrai `JarvisDesktop` (profil vide) : aucune exception.

## À tester

```
⏳ Une vraie session contre MARK LIII (voix montante et descendante)
⏳ Le visage avec le vrai modèle .glb installé (testé ici en corps procédural)
⏳ Le mode orbe sur un deuxième écran à 150 %
```
