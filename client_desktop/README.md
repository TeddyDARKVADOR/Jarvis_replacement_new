# JARVIS Desktop — le HUD de ce poste

Le terminal Windows de JARVIS. Le troisième client, pas un troisième assistant.

```
                 ☁️  ORACLE
              MARK LIII + Gemini
                     │
                  Tailscale
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
       📱 Android        💻 client_desktop (HUD)
```

```bash
python main.py                          # le HUD, relié à Oracle (même chose que la ligne suivante)
python -m client_desktop                # le HUD
python -m client_desktop --mini         # démarre en orbe, dans le coin de l'écran
python -m client_desktop --debug        # + la fenêtre développeur (compteurs, journal brut)
python -m client_desktop --selftest     # 33 contrôles : pas de fenêtre, pas de réseau, pas de micro
python -m client_desktop --autostart on # inscrire le démarrage avec Windows
python main.py --local                  # le MÊME HUD, cerveau sur ce PC (local_hud.py)
```

L'interface elle-même — le noyau, les anneaux, les panneaux, la page
Paramètres, le gouverneur de performances — est décrite dans
[`hud/README.md`](hud/README.md).

---

## La règle de ce paquet

```
client_desktop/  ──────►  core.wake_word, core.action_loader   (importés, jamais modifiés)
client_desktop/  ◄──X──   (rien de main.py, du serveur, des actions n'importe ce paquet)
```

Le selftest la rend mécanique :

* **rien ici n'importe `main` ni un SDK Gemini** — la promesse « pas de seconde
  session » : un client qui instancierait `JarvisLive` serait un second
  assistant portant le même nom, avec deux historiques et deux factures ;
* **`core.wake_word` et `core.action_loader` sont les seuls modules de `core/`
  touchés** — un autre (un client de modèle, un moteur vocal) serait un second
  cerveau arrivé par la porte de derrière ;
* **les chemins protégés** (`main.py`, `core/`, `memory/`, `actions/`,
  `plugins/`, `dashboard/`, `requirements.txt`) **n'ont aucune modification non
  commitée** : le cerveau se change délibérément, dans son propre commit.

Ce paquet parle le protocole filaire ; il ne pourrait pas ouvrir de session même
s'il essayait, parce que la clé d'API ne quitte jamais le serveur.

`local_hud.py`, à la racine, réutilise le HUD de ce paquet pour afficher le
cerveau quand il tourne sur ce PC : c'est lui qui importe `main`, pas ce paquet.

---

## Appairage

Pas d'écran à part : au premier lancement, sur un PC jamais appairé, le HUD
s'ouvre sur **Paramètres → Connexion**.

| Champ | D'où il vient |
|---|---|
| **Adresse** | le nom MagicDNS, ex. `jarvis-vps.tailnet-XXXX.ts.net` (une URL collée est nettoyée) |
| **Port** | 8000 |
| **Jeton d'accès** | `python -m server.run_headless --pairing` sur Oracle |

**Tester la connexion** pose deux questions séparément : le serveur répond-il
(`/health`), le jeton est-il accepté (`/api/device-login`) ? Si le serveur est un
`.ts.net` injoignable et que l'application Tailscale n'est pas lancée, la page le
dit.

Les réglages vivent dans `%LOCALAPPDATA%\JarvisDesktop\settings.json`, **pas**
dans `config/` : le jeton est un mot de passe, et un secret dans l'arbre git est
à un `git add -A` d'être publié. Un seul fichier pour tout — connexion, audio,
apparence du HUD (l'ancien `hud.json` y est migré tout seul) — et le même dans
les deux modes.

---

## Ce que le HUD demande à « l'assistant »

Mémoire, nom, voix, briefing, plugins, fichier joint : le HUD les demande à un
*backend* ([`hud/backends.py`](hud/backends.py)) sans savoir qui répond.

| | en ligne | en local |
|---|---|---|
| mémoire, profil, plugins | `POST /api/assistant` sur Oracle, avec le jeton de session | `server.assistant_control` appelé directement — **le même objet** |
| fichier à analyser | `POST /api/upload` ; le serveur lit le dernier envoi | lu sur le disque par le cerveau local |
| téléphone | appairé avec Oracle (`--pairing`) | QR code du tableau de bord local |

---

## La porte du micro

La détection et la permission sont séparées :

```
   micro  ──chaque trame──►  mot d'éveil          (local, toujours)
                                  │
                               détecté
                                  ▼
   micro  ──tant qu'ouverte──►  la porte  ──►  /ws/phone-audio   (Oracle)
```

* La porte **se ferme sur minuterie** (25 s par défaut, réglable dans
  Paramètres → Audio, rafraîchie par l'activité). Une porte qui resterait ouverte
  jusqu'à ce qu'on la ferme serait un micro ouvert sur un poste de travail à la
  première callback manquée — et cette panne-là est silencieuse.
* La vie du socket montant **est** la vie de la porte : le connecter signifie
  quelque chose pour le serveur (un micro est vivant, il coupe le sien).
* Le micro **n'est pas ouvert quand rien n'en a besoin** : il tourne quand le mot
  d'éveil écoute ou que la porte est ouverte, et s'arrête sinon.

Le bouton micro (et Ctrl+Espace) verrouille la porte ouverte et ignore la
minuterie — le cas « appuyer pour parler ». Un clic sur le noyau interrompt JARVIS
s'il parle, et ouvre ou ferme la porte sinon.

---

## Démarrer avec Windows

`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `pythonw.exe launch.pyw`,
qui ouvre le HUD. Une seule entrée (une éventuelle entrée `JarvisHUD` d'une
version précédente est retirée au premier changement).

**Pas un service** : un service tourne en session 0, sans bureau où afficher le
HUD ni session audio où ouvrir un micro. **Pas le Planificateur** : sa relance
sur échec, combinée à la clé Run et à un double-clic sur le raccourci, est
exactement la boucle de doublons à éviter. La clé Run ne demande aucun droit
administrateur et se désactive depuis le Gestionnaire des tâches → Démarrage.

### Une seule instance

Un mutex noyau nommé (`Local\JarvisDesktop.SingleInstance`), pas un fichier PID :
un fichier PID survit à un plantage et ment ensuite. Un second lancement — en
ligne comme en local — signale l'instance existante et s'arrête ; la première
montre son HUD. `Local\` plutôt que `Global\` : deux utilisateurs du même poste
ont chacun leur JARVIS, leur micro et leurs réglages.

---

## Reconnexion

Portée telle quelle de `ReconnectManager.kt`, parce que la panne qu'elle traite
n'est pas propre à Android.

| Situation | Réponse |
|---|---|
| fermeture ordinaire, redémarrage du serveur, réseau perdu | attente 1→2→4→8→15→30 s, ±20 % |
| fermeture **4001** | le jeton de session a vieilli — nouvelle connexion, pas une erreur |
| **401** sur `/api/device-login` | le jeton d'accès est refusé — arrêt, et on le dit |

* **Le délai est plafonné, pas le nombre d'essais.** Un poste dont le tunnel est
  tombé pendant le redémarrage d'Oracle doit être reconnecté le matin.
* **Une connexion qui a vécu moins de 3 s ne remet pas l'attente à zéro** : c'est
  un échec en deux actes, et le traiter comme un succès produit une boucle serrée.
* Un petit « toc » TCP pendant un long compte à rebours remplace le rappel
  système qu'Android reçoit quand le réseau revient.

### Une commande perdue se signale, elle ne se rejoue pas

Une commande tapée qui reste sans réponse 25 s est signalée (journal +
notification). Elle n'est **jamais renvoyée** toute seule : une commande peut être
« vide le dossier Téléchargements », et une seconde tentative silencieuse d'une
action irréversible est bien pire qu'une phrase à répéter.

---

## À tester — pas encore vérifié

```
⏳ Le mot d'éveil « Hey Jarvis » avec une vraie voix (openwakeword non installé ici)
⏳ La voix montante réelle (micro → Gemini) en ligne
⏳ Le démarrage automatique à une vraie ouverture de session
⏳ La reconnexion après un vrai redémarrage d'Oracle
⏳ Le comportement après veille/reprise du portable
⏳ /api/assistant contre le vrai Oracle (testé en local et par le selftest du serveur)
```

### Note sur Tailscale

`tailscaled` peut rester en `BackendState: NoState` — « Tailscale is starting »,
aucun pair — quand **`tailscale-ipn.exe` n'est pas lancé** : sous Windows c'est ce
processus qui active le profil de l'utilisateur. Le lancer remet le réseau en
route immédiatement ; redémarrer le *service* n'y fait rien.
