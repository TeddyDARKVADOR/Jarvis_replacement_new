# Ce que JARVIS sait faire sur PC, et ce qui en arrive sur Android

Audit du 2026-09-24, fait sur le **code** (pas les README) : `main.py`
(`TOOL_DECLARATIONS`), `actions/*.py` chargés par `core/action_loader.py`,
`plugins/`, `server/` (routage, notifications, contexte), `client_desktop/`,
`client-android/`. Aucune de ces fonctionnalités n'a été codée : c'est une base
de décision.

## Comment une action arrive (ou non) sur un appareil

Il faut ce modèle en tête pour lire la matrice.

- **Une capacité est un nom d'action.** Un client qui *déclare* `open_app` reçoit
  les appels `open_app` (`server/routing.py`, règles de `server/targeting.py` :
  appareil nommé → seul capable → origine → sinon on demande).
- **Une action que personne ne déclare s'exécute sur le serveur**, c'est-à-dire
  sur Oracle : Linux, sans écran, sans clavier, sans navigateur graphique.
- Le **client Desktop** déclare les actions qu'il peut exécuter localement
  (`client_desktop/device.py`, découvertes et prouvées au démarrage).
- Le **téléphone** en déclare **une seule** : `open_app`
  (`Protocol.CAPABILITIES`, `DeviceCapabilities.kt`), sous réserve de
  l'autorisation « affichage par-dessus les autres applications ».

Donc, depuis le téléphone, tout ce qui n'est pas `open_app` tourne sur Oracle :
parfait pour ce qui est du réseau et de la réflexion, inutile ou en échec pour
ce qui touche un écran.

Sur Oracle, 15 actions sont chargées ; `browser_control` est rejetée
(pas de Playwright, voulu : `requirements-server.txt`).

## Matrice

Légende : ✅ fait · ⚠️ fonctionne mais pas au bon endroit / partiellement ·
❌ absent. « Serveur » = Oracle sait déjà le faire pour n'importe quel client.

| Fonctionnalité | Outil | PC | Android | Facile à transférer | Nécessite serveur | Dépend du système | Intérêt téléphone |
|---|---|---|---|---|---|---|---|
| **Conversation vocale** (micro, voix, interruption) | session Live | ✅ | ✅ micro, haut-parleur, INTERRUPT | — | oui | non | — |
| **Mot d'éveil** « Hey Jarvis » | `wakeword/` | ✅ `client_desktop/wake.py` | ✅ openWakeWord local | — | non | micro | — |
| **Saisie texte** | TYPE | ✅ | ✅ | — | oui | non | — |
| **Historique** | `log` | ✅ | ✅ onglet HISTORY | — | oui | non | — |
| **Recherche web** (search, news, research) | `web_search` | ✅ | ✅ via serveur | déjà là | oui | non | fort |
| **Météo** | `weather_report` | ✅ ouvre le navigateur + phrase | ⚠️ phrase seule ; le navigateur s'ouvre… sur Oracle (rien) | simple | oui | non | fort |
| **Vols** | `flight_finder` | ✅ | ✅ via serveur (Gemini) | déjà là | oui | non | moyen |
| **Mémoire** (retenir / rappeler) | `save_memory`, `recall_memory` | ✅ | ✅ via serveur | déjà là | oui | non | fort |
| **Annulation** | `undo` | ✅ | ⚠️ annule les actions serveur ; rien de local à annuler | — | oui | — | faible |
| **Confirmation** | événement `confirm` | ✅ | ✅ carte CONFIRM/CANCEL + délai | déjà là | oui | non | — |
| **Contexte** (écran, casque, batterie, réseau, ne pas déranger…) | `context/` | ✅ | ✅ `DeviceStateReporter` (13 champs) | déjà là | oui | non | — |
| **Notifications** (alertes, file, relâche) | `server/notify.py`, `alerts.py` | ✅ | ✅ `JarvisNotificationManager` | déjà là | oui | non | — |
| **Routage par appareil** | `targeting.py` | ✅ | ✅ (1 capacité déclarée) | — | oui | — | — |
| **Présence / avatar** | `set_presence`, `avatar/` | ✅ visage 3D | ✅ visage 3D (cette phase) | — | oui | WebView/GPU | — |
| **Veille de sujets** | `manage_monitor` (`background_monitor`) | ✅ | ✅ via serveur ; résultats en notification | déjà là | oui | non | fort |
| **Proactif / briefing du matin** | `proactive.py` | ✅ | ✅ via serveur (voix/notification) | déjà là | oui | non | — |
| **Rappels** | `reminder` | ✅ Planificateur de tâches Windows + notification locale | ❌ sur Oracle : `systemd-run` + notification de bureau que personne ne voit | moyen | oui | Windows / cron | **très fort** |
| **Ouvrir une app** | `open_app` | ✅ | ✅ (capacité déclarée) | — | non | intents Android | — |
| **YouTube** : résumé, infos, tendances | `youtube_video` | ✅ | ✅ via serveur (transcriptions) | déjà là | oui | non | moyen |
| **YouTube** : lire une vidéo | `youtube_video` play | ✅ navigateur local | ❌ l'URL s'ouvre sur Oracle | simple | non | intent `ACTION_VIEW` | fort |
| **Navigateur** (ouvrir, chercher, cliquer, remplir) | `browser_control` | ✅ Playwright | ❌ rejetée sur Oracle | ouvrir une URL : simple ; piloter : lourd | non | navigateur local | ouvrir : fort ; piloter : faible |
| **Messages** (WhatsApp, Telegram) | `send_message` | ✅ automatisation de l'app de bureau | ❌ pyautogui | moyen | non | intents / partage Android | **fort** |
| **Fichiers** (lister, créer, déplacer…) | `file_controller` | ✅ disque du PC | ⚠️ agit sur le disque d'Oracle | lourd | non | stockage Android (SAF) | faible |
| **Traitement de fichier envoyé** (image, PDF, OCR…) | `file_processor` | ✅ | ⚠️ le serveur sait traiter ; l'app ne sait pas envoyer | moyen | oui | sélecteur de fichiers | moyen |
| **Écran** (capture, lecture de l'écran) | `screen_process` | ✅ `mss` | ❌ | lourd | oui (analyse) | MediaProjection | faible |
| **Webcam / caméra** | `screen_process` (camera), `close_camera` | ✅ OpenCV | ❌ | moyen | oui (analyse) | CameraX + permission | **fort** |
| **Réglages** (volume, luminosité, Wi-Fi, verrouillage…) | `computer_settings` | ✅ | ❌ (pycaw, pyautogui) | moyen | non | API Android, souvent restreintes | moyen |
| **Contrôle souris/clavier** | `computer_control` | ✅ | ❌ | sans objet | non | — | aucun |
| **Bureau** (fond d'écran, rangement) | `desktop_control` | ✅ | ❌ | sans objet | non | — | aucun |
| **Jeux Steam/Epic** | `game_updater` | ✅ | ❌ | sans objet (pilote le PC) | non | — | via le PC seulement |
| **Surveillance matériel** | `system_status` | ✅ métriques du PC | ⚠️ renvoie les métriques d'**Oracle** | simple | non | `BatteryManager`, déjà dans le contexte | moyen |
| **Code / projets** | `code_helper`, `dev_agent` | ✅ fichiers + VS Code locaux | ⚠️ écrit sur Oracle, invisible | sans objet | oui | — | faible |
| **Arrêter JARVIS** | `shutdown_jarvis` | ✅ | ✅ | — | oui | — | — |

## Chaque transfert, en détail

Pour chaque capacité PC absente ou mal placée sur Android : ce qui manque, si le
serveur sait déjà, si le téléphone peut n'être qu'un client, la permission, la
capacité locale, la difficulté, le sens sur un téléphone.

### Rappels — `reminder`
- **Manque** : sur Oracle (Linux), `reminder` programme une tâche `systemd-run`
  (ou `at`) qui affiche une notification de *bureau* (`plyer`) : personne ne la
  voit, et le téléphone n'est jamais prévenu.
- **Serveur** : oui pour la partie utile — `server/alerts.py` et `notify.py`
  savent déjà livrer une notification au téléphone, avec file et relâche.
- **Client seulement ?** Oui : le téléphone affiche déjà les notifications.
- **Permission** : aucune nouvelle (`POST_NOTIFICATIONS` déjà accordée).
  `SCHEDULE_EXACT_ALARM` seulement si on voulait un rappel *hors ligne*.
- **Capacité locale** : non, si le rappel vit sur le serveur.
- **Difficulté** : moyenne (un planificateur côté serveur qui alimente la file
  existante ; attention au redémarrage d'Oracle, le rappel doit être persistant).
- **Sens** : le plus fort de la liste — c'est l'usage type d'un assistant de poche.

### Messages — `send_message`
- **Manque** : l'action automatise l'app de bureau (pyautogui).
- **Serveur** : non, il ne peut qu'orienter.
- **Client** : le téléphone devient l'exécutant d'une capacité `send_message`.
- **Permission** : aucune pour un *brouillon* ouvert dans WhatsApp/SMS (intent
  `ACTION_SENDTO` / `wa.me`) que l'utilisateur envoie d'un toucher ; `SEND_SMS`
  (sensible, restreinte par le Play Store) pour un envoi sans toucher.
- **Difficulté** : moyenne (intents simples ; la résolution du destinataire
  demande `READ_CONTACTS`).
- **Sens** : fort, et la confirmation existante (`confirm`) s'y applique.

### Caméra — `screen_process` (webcam)
- **Manque** : la capture passe par OpenCV sur le PC.
- **Serveur** : oui pour l'analyse (Gemini sait décrire une image).
- **Client** : capacité `camera` déclarée par le téléphone ; `targeting.py`
  prend déjà « photo → le téléphone » comme exemple de sa règle 2.
- **Permission** : `CAMERA` (nouvelle).
- **Capacité locale** : oui (CameraX), plus l'envoi de l'image.
- **Difficulté** : moyenne.
- **Sens** : fort (« qu'est-ce que c'est ? » en montrant un objet).

### Ouvrir une URL / lire une vidéo — `browser_control` (open, search), `youtube_video` (play), `weather_report`
- **Manque** : ces actions ouvrent un navigateur… sur la machine qui les exécute.
- **Serveur** : il sait construire l'URL ; il ne peut pas l'afficher.
- **Client** : une capacité `open_url` (intent `ACTION_VIEW`), sur le modèle
  exact d'`open_app`.
- **Permission** : la même que `open_app` (lancer une activité depuis un service
  en arrière-plan).
- **Difficulté** : simple pour ouvrir ; **piloter** une page (cliquer, remplir)
  reste lourd et peu utile sur un téléphone.
- **Sens** : fort pour ouvrir, faible pour piloter.

### Envoi de fichiers — `file_processor`
- **Manque** : le serveur traite déjà un fichier reçu (`file_received`) ; l'app
  n'a pas de moyen d'en envoyer un.
- **Permission** : aucune (sélecteur de documents du système).
- **Difficulté** : moyenne (téléversement + événement existant).
- **Sens** : moyen (photo d'un document à résumer, capture à expliquer).

### Réglages du téléphone — `computer_settings`
- **Manque** : tout est Windows (pycaw, raccourcis).
- **Client** : capacité locale uniquement.
- **Permission** : volume sans permission ; luminosité `WRITE_SETTINGS`
  (spéciale) ; Wi-Fi/Bluetooth très restreints depuis Android 10 ; « ne pas
  déranger » demande l'accès aux règles DND.
- **Difficulté** : moyenne, et partielle par nature.
- **Sens** : moyen (le volume et « ne pas déranger » oui, le reste déjà à un geste).

### État du téléphone — `system_status`
- **Manque** : l'outil renvoie les métriques d'Oracle.
- **Déjà là** : batterie, charge, réseau, casque, écran, DND remontent dans le
  contexte (`DeviceStateReporter`) — il manque seulement que l'outil lise celles
  de l'appareil d'origine.
- **Permission** : aucune.
- **Difficulté** : simple (côté serveur).
- **Sens** : moyen.

### Fichiers — `file_controller` ; Écran — `screen_process` (écran)
- **Fichiers** : stockage Android cloisonné (SAF) ; lourd ; faible intérêt vocal.
- **Écran** : `MediaProjection` exige un consentement à chaque session et une
  notification permanente ; lourd ; faible intérêt.

### Sans objet sur un téléphone
`computer_control` (souris/clavier), `desktop_control` (fond d'écran, rangement),
`game_updater` (Steam/Epic : pilote le PC, et reste utile **depuis** le téléphone
quand le PC est allumé — le routage « sur mon PC » le permet déjà),
`code_helper` / `dev_agent` (produisent des fichiers et un VS Code).

## Proposition d'ordre (à décider, pas un classement)

Critère : l'intérêt au téléphone, pondéré par ce qui existe déjà.

1. **Rappels livrés en notification** — aucune permission, l'infrastructure de
   notification existe, usage le plus fréquent.
2. **`open_url`** — même patron qu'`open_app` ; débloque météo (page), YouTube
   (lecture) et « ouvre tel site ».
3. **`system_status` de l'appareil d'origine** — données déjà reçues.
4. **Messages en brouillon** — intents, sans `SEND_SMS` ; la confirmation existe.
5. **Caméra** — nouvelle permission, mais le routage l'attend déjà.
6. **Envoi de fichiers** — l'analyse existe côté serveur.
7. **Réglages** (volume, ne pas déranger) — partiel par nature.

Les points 1 et 3 sont **côté serveur** ; 2, 4, 5, 6, 7 ajoutent une capacité
déclarée par le téléphone, qui suit la chaîne décrite dans
le client Desktop (`client_desktop/device.py`) : prouvée avant d'être
déclarée, testée par sa sortie réelle.
