# Contrat V2 — comment on ajoute une fonctionnalité à JARVIS

> **Point de retour :** `v1.0-jarvis-24-7` (commit `425be0a`).
> Tout ce qui suit ce tag est jetable. Rien de ce qui précède ne l'est.

---

## La règle d'or

**Toute fonctionnalité ajoutée après V1.0 doit pouvoir être retirée sans casser V1.0.**

Test concret, à faire avant de committer : supprime le(s) fichier(s) que tu viens
d'ajouter, relance JARVIS. S'il démarre et fonctionne exactement comme avant,
la fonctionnalité respecte le contrat. Sinon, elle est mal découpée — recommence.

---

## L'arbre de décision

Avant d'écrire une ligne de code, réponds dans l'ordre. **Le premier « oui » gagne.**

```
Nouvelle idée
     │
     ├─ 1. Est-ce une capacité que Gemini peut appeler comme un outil ?
     │       → plugins/<nom>.py            ← 90 % des cas, zéro risque
     │
     ├─ 2. Est-ce purement de l'affichage, du capteur ou de l'UX téléphone ?
     │       → client-android/             ← le serveur ne bouge pas
     │
     ├─ 3. Est-ce un service de fond, permanent, sans invocation vocale ?
     │       → nouveau paquet Python à la racine (ex. context/)
     │         + un hook de délégation d'une ligne dans le cœur
     │
     └─ 4. Rien de tout ça ne marche ?
             → modification du cœur      ← DERNIER RECOURS, justifie par écrit
```

### 1. Plugin — le cas par défaut

Le contrat existe déjà et il est appliqué par [`core/plugin_loader.py`](../core/plugin_loader.py) :

- copie [`plugins/_template.py`](../plugins/_template.py), renomme sans underscore initial ;
- déclare `PLUGIN` (`name`, `description`, `parameters`) et `run()` ;
- optionnellement `PLUGIN_SETTINGS` pour un formulaire de config dans l'UI.

Ce que le loader garantit **sans que tu touches à quoi que ce soit** :

| Garantie | Mécanisme |
|---|---|
| Découverte automatique au démarrage | `discover_plugins()` scanne `plugins/*.py` |
| Un plugin cassé ne casse pas JARVIS | import et validation sous `try/except`, le fichier est rejeté et loggé |
| Pas de collision de nom | rejet si le nom existe déjà en plugin ou en outil cœur |
| Activation/désactivation à chaud | état relu dans la config à chaque appel, sans redémarrage |
| Une exception dans `run()` ne tue pas la session | filet de sécurité dans `PluginRegistry.run()` |

**Un plugin ne nécessite aucune modification de `main.py`. Jamais.**

### 2. Android seul

Batterie, Bluetooth, casque, état d'écran, capteurs, notifications, haptique,
widgets : ça se lit et s'affiche sur le téléphone. Si le serveur n'a pas besoin
de le savoir, le serveur n'en entend pas parler.

Si le serveur doit le savoir, ça devient un message supplémentaire sur `/ws`
(voir §3) — **jamais** un second protocole à côté.

### 3. Nouveau module serveur

Pour ce qui tourne en permanence et n'est pas invocable comme outil : contexte,
politique de proactivité, routines, planificateur.

Forme obligatoire :

- **un paquet Python neuf, à la racine**, à côté de `core/`, `memory/`, `server/` ;
- **il n'importe rien de `main.py`** — la dépendance va dans un seul sens ;
- il est **importable et testable seul**, sans démarrer JARVIS ;
- il expose une API minuscule (une classe, deux ou trois fonctions).

### 4. Le hook de délégation — la seule façon acceptable de toucher au cœur

Quand un module de fond doit être branché, le cœur ne reçoit pas la logique :
il reçoit **un appel vers elle**, sous garde, avec un comportement de repli
identique à V1.

```python
# Interdit — la logique atterrit dans main.py
if battery < 20 and not sleeping and headset_connected:
    ...

# Obligatoire — main.py délègue et ne sait rien
decision = self._policy.evaluate(...) if self._policy else None
if decision is None:
    ...            # exactement le comportement V1
```

Trois conditions, non négociables :

1. **Import optionnel.** Le paquet absent → JARVIS démarre quand même.
   ```python
   try:
       from context.policy import ProactivityPolicy
   except Exception:
       ProactivityPolicy = None
   ```
2. **Repli explicite.** Le hook inactif → le comportement est celui de V1.0, à l'identique.
3. **Aucune ligne existante supprimée ou réécrite.** On insère, on ne remplace pas.

Un hook qui respecte les trois se retire en supprimant les lignes ajoutées.
C'est ça, « retirable sans casser V1 ».

---

## La zone protégée

| Fichier | Règle |
|---|---|
| `main.py` | hooks de délégation uniquement (§4), ou bug critique |
| `ui.py` | idem |
| `core/*.py` | idem — ce sont des mécanismes, pas des fonctionnalités |
| `dashboard/server.py` | ajout de routes et de types de messages autorisé ; **jamais** de modification d'une route existante |
| `actions/*.py` | figé — les nouvelles capacités vont dans `plugins/` |

`actions/` est l'ancienne façon d'ajouter une capacité, celle d'avant le loader.
Elle reste parce qu'elle marche. On n'y ajoute plus rien.

---

## Le cycle

```
Idée
 └→ classer (§1–4) et écrire la réponse dans le message de commit
     └→ écrire dans des fichiers NEUFS
         └→ tester le module seul, sans JARVIS
             └→ tester JARVIS avec, puis sans (le test de retrait)
                 └→ commit
```

**Une fonctionnalité par commit.** Un commit qui ajoute la proactivité *et*
corrige un bug audio est un commit qu'on ne peut pas annuler proprement.

### Le test de retrait

```bash
git stash            # ou : supprime les fichiers neufs
python main.py       # doit démarrer et se comporter comme V1.0
git stash pop
```

### Retour arrière

```bash
git checkout v1.0-jarvis-24-7      # inspecter V1
git diff v1.0-jarvis-24-7 -- main.py   # tout ce que la V2 a touché au cœur
```

Cette deuxième commande est la mesure de santé du projet. **Elle doit rester
courte.** Le jour où elle ne tient plus sur un écran, le contrat a été rompu.

---

## Exemple appliqué — le transport des notifications

L'arbre de décision, sur un cas réel, pour montrer à quoi ressemble une réponse
correcte à chacune de ses branches.

**L'idée :** quand la politique de contexte classe une information en `NOTIFY`,
elle doit devenir une vraie notification Android.

| Branche | Réponse | Où |
|---|---|---|
| §1 plugin ? | Non — rien à invoquer, c'est un canal de sortie permanent | — |
| §2 Android seul ? | **En partie.** L'affichage, les canaux, la déduplication et les compteurs sont purement Android | `client-android/notification/` |
| §3 module serveur ? | **Oui, l'essentiel.** Produire, retenir, re-proposer | `server/notify.py` |
| §4 cœur ? | **Non.** `main.py` n'a pas bougé | — |

Ce que le serveur existant a fourni sans être modifié : `dashboard.broadcast()`
pour la diffusion, `/ws` pour le canal, le rejeu à la connexion pour la remise
en attente, et le fait qu'un `type` inconnu soit ignoré côté client pour la
compatibilité ascendante. **Aucun nouveau protocole, aucun nouveau socket.**

Les deux seules insertions dans la zone protégée :

- `dashboard/server.py` : re-proposer les notifications en attente à la
  connexion (ajout, §3 — une route ni modifiée ni remplacée).
- rien dans `main.py`.

### Ce que cet exemple apprend sur le contrat

La bonne question n'était pas « où mettre le code ». C'était **« qu'est-ce que
le système sait déjà faire que je m'apprête à réécrire ? »**

Le rejeu des 50 derniers messages existait pour redonner du contexte à un client
qui se reconnecte. Il se trouve que c'est aussi, presque, une file d'attente de
notifications — et le voir a évité d'écrire un système de file généraliste. Ce
qui manquait tenait en deux choses : une durée de validité, et un identifiant
pour que le client sache reconnaître ce qu'il a déjà montré.

Corollaire : le même mécanisme de rejeu qui sauve une notification est celui qui
la répéterait. **Une fonctionnalité qui réutilise un mécanisme existant hérite
aussi de ses effets de bord**, et c'est là qu'il faut regarder en premier.

## À remplir pour chaque fonctionnalité

- [ ] Classée §1, §2, §3 ou §4 — et la raison est écrite
- [ ] Le code vit dans des fichiers neufs
- [ ] Si §4 : import optionnel + repli V1 + aucune ligne existante réécrite
- [ ] Le module se teste seul
- [ ] Le test de retrait passe
- [ ] Un seul sujet dans le commit
