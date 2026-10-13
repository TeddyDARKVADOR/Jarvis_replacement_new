# `cognition/` — architecture cognitive persistante (phases 1–2)

Paquet racine au sens de [docs/V2_CONTRACT.md](../docs/V2_CONTRACT.md). Actif
**en mode local uniquement** (`python main.py --local`) pour l'instant. Supprimer
ce dossier et `plugins/cognition_memory.py` rend JARVIS identique à avant :
chaque hook de `main.py` est un `if self._cognition:`, et l'attache est sous
`try/except`.

```
python -m cognition.selftest      # 23 checks, sans clé, sans réseau, sans toucher tes données
python -m cognition.providers     # sonde réelle des générateurs gratuits configurés
```

Recherche et plan complet : [docs/cognition/RECHERCHE.md](../docs/cognition/RECHERCHE.md).

## Ce que ça fait aujourd'hui

| | |
|---|---|
| **Journal d'événements** | Chaque tour de parole et chaque résultat d'outil est écrit dans `memory/cognition.db` (ignoré par Git), par un fil de fond : environ 5 µs par hook côté conversation. Les tours bruts sont purgés après 30 jours. |
| **Mémoire avec provenance** | Quand Gemini appelle `save_memory`, la V1 écrit `long_term.json` comme avant. En plus, le fait est confronté à ce que tu as **réellement dit** dans la session. S'il contient un nom propre ou un nombre que tu n'as jamais prononcé, il est enregistré comme `proposed` (non vérifié, confiance ≤ 0,5) et n'entre jamais dans le prompt comme un fait. |
| **Contradictions** | Une déduction du modèle ne remplace jamais ce que tu as dit : elle est gardée à côté et l'ancienne passe `contested`. Ce que tu redis remplace l'ancienne valeur (`superseded`, avec date de fin). Rien n'est effacé en silence. |
| **Suppression** | `Memory.forget()` efface vraiment : la ligne, l'index plein texte et les liens. Il reste une trace « N souvenirs supprimés », sans le contenu. |
| **Autobiographie** | Le résumé de session V1 est consommé au briefing puis détruit. Ici il est **archivé** comme épisode daté. Les 3 derniers épisodes entrent dans le prompt, les autres sont accessibles par l'outil `recall_episodes`. |
| **Rappel** | Formule de Generative Agents (Park et al. 2023) : récence (0,995 par heure) + importance + pertinence (BM25 de FTS5), chacune normalisée entre 0 et 1. |
| **Constitution** | [constitution/jarvis.toml](constitution/jarvis.toml) est versionnée. Le hash de chaque version est enregistré, et une modification sans changement de numéro est signalée. Un noyau de 304 caractères part dans le prompt. |
| **Import V1** | `long_term.json` est **lu**, jamais écrit. L'import est idempotent, et une valeur modifiée côté V1 remplace l'ancienne avec son historique. |
| **Générateurs de secours** | `providers.py` enchaîne local (Ollama et compatibles) → Groq → Cerebras → Gemini → OpenRouter → Mistral, selon les clés présentes. Un quota épuisé met le fournisseur en pause (durée annoncée par le serveur, sinon 60 s doublées), une clé refusée le met en pause 1 h. **Pas encore utilisé par la conversation** : il sert aux phases suivantes (réflexion, appraisal). |

## Coût

- **Tokens** : +304 caractères de prompt système par connexion, et jusqu'à environ 780 avec 3 épisodes archivés (budget dur de 900). Aucun appel d'API supplémentaire.
- **Outils** : un outil de plus déclaré (`recall_episodes`).

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
| `runtime.py` | `Cognition` + `attach()` : ce que voit `main.py` ; aucune exception ne sort |
| `selftest.py` | les 23 vérifications |

## Limites connues (honnêtement)

- **La preuve est lexicale.** Une paraphrase d'un mot commun que Gemini traduit (« nurse » pour « infirmière ») sans nom propre ni nombre restera « non vérifiée ». C'est un faux négatif sans perte : le fait est gardé, seulement marqué.
- **La transcription vient de la reconnaissance vocale de Gemini.** Un nom mal transcrit fera aussi échouer la preuve.
- **Il n'existe pas encore d'écran HUD pour valider, contester ou supprimer** un souvenir de `cognition.db`. Le panneau mémoire V1 ne concerne que `long_term.json`.
- **Pas encore d'émotions, d'objectifs ni de modèle de soi** (phases 4 à 6).
