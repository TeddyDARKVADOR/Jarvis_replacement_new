# `cognition/` — architecture cognitive persistante (phases 1–5)

Paquet racine au sens de [docs/V2_CONTRACT.md](../docs/V2_CONTRACT.md). Actif
**en mode local uniquement** (`python main.py --local`) pour l'instant. Supprimer
ce dossier et `plugins/cognition_*.py` rend JARVIS identique à avant :
chaque hook de `main.py` est un `if self._cognition:`, et l'attache est sous
`try/except`.

```
python -m cognition.selftest      # 42 checks, sans clé, sans réseau, sans toucher tes données
python -m cognition.providers     # sonde réelle des générateurs gratuits configurés
```

Recherche et plan complet : [docs/cognition/RECHERCHE.md](../docs/cognition/RECHERCHE.md).

## Ce que ça fait aujourd'hui

| | |
|---|---|
| **Journal d'événements** | Chaque tour de parole et chaque résultat d'outil est écrit dans `memory/cognition.db` (ignoré par Git), par un fil de fond : environ 5 µs par hook côté conversation. Les tours bruts sont purgés après 30 jours. |
| **Mémoire avec provenance** | Quand Gemini appelle `save_memory`, la V1 écrit `long_term.json` comme avant. En plus, le fait est confronté à ce que tu as **réellement dit**. Trois verdicts :<br>• **soutenu** → actif, confiance 0,85 ;<br>• **non soutenu** : un nom propre, un nombre ou une date jamais dits → `proposed`, confiance ≤ 0,5, jamais dans le prompt ;<br>• **incertain**, en général une traduction (« pastries » pour « pâtisseries ») → **second avis** d'un autre modèle gratuit (Groq ou Gemini, jamais le petit modèle local) ; s'il accepte, actif à 0,75.<br>Les dates déduites d'« hier » ou « demain » sont reconnues. |
| **Pas de blanchiment** | Un fait non vérifié que la V1 a recopié dans `long_term.json` n'est pas promu par la réimportation. Seule une preuve (ta parole, un outil) promeut un souvenir. |
| **Contradictions** | Une déduction du modèle ne remplace jamais ce que tu as dit : elle est gardée à côté et l'ancienne passe `contested`. Ce que tu redis remplace l'ancienne valeur (`superseded`, avec date de fin). Rien n'est effacé en silence. |
| **Suppression** | `Memory.forget()` efface vraiment : la ligne, l'index plein texte et les liens. Il reste une trace « N souvenirs supprimés », sans le contenu. |
| **Autobiographie** | Le résumé de session V1 est consommé au briefing puis détruit. Ici il est **archivé** comme épisode daté. Les 3 derniers épisodes entrent dans le prompt, les autres sont accessibles par l'outil `recall_episodes`. |
| **Rappel** | Formule de Generative Agents (Park et al. 2023) : récence (0,995 par heure) + importance + pertinence (BM25 de FTS5), chacune normalisée entre 0 et 1. |
| **Constitution** | [constitution/jarvis.toml](constitution/jarvis.toml) est versionnée. Le hash de chaque version est enregistré, et une modification sans changement de numéro est signalée. Un noyau de 304 caractères part dans le prompt. |
| **Import V1** | `long_term.json` est **lu**, jamais écrit. L'import est idempotent, et une valeur modifiée côté V1 remplace l'ancienne avec son historique. |
| **Moteur émotionnel** (`affect.py`) | Six variables calculées : valence, activation, assurance, frustration, curiosité, chaleur. Elles changent selon des **règles fixes** sur des événements observés : un outil réussit ou échoue, échoue encore, tu remercies, tu corriges, un rappel revient vide. Chaque variable retombe vers sa ligne de base avec sa demi-vie, et l'état est conservé au redémarrage (décru du temps passé, pas remis à zéro). Chaque changement est journalisé avec sa cause. |
| **Conséquences des émotions** | Au-delà d'un seuil, une consigne : changer d'approche après des échecs répétés, prudence quand l'assurance est basse, réponses brèves, question de suivi, ton plus chaleureux. Elle entre dans le prompt à la connexion, et **en cours de session dans la réponse d'outil** au moment où le seuil est franchi (une seule fois). **Invariant testé** : les consignes viennent d'une liste fermée qui ne contient que de la prudence ou du ton ; aucune n'élargit une permission ni ne lève une confirmation. |
| **`self_state`** | Nouvel outil. « Comment tu te sens ? » obtient une réponse lue dans les données (valeurs, causes récentes, consignes actives), présentée comme des variables calculées, pas comme un ressenti. Le selftest vérifie que le rapport est identique aux données, avec un témoin. |
| **Réflexion** (`reflection.py`) | Après environ 1,5 d'importance cumulée (au plus toutes les 12 h), un modèle gratuit propose jusqu'à 3 synthèses, **chacune citant ses souvenirs sources**. Une citation inventée invalide la synthèse. Les synthèses restent `proposed` et n'entrent jamais dans le prompt ; le rappel les marque REFLECTION. |
| **Objectifs persistants** (`goals.py`, outil `goals`) | **JARVIS propose, tu disposes.** Tout objectif naît `proposed`. Il ne devient actif, atteint, annulé ou relancé que si tu appuies sur **CONFIRMER** sur le HUD : c'est le même bandeau que pour l'arrêt de la machine (`core/confirm.py`), avec un jeton émis par l'interface que le modèle ne peut pas fabriquer. Le modèle peut seulement proposer, noter un progrès, ou retirer sa propre proposition.<br>Chaque objectif a une justification, un critère de réussite, une priorité de 1 à 5, une échéance facultative et un **budget d'actions**. Budget épuisé ou même note de progrès répétée 3 fois → `blocked` (anti-boucle) ; échéance passée → `expired`.<br>Au plus 10 objectifs ouverts et 3 propositions de JARVIS par jour. Seuls les objectifs actifs entrent dans le prompt. Un objectif atteint fait monter la valence, un objectif bloqué la frustration. L'historique (`goal_events`) dit qui a fait quoi : modèle, utilisateur ou système. |
| **Générateurs de secours** | `providers.py` enchaîne local (Ollama et compatibles) → Groq → Cerebras → Gemini → OpenRouter → Mistral, selon les clés présentes. Un quota épuisé met le fournisseur en pause (durée annoncée par le serveur, sinon 60 s doublées), une clé refusée le met en pause 1 h. Utilisé pour le second avis et la réflexion. `Cognition.generate()` donne à **n'importe quel** modèle, IA locale comprise, la constitution et l'état émotionnel courant dans son prompt système. |

## Coût

- **Tokens Gemini Live** : +522 caractères de prompt système par connexion (constitution et état), et jusqu'à environ 1 000 avec 3 épisodes (budget dur de 1 100). Plus 3 outils déclarés (`recall_episodes`, `self_state`, `goals`). Les objectifs actifs ajoutent environ 120 caractères chacun (3 au plus).
- **API gratuites** : un second avis par fait traduit, une réflexion au plus toutes les 12 h.

## Configuration (optionnelle, `config/api_keys.json`)

```json
{
  "groq_api_key": "…", "gemini_api_key": "…",
  "cerebras_api_key": "…", "openrouter_api_key": "…", "mistral_api_key": "…",
  "cognition_provider_order": ["local", "groq", "gemini"],
  "cognition_providers": {
    "local": {"model": "qwen3:1.7b", "base_url": "http://127.0.0.1:11434/v1"},
    "groq":  {"model": "openai/gpt-oss-20b", "extra": {"reasoning_effort": "low"}}
  }
}
```

Un fournisseur sans clé est ignoré. Le serveur local n'est essayé que s'il répond
(sonde de 0,5 s, au plus une fois par minute).

## Fichiers

| Fichier | Rôle |
|---|---|
| `policy.py` | règles épistémiques pures : provenance → statut/confiance, preuve dans la transcription |
| `store.py` | SQLite (WAL), schéma versionné par `user_version` |
| `memory.py` | journal, souvenirs, contradictions, oubli, rappel, import V1 |
| `constitution/` | `jarvis.toml`, `CHANGELOG.md`, chargement et contrôle de version |
| `providers.py` | chaîne de générateurs gratuits avec pauses |
| `affect.py` | moteur émotionnel pur : variables, règles, décroissance, consignes |
| `reflection.py` | synthèses citées (Park 2023), jamais promues sans preuve |
| `goals.py` | objectifs : règles, budgets, boucles, échéances, historique |
| `../plugins/cognition_goals.py` | le seul pont vers `core/confirm.py` |
| `runtime.py` | `Cognition` + `attach()` : ce que voit `main.py` ; aucune exception ne sort |
| `selftest.py` | les 42 vérifications |

## Mesures réelles sur ce PC (2026-10-13)

**Vitesse des générateurs :**

| Générateur | Temps | Remarque |
|---|---|---|
| Groq `gpt-oss-20b` | 0,44–0,98 s | |
| Gemini `3.1-flash-lite` | 1,0–3,0 s | |
| Qwen3 1.7B local, sans réflexion | 0,7–1,0 s | CPU, environ 33 tokens/s |
| Qwen3 1.7B local, avec réflexion | 6–7 s | |

**Qualité :**
- **Second avis réel (Groq)** : il accepte « ate pastries yesterday » pour « Hier, j'ai mangé des pâtisseries », et refuse « …from the bakery near his office », un détail inventé.
- **Qwen3 1.7B sans réflexion n'est pas fiable pour juger** : il a noté valence −1 un remerciement après un succès. C'est pour cela qu'il est exclu des tâches de jugement et que l'appraisal est faite par des règles.
- **Qwen3 1.7B ne tient pas compte de l'état reçu** : avec une frustration à 1,0 après 3 échecs, il a répondu que ses recherches « se sont déroulées de manière réussie ». Recevoir l'état ne suffit pas à un modèle de cette taille ; un modèle plus gros reste à mesurer.

## Limites connues (honnêtement)

- **La preuve est lexicale.** Une paraphrase d'un mot commun que Gemini traduit (« nurse » pour « infirmière ») sans nom propre ni nombre restera « non vérifiée ». C'est un faux négatif sans perte : le fait est gardé, seulement marqué.
- **La transcription vient de la reconnaissance vocale de Gemini.** Un nom mal transcrit fera aussi échouer la preuve.
- **Il n'existe pas encore d'écran HUD pour valider, contester ou supprimer** un souvenir de `cognition.db`. Le panneau mémoire V1 ne concerne que `long_term.json`.
- **Les indices de parole sont lexicaux** (FR et EN) : « merci », « tu te trompes ». L'ironie ou une correction implicite ne sont pas détectées.
- **Les seuils et demi-vies sont des choix de départ, pas des valeurs calibrées.** Ils sont à ajuster sur l'usage réel, à partir de `affect_log`.
- **La consigne en cours de session n'est portée que par une réponse d'outil.** Une conversation sans aucun outil ne la verra qu'à la reconnexion suivante.
- **Un seul bandeau de confirmation à la fois** (contrainte de `core/confirm.py`). Si une confirmation attend déjà, une nouvelle proposition est enregistrée, mais son bouton devra être redemandé (`request_approval`).
- **Le progrès vers un objectif est déclaré par le modèle** (`progress`). Ses autres actions n'y sont pas rattachées automatiquement, donc le budget compte ce qu'il déclare, pas tout ce qu'il fait.
- **Aucune boucle autonome ne poursuit encore les objectifs** (phase 7) : ils guident la conversation, sans déclencher d'action seuls.
- **Le modèle de soi est partiel** : `self_state` couvre l'état, la mémoire, les objectifs, la constitution et le nombre d'outils.
