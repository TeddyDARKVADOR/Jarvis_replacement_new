# `context/` — contexte et politique de proactivité

Paquet autonome. **Il n'importe rien de `main.py`, `ui.py`, `core/`, `dashboard/`,
`actions/` ou `server/`** — vérifié automatiquement par le test. Supprimer le
dossier rend JARVIS identique à `v1.0-jarvis-24-7`.

```
python -m context.selftest     # 30 checks, sans clé Gemini, sans micro, sans téléphone
```

## Les quatre fichiers

| Fichier | Rôle | Dépendances |
|---|---|---|
| `model.py` | le vocabulaire : `Priority`, `Channel`, `Route`, `Situation`, `DeviceState`, `Decision` | aucune |
| `situation.py` | faits bruts → une situation, pur et testable | `model` |
| `store.py` | ce que le téléphone a dit, thread-safe, avec péremption | `model`, `situation` |
| `policy.py` | priorité × situation → canal de sortie | `model` |

## La table

C'est la fonctionnalité. Tout le reste l'alimente ou l'applique.

| | CRITIQUE | IMPORTANT | UTILE | ANODIN |
|---|---|---|---|---|
| **DRIVING** | interrompre | **voix** | plus tard | rien |
| **MEETING** | notif sonore | notif muette | plus tard | rien |
| **ASLEEP** | interrompre¹ | plus tard | plus tard | rien |
| **ACTIVE** | interrompre | voix | notif muette | rien |
| **IDLE** | interrompre | notif sonore | notif muette | rien |
| **UNKNOWN** | interrompre | notif sonore | plus tard | rien |

¹ `PolicyConfig(wake_for_critical=False)` la neutralise.

Le casque n'apparaît pas dans la table : il décide de la **route** (`HEADSET` /
`PHONE` / `DESKTOP` / `NONE`), pas de la priorité. Un casque débranché ne doit
pas ressembler à un changement de politique.

**Plus tard ≠ rien.** Tout ce qui est `DEFER` part dans `DeferralQueue` et
revient quand la situation le permet — la politique est rejouée à ce moment-là,
donc se réveiller en réunion ne libère rien.

## Ordre de déduction

`DRIVING` → `ASLEEP` → `MEETING` → `ACTIVE` → `IDLE`

Règle : **l'observé prime sur le déduit, le déduit prime sur le réglage.**

- `DRIVING` d'abord : l'accéléromètre observe un mouvement, et se taire en
  voiture c'est se taire au seul endroit où la voix est le seul canal utilisable.
- `ASLEEP` demande **trois** signaux concordants (heures calmes + écran éteint
  + inactivité ≥ 30 min). L'horloge seule a tort tous les soirs où tu veilles.
- `MEETING` (Ne pas déranger) passe **après** le sommeil : beaucoup de gens
  laissent DND actif la nuit, et un DND prioritaire requalifierait chaque nuit
  en réunion.

Données de plus de 5 minutes → périmées. Un casque cru connecté une heure après
avoir été débranché, c'est un assistant qui parle dans une pièce vide.

## Deux sources de contexte, pas une

Le téléphone n'est pas la seule chose qui sait où tu es.

| Source | Ce qu'elle voit | Quand elle sert |
|---|---|---|
| Téléphone | batterie, casque, écran, DND, sonnerie, réseau | dès qu'il rapporte |
| Poste local | `local_presence_s` — depuis quand tu ne lui as pas parlé | quand le téléphone est absent ou périmé |

Le téléphone l'emporte toujours : il voit davantage. Le repli local existe pour
une raison précise — sans lui, **toute installation sans téléphone verrait sa
proactivité s'éteindre complètement**, puisque `UNKNOWN` + `IMPORTANT` est
différé. Or « pas de téléphone » ne veut pas dire « pas d'utilisateur » : c'est
exactement le signal sur lequel la proactivité V1 reposait déjà.

Un seul signal ne conclut jamais au sommeil : sans écran à observer, le repli
local ne produit que `ACTIVE` ou `IDLE`.

## Branchement — fait

### 1. `dashboard/server.py` (+22 lignes, 0 suppression)

Nouveau type de message `device_state` sur `/ws`, et oubli à la déconnexion —
mais **seulement pour une socket qui avait effectivement rapporté**, sinon un
onglet du dashboard web qu'on ferme effacerait l'état du téléphone.

### 2. `main.py` (+31 lignes, 0 suppression)

Hook dans `_run_proactive_mode`. Deux choix qui méritent d'être connus :

- **Le check-in proactif est classé `IMPORTANT`, pas `UTILE`.** Il a déjà franchi
  le verrou de silence et le cooldown de `ProactiveEngine` : c'est par
  construction un message que JARVIS a jugé digne d'être dit. `UTILE` est la
  bande des messages que les automatisations produiront plus tard.
- **La condition est `speaks`, pas `silent`.** Les canaux `NOTIFY` existent dans
  la table mais **aucun transport ne les délivre encore**. Tant que c'est le
  cas, tout ce qui n'est pas la voix est mis en attente plutôt que parlé quand
  même. C'est la prochaine brique à construire.

La sonde système lit `sounddevice.__headless__` (`server/audio_bridge.py`) pour
savoir s'il existe des enceintes locales — plutôt que d'inventer un second
drapeau.

Effet net sur V1 :

| Situation | V1 | Maintenant |
|---|---|---|
| Tu viens de parler au poste | parle | parle |
| Téléphone actif, casque ou non | parle | parle |
| En voiture | parle | parle |
| Absent depuis 40 min | parle dans le vide | différé |
| En réunion | parle | différé |
| Tu dors | parle | différé |

### 3. `client-android/` (+40 lignes, 0 suppression, 1 fichier neuf)

`device/DeviceStateReporter.kt` rapporte toutes les 30 s, et immédiatement à
chaque changement d'écran, de casque, de sonnerie ou de DND. Battement de cœur
à 120 s, soit la moitié du TTL serveur — un téléphone silencieux ne doit pas
ressembler à un téléphone parti. Le test vérifie que ces deux nombres restent
cohérents entre le Kotlin et le Python.

**Aucune permission nouvelle.** Batterie, écran, casque, sonnerie, DND et type de
réseau sont tous lisibles avec ce que `AndroidManifest.xml` déclare déjà.

## Limite connue : la détection de la voiture

`ACTIVITY_RECOGNITION` (le signal `IN_VEHICLE`) et la localisation sont
volontairement absents — ils exigeraient des permissions que le manifeste refuse
explicitement, plus Play Services.

Conséquence concrète : **la ligne `DRIVING` ne se déclenche que par le nom de
l'appareil Bluetooth connecté.** Pour qu'elle fonctionne chez toi :

```python
# config, namespace "context"
car_bluetooth_names = "nom exact de ton autoradio"
```

Et sur Android 12+, ce nom peut revenir générique sans `BLUETOOTH_CONNECT` —
d'où le fait qu'il doive être déclaré côté serveur plutôt que deviné côté
téléphone.

## Réglages

Namespace de config `context`, lu paresseusement au premier `get_store()` :

| Clé | Défaut | Effet |
|---|---|---|
| `quiet_start_hour` / `quiet_end_hour` | 23 / 7 | fenêtre des heures calmes |
| `asleep_idle_s` | 1800 | inactivité minimale pour « endormi » |
| `active_idle_s` | 300 | en deçà, « aux commandes » |
| `device_ttl_s` | 300 | péremption des données téléphone |
| `vehicle_confidence` | 70 | seuil Android pour croire `IN_VEHICLE` |
| `car_bluetooth_names` | liste générique | **à compléter — voir ci-dessus** |

`context.reset()` relit la config.

## Test de retrait

```bash
mv context /tmp/ && python main.py && mv /tmp/context .
```

Les deux hooks du cœur ne rattrapent que `ImportError` : paquet absent →
comportement V1 à l'identique ; paquet cassé → l'`except` existant avale
l'erreur et le check-in est sauté, parce qu'en cas de doute se taire est le bon
échec.

## Ce qui manque pour la suite

1. **Un transport pour les notifications.** Toute la colonne `NOTIFY` est
   décidée mais rien ne la délivre. `dashboard.broadcast()` existe déjà et le
   client Android sait traiter des événements `/ws` typés — c'est la plus petite
   brique suivante.
2. **Vider la file.** `DeferralQueue` se remplit ; personne ne la relit encore.
   Le briefing du matin est son consommateur naturel.
