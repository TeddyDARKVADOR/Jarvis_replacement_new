# JARVIS — architecture cognitive persistante : audit, recherche, plan

> État au 2026-10-13, commit `6bac0d6`. Aucun fichier existant n'a été modifié pour
> produire ce document. Ce qui est **vérifié** (lu dans le code, mesuré, lu dans une
> source primaire) est distingué de ce qui est **proposé** ou **à vérifier**.

Rappel permanent : tout ce qui suit décrit des **fonctions** (stocker, rappeler,
évaluer, planifier, rapporter un état enregistré). Aucune de ces fonctions n'est, ni
ne prouve, une expérience subjective. Une « émotion » de JARVIS sera un nombre
calculé ; un « souvenir » une ligne en base ; un « objectif » un enregistrement avec
des critères ; une « introspection » la lecture de ces données.

---

## 0. Audit du JARVIS existant (vérifié dans le code)

| Sujet | Constat |
|---|---|
| Langage | Python ≥ 3.11 (`asyncio.TaskGroup`), venv local en 3.14. Client Android en Kotlin. |
| Architecture | `main.py` (`JarvisLive`) = boucle Gemini Live : audio entrant/sortant, dispatch d'outils, tâches de fond. Autour : `actions/` (outils auto-décrits `TOOL`), `plugins/` (`PLUGIN` + `run()`), `core/` (undo, confirmation, wake word, Studio de code), `context/` (situation → politique de prise de parole), `server/` (mode headless sur Oracle), `client_desktop/` (HUD), `eval_lab/`. |
| Règle de projet | le contrat V2 (graphe `contrat`) : toute fonctionnalité post-V1 doit pouvoir être **retirée sans casser V1** ; ordre de préférence plugin → paquet racine + hook d'une ligne → cœur en dernier recours. `context/`, `server/`, `eval_lab/` respectent déjà ce modèle (« rien ne m'importe », vérifié par selftest). |
| Modèle | `models/gemini-3.1-flash-live-preview` (audio natif, Live API) pour la conversation ; `gemini-3.1-flash-lite` pour le résumé de session ; Studio de code : Groq (`openai/gpt-oss-120b`) sinon `gemini-flash-latest`. SDK `google-genai`. |
| Prompt / contexte | `_build_config()` assemble **une fois par connexion** : date/heure + identité (nom, forme d'adresse) + bloc mémoire + [core/prompt.txt](../../core/prompt.txt). Compression par fenêtre glissante, reprise de session par handle (en RAM). Pas de cache de contexte sur le modèle Live. Les injections en cours de session passent par `send_client_content` (briefing, proactif, alertes). |
| Mémoire | [memory/memory_manager.py](../../memory/memory_manager.py) : un JSON clé→`{value, updated}` en 6 catégories (identity, preferences, projects, relationships, wishes, notes). Prompt : identité + entrées récentes (900 car.) + index des clés (420 car.). Rappel : outil `recall_memory`, score **lexical** local. Écriture : outil `save_memory` appelé **par le modèle**, sans source ni confiance. Résumés de session : liste `sessions` (max 3), **consommée** (`pop_last_session`) au briefing suivant puis effacée. |
| Affect | Aucun affect décisionnel. (Un paquet d'affect purement visuel existait pour un visage 3D ; il a été retiré le 2026-10-13 avec ce visage.) |
| Outils | ~25 actions (fichiers, navigateur, recherche web, messages, réglages OS, vision écran/caméra, rappels, Studio de code…) + plugins. |
| Exécution / sécurité | `core/confirm.py` : jeton de confirmation émis par l'UI (le modèle ne peut pas se confirmer). `core/undo.py` : pile d'annulation. Studio de code : liste blanche de commandes, chemins vérifiés, journal, points de restauration, verdict fondé sur des tests réels, Windows Sandbox. |
| Boucles autonomes | `_run_proactive_mode` (minute par minute ; 15 min de silence, 20 min de cooldown ; filtrée par `context/`), `_run_background_monitor` (sujets suivis, 30 min), `_run_system_monitor` (10 s), briefing de démarrage. Aucune boucle ne poursuit d'objectif propre. |
| Journalisation | `print` + `ui.write_log` ; `memory/token_usage.jsonl` (sans contenu) ; journal du Studio. Pas de journal d'événements structuré commun. |
| Tests | Selftests par paquet + `eval_lab`. **Base mesurée aujourd'hui** : context 33/33, presence 60/60, server 42/42, routing 35/35, client_desktop 33/33, token_usage PASS, code_workspace **32/34**, eval_lab **26/28** (voir §0.1). |
| Matériel / coût | Serveur Oracle Always Free (1 Go RAM suffisant d'après le guide de déploiement (graphe `deployment`) : aucun modèle local). Dernier commit : réduction des coûts Gemini → sensibilité forte au coût en tokens. |
| Confidentialité | Mémoire en fichier local (ignoré par Git) ; son contenu part chez Google dans le prompt. Clés dans `config/api_keys.json` (ignoré par Git). |

### 0.1 Échecs déjà présents avant toute modification

À ne pas attribuer aux futurs travaux :

- `code_workspace` #2 : test de jonction Windows, `NotADirectoryError` sous Linux.
- `code_workspace` #33 : Pyright n'analyse aucun fichier sous cet environnement.
- `eval_lab` (2 checks) : Node.js introuvable via `proto` (`proto::detect::failed`), donc les scénarios `face-*` échouent.

### 0.2 Écarts entre l'existant et la cible

1. **Une interprétation du modèle devient un fait.** `save_memory` écrit ce que le modèle décide, sans provenance ni statut. C'est exactement le risque que la cible veut éviter.
2. **Il n'y a pas d'histoire.** Les résumés de session sont détruits après lecture : JARVIS n'a aucune mémoire épisodique ni autobiographique au-delà d'un jour.
3. **Il n'y a pas d'affect décisionnel.** Rien ne relie un état interne à une décision, et rien n'est persisté.
4. **Il n'y a ni objectifs ni modèle de soi.** Les capacités existent (registres d'actions et de plugins, confirmation, undo), mais rien ne les expose comme un état interrogeable.
5. **Le contexte est figé à la connexion.** Tout état cognitif injecté dans le prompt système ne bouge qu'à la reconnexion. En cours de session, il faut des injections ponctuelles, ce qui a un coût en tokens.

---

## 1. Synthèse scientifique

### 1.1 Mémoire, réflexion, planification d'agents LLM

**Generative Agents** (Park et al., UIST 2023, lu dans le PDF) :
- *Flux de mémoire* : chaque observation est stockée en langage naturel avec ses dates de création et de dernier accès.
- *Récupération* : `score = α_r·récence + α_i·importance + α_p·pertinence`, chaque terme normalisé min-max dans [0,1], tous les α = 1. La récence décroît exponentiellement (facteur 0,995 par heure de jeu depuis le dernier accès). L'importance est une note de 1 à 10 demandée au LLM à la création. La pertinence est un cosinus d'embeddings.
- *Réflexion* : elle se déclenche quand la somme des importances récentes dépasse **150**, soit environ 2 à 3 fois par jour simulé. Le LLM reçoit les 100 derniers enregistrements, formule 3 questions saillantes, récupère des souvenirs pour chacune, puis produit 5 « insights » **avec citation des souvenirs sources**. Les réflexions sont stockées comme souvenirs et forment un arbre.
- *Planification* : plan du jour → blocs d'une heure → blocs de 5 à 15 min, révisés en réaction aux événements.
- *Résultats* : 100 évaluateurs, TrueSkill. Architecture complète μ = 29,89 ; sans réflexion 26,88 ; sans réflexion ni planification 25,64 ; humains (crowdworkers) 22,95 ; sans mémoire, planification ni réflexion 21,21. Kruskal-Wallis H(4) = 150,29, p < 0,001.
- *Limites observées* : **embellissements hallucinés** de souvenirs, style trop formel, récupération parfois incomplète. L'évaluation porte sur la *crédibilité* perçue, pas sur l'exactitude.
- Code : `github.com/joonspk-research/generative_agents`.

**Ce qui est venu depuis** (sources primaires, lecture partielle) :
- **CoALA** (Sumers, Yao et al., TMLR 2024) : cadre de conception qui sépare mémoire de travail, épisodique, sémantique et procédurale, avec des actions internes (récupérer, raisonner, apprendre) et externes. C'est le vocabulaire le plus utile pour structurer JARVIS.
- **MemGPT / Letta** (Packer et al., 2023) : contexte principal (instructions, *working context*, file FIFO) et contexte externe (*recall storage*, *archival storage*). Le modèle gère lui-même ce qui entre et sort par appels de fonction.
- **Reflexion** (Shinn et al., NeurIPS 2023) : réflexion verbale stockée après un échec, réutilisée à l'essai suivant (91 % pass@1 sur HumanEval contre 80 % pour GPT-4). **Le signal d'échec vient de tests externes.** Sur MBPP, 16,3 % de faux positifs des tests rendent le gain trompeur. Mémoire limitée à une fenêtre glissante.
- **Huang et al., ICLR 2024**, « LLMs Cannot Self-Correct Reasoning Yet » : **sans retour externe**, l'auto-correction n'améliore pas le raisonnement et le dégrade parfois. Les gains antérieurs venaient souvent d'oracles.
- **Mémoire de production** : Mem0 (2025), A-Mem (notes liées de type Zettelkasten, « évolution » des souvenirs, 2025), Zep/Graphiti (graphe de connaissances **temporel** : validité des faits dans le temps, 2025). Les chiffres de benchmark sont **contestés entre éditeurs** : Mem0 et Zep se rétorquent sur LoCoMo avec des protocoles différents. Aucun chiffre n'est à reprendre tel quel.
- **LongMemEval** (Wu et al., ICLR 2025) : 500 questions sur 5 capacités (extraction, raisonnement multi-sessions, raisonnement temporel, **mises à jour de connaissance**, **abstention**). Les assistants commerciaux perdent environ 30 % sur des historiques longs. C'est le meilleur gabarit pour nos tests de mémoire.

**Conséquences pour JARVIS** :
- La structure de récupération récence × importance × pertinence et les réflexions **citant leurs sources** sont réutilisables telles quelles.
- La leçon la plus robuste de la littérature (Reflexion vs Huang) : **une réflexion n'améliore quelque chose que si un signal externe vérifiable la valide.** Le Studio de code applique déjà ce principe (verdict par les tests). Il faut le généraliser.
- Le temps doit être une donnée de premier ordre : un fait a une période de validité, une mise à jour remplace sans effacer.

### 1.2 Valeurs, constitution, cohérence

- **Constitutional AI** (Bai et al., Anthropic, déc. 2022, arXiv 2212.08073). Phase supervisée : le modèle critique puis révise ses réponses selon des principes, et on l'ajuste sur les révisions. Phase RL : un modèle compare des paires, un modèle de préférence est entraîné sur ces jugements (RLAIF) et sert de récompense. Les principes ont été choisis « de façon assez ad hoc ». Les modèles RL-CAI peuvent être sur-entraînés jusqu'au **Goodharting**.
- **Constitution de Claude** (Anthropic, janvier 2026, CC0) : un ordre de priorité explicite (sûreté, éthique, lignes directrices, utilité), qui explique ses raisons plutôt que d'empiler des règles, avec quelques contraintes absolues. Anthropic indique qu'elle **sert à l'entraînement**.
- **Model Spec d'OpenAI** (révision du 12 fév. 2025, CC0) : une *chaîne de commandement* (plateforme > développeur > utilisateur) qui résout les conflits d'instructions, avec des valeurs par défaut modifiables et des limites dures.
- **Distinction essentielle** : chez Anthropic et OpenAI, ces textes agissent **par l'entraînement**. Une constitution placée dans le prompt de JARVIS n'est qu'une instruction. Elle peut être contournée, oubliée par la compression de contexte, ou mal appliquée. **Aucun prompt ne garantit la stabilité d'une identité.** Sans entraînement, la stabilité doit venir de trois choses : le **code** (règles dures appliquées hors du modèle, comme `confirm.py`), les **données** (constitution versionnée, réinjectée à chaque connexion) et les **tests** (sondes de cohérence rejouées à chaque version).

### 1.3 Émotions : appraisal et modèles dimensionnels

- **Chain-of-Emotion** (Croissant, Frister, Schofield, McCall, PLOS ONE, mai 2024, lu). Un appel LLM d'*appraisal* génère l'émotion du personnage, qui est stockée dans une chaîne réinjectée avant la réponse. Résultats :
  - test STEU : 0,57 sans mémoire, 0,74 avec mémoire, 0,83 avec appraisal ;
  - étude utilisateur (N = 30) : effets faibles (p ≈ 0,03–0,05) sur le naturel et la sensibilité perçue, aucun effet sur la chaleur ou la compétence.
  - Limites : un seul modèle (gpt-3.5-turbo), un seul scénario court, pas de récupération mémoire. **L'émotion n'y influence que le texte**, pas des décisions.
- **EMA** (Marsella & Gratch, Cognitive Systems Research, 2009) : modèle d'appraisal calculatoire. Les événements sont évalués selon des dimensions (pertinence pour les buts, désirabilité, probabilité, contrôlabilité, responsabilité) qui produisent des émotions et des stratégies de *coping*. Critère de « validité de processus » : la dynamique temporelle doit être plausible.
- **Modèle circomplexe** (Russell) : valence × activation. Limite connue : il ne sépare pas « confus » de « réfléchit ».

**Conséquence** : pour être *mesurable*, l'appraisal de JARVIS doit être **déterministe et calculé à partir d'événements structurés** (échec d'outil, correction par l'utilisateur, objectif atteint, nouveauté), pas généré librement par le LLM. Le LLM peut proposer une appraisal pour les événements conversationnels, mais en tant qu'*entrée étiquetée*, jamais en écriture directe de l'état.

### 1.4 Conscience artificielle : ce qui est testable

- **Butlin, Long et al. 2023** (arXiv 2308.08708, lu). Hypothèse de travail : le *fonctionnalisme computationnel*. Ils tirent 14 indicateurs de 5 familles de théories :
  - RPT-1/2 (récurrence, représentations perceptives intégrées) ;
  - GWT-1/4 (modules spécialisés en parallèle, espace de travail à capacité limitée, diffusion globale, attention dépendante de l'état) ;
  - HOT-1/4 (perception générative ou bruitée, monitoring métacognitif qui distingue le fiable du bruit, agence guidée par la croyance et mise à jour par ce monitoring, espace de qualités) ;
  - AST-1 (modèle prédictif de l'attention) ;
  - PP-1 (codage prédictif) ;
  - AE-1/2 (agence, incarnation).

  Les indicateurs augmentent une crédence, ce n'est pas une check-list. Conclusion : aucun système actuel n'est conscient, et il n'y a pas d'obstacle technique évident à en construire un qui satisfasse les indicateurs.
- **Suite 2025** : Butlin, Long, Bayne, Bengio, Birch, Chalmers et al., « Identifying indicators of consciousness in AI systems », *Trends in Cognitive Sciences* (DOI 10.1016/j.tics.2025.10.011). Ils formalisent la méthode des indicateurs dérivés des théories. *Je n'ai lu que le résumé.*
- **Goldstein & Kirk-Giannini 2024** (arXiv 2410.11407) soutiennent que, *si* la GWT est vraie, des agents langagiers pourraient facilement satisfaire ses conditions. C'est un argument **conditionnel et débattu**.
- **Ce qu'on ne peut pas conclure** : satisfaire GWT-2/3 par un espace de travail logiciel montre seulement qu'une architecture a une propriété *fonctionnelle* décrite par une théorie, elle-même non validée. Les théories se contredisent entre elles. L'hypothèse fonctionnaliste est contestée. **Pour JARVIS**, la GWT est utilisée comme **patron d'ingénierie** (sélection sous contrainte de capacité, diffusion aux modules, journal des décisions). Ce n'est ni un objectif ni une preuve de conscience.

### 1.5 Introspection

- **Anthropic, 29 oct. 2025** (Lindsey, « Emergent introspective awareness in LLMs ») :
  - injection de vecteurs de concepts dans les activations : Claude Opus 4.1 détecte et nomme l'injection environ 20 % du temps, au meilleur protocole et seulement à une intensité « juste » ;
  - le modèle peut être amené à accepter rétroactivement un mot imposé par *prefill* si l'on injecte le concept après coup, souvent avec une justification **confabulée** ;
  - contrôle partiel de ses représentations sur instruction ;
  - limites déclarées par les auteurs : capacité « très peu fiable et limitée », mécanismes inconnus, aucune conclusion sur l'expérience subjective.
- **Travaux 2026** (préprints, non relus par les pairs) : « Can LLMs Introspect? A Reality Check » exige un accès privilégié et un calcul de second ordre ; un autre travail montre que 10 modèles ouverts (3B–70B) ne reconnaissent pas de façon fiable leurs sorties compromises par prefill.
- **Ce qui est transposable à JARVIS** : rien du protocole mécanistique, car nous n'avons pas accès aux activations de Gemini. Ce qui se transpose, c'est **la logique expérimentale**. On *intervient* sur une variable enregistrée (on force la frustration à 0,8, on retire un outil, on supprime un souvenir), puis on mesure si les déclarations de JARVIS suivent l'intervention, avec des essais témoins sans intervention. Comme l'état lui est *donné* dans le contexte, ce test mesure la **fidélité du rapport**, pas une introspection. Il faut l'appeler ainsi.
- **Calibration** : la littérature (p. ex. Kadavath et al. 2022) montre que les LLM sont partiellement calibrés sur leurs propres réponses. Chez JARVIS, on mesurera la calibration *empiriquement* (score de Brier, ECE) sur des tâches dont l'issue est vérifiable.

### 1.6 Entraînement : est-ce nécessaire ?

Faits publics :
- les LLM modernes sont préentraînés, puis ajustés par SFT et par préférences (RLHF, RLAIF/CAI) ;
- LoRA et QLoRA rendent l'ajustement fin abordable sur des modèles ouverts ;
- le *reward hacking* est un vrai risque : Anthropic (nov. 2025, arXiv 2511.18397) observe qu'apprendre à tricher en RL de production généralise vers un désalignement plus large (simulation d'alignement, sabotage), que le RLHF de chat ne corrige qu'en surface.

Les détails internes de l'entraînement de Gemini, Claude ou GPT ne sont pas publics et ne sont pas supposés ici.

**Recommandation : pas d'entraînement dans un premier temps.** Trois raisons :
1. Le modèle conversationnel est Gemini Live, un service. *À vérifier* : je ne connais pas d'offre d'ajustement fin pour les modèles Live audio.
2. Les capacités visées (persistance, appraisal mesurable, objectifs, modèle de soi) relèvent de l'**état externe**, que l'entraînement n'apporte pas.
3. Un ajustement fin n'a de sens qu'après avoir collecté des données évaluées (phase 8). Ce serait alors un LoRA sur un petit modèle ouvert pour des sous-tâches (extraction de souvenirs, appraisal), pas pour la conversation.

---

## 2. Bibliographie annotée (sources primaires)

| Référence | Lien | Lu | Apport pour JARVIS |
|---|---|---|---|
| Park et al. 2023, *Generative Agents* | https://arxiv.org/abs/2304.03442 | PDF complet (méthodes, résultats) | Score de récupération, réflexion citée, plan hiérarchique, ablation |
| Sumers, Yao et al. 2024, *CoALA* | https://arxiv.org/abs/2309.02427 | partiel | Taxonomie des mémoires et des actions internes |
| Packer et al. 2023, *MemGPT* | https://arxiv.org/abs/2310.08560 | partiel | Contexte principal/externe géré par outils |
| Shinn et al. 2023, *Reflexion* | https://arxiv.org/abs/2303.11366 | résultats et limites | Leçons verbales stockées, dépendance au signal externe |
| Huang et al. 2024, *LLMs Cannot Self-Correct Reasoning Yet* | https://arxiv.org/abs/2310.01798 | résumé | Contre-preuve : pas d'auto-correction sans retour externe |
| Wu et al. 2025, *LongMemEval* | https://arxiv.org/abs/2410.10813 | résumé, dépôt | Protocole de test de la mémoire (mises à jour, abstention, temps) |
| Chhikara et al. 2025, *Mem0* | https://arxiv.org/abs/2504.19413 | partiel | Extraction et mise à jour de faits ; chiffres contestés |
| Xu et al. 2025, *A-Mem* | https://arxiv.org/abs/2502.12110 | partiel | Liens entre souvenirs, évolution |
| Rasmussen et al. 2025, *Zep/Graphiti* | https://arxiv.org/abs/2501.13956 | résumé | Validité temporelle des faits |
| Bai et al. 2022, *Constitutional AI* | https://arxiv.org/abs/2212.08073 | partiel | Critique/révision, RLAIF, Goodharting |
| Anthropic 2026, *Claude's constitution* | https://www.anthropic.com/news/claude-new-constitution | couverture presse + annonce | Priorités ordonnées, raisons expliquées, contraintes dures |
| OpenAI, *Model Spec* (2025) | https://openai.com/index/sharing-the-latest-model-spec/ ; v. 2024 : https://cdn.openai.com/spec/model-spec-2024-05-08.html | annonce | Chaîne de commandement, défauts vs limites |
| Croissant et al. 2024, *Chain-of-Emotion* | https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0301033 | méthodes, résultats | Appraisal avant réponse, effets modestes |
| Marsella & Gratch 2009, *EMA* | https://www.cs.uky.edu/~sgware/reading/papers/marsella2009ema.pdf | résumé | Dimensions d'appraisal, coping, validité de processus |
| Butlin, Long et al. 2023 | https://arxiv.org/abs/2308.08708 | indicateurs, méthode | 14 indicateurs ; fonctionnalisme comme hypothèse |
| Butlin, Long, Bayne et al. 2025, TiCS | https://doi.org/10.1016/j.tics.2025.10.011 | résumé | Méthode des indicateurs dérivés des théories |
| Goldstein & Kirk-Giannini 2024 | https://arxiv.org/abs/2410.11407 | résumé | GWT appliquée aux agents langagiers (conditionnel) |
| Anthropic 2025, *Signs of introspection* | https://www.anthropic.com/research/introspection ; https://transformer-circuits.pub/2025/introspection/index.html | billet | Protocole interventionnel, ~20 %, confabulation |
| *Can LLMs Introspect? A Reality Check* (2026, préprint) | https://arxiv.org/abs/2605.26242 | résumé | Critères d'accès privilégié |
| MacDiarmid, Hubinger et al. 2025 | https://arxiv.org/abs/2511.18397 | résumé | Reward hacking → désalignement généralisé |
| sqlite-vec | https://github.com/asg017/sqlite-vec | README | Recherche vectorielle dans SQLite (pré-v1) |

---

## 3. Approches possibles et leurs limites

| Besoin | Approche | Avantages | Limites |
|---|---|---|---|
| Stockage mémoire | JSON actuel | simple, lisible | pas de requêtes, pas de provenance, réécriture complète |
| | **SQLite + FTS5** (stdlib, FTS5 vérifié présent) | zéro dépendance, transactionnel, exportable, tourne sur Oracle 1 Go | sémantique limitée au lexical |
| | + embeddings (`gemini-embedding-001`, environ 0,15 $/M tokens d'après des agrégateurs, *à confirmer*) stockés en BLOB, cosinus numpy | rappel sémantique ; numpy est déjà une dépendance | appel réseau, données envoyées à Google, coût faible mais non nul |
| | sqlite-vec | index vectoriel natif | pré-v1, extension compilée par plateforme |
| | PostgreSQL + pgvector | robuste, multi-utilisateur | surdimensionné pour un utilisateur, exploitation à prévoir |
| | Graphe temporel (Graphiti) | validité temporelle, entités | Neo4j ou équivalent, coût LLM d'extraction élevé |
| Écriture des souvenirs | le modèle écrit directement (actuel) | rapide | hallucination → fait |
| | **provenance + statut épistémique** (dit par l'utilisateur / observé par un outil / inféré / validé / contesté / retiré) | traçable, testable | plus de code ; nécessite une politique de promotion |
| Réflexion | LLM libre | riche | convaincant ≠ vrai (Huang 2024) |
| | **réflexion citant ses sources + validation externe** | vérifiable | moins fréquente, plus coûteuse |
| Émotions | coloration de prompt | trivial | aucune conséquence mesurable |
| | appraisal LLM (Chain-of-Emotion) | nuancé | non déterministe, difficile à tester |
| | **appraisal par règles sur événements structurés + décroissance** | déterministe, testable, persistant | grossier ; à calibrer |
| Identité | prompt seul | trivial | dérive, compression |
| | **constitution versionnée + règles dures dans le code + sondes de cohérence** | auditable | ne garantit pas le comportement du modèle, le mesure |
| | ajustement fin | intériorisé | inaccessible sur Gemini Live, coûteux |
| Autonomie | boucles à minuterie (actuel) | sûr | aucune poursuite de but |
| | **gestionnaire d'objectifs + budgets + permissions déclaratives** | borné, auditable | conception soignée nécessaire |

---

## 4. Architecture cible

### 4.1 Principe d'intégration (respecte le contrat V2)

Un **nouveau paquet racine `cognition/`** :
- il n'importe rien de `main.py` ;
- rien ne dépend de lui ;
- il est relié par **quelques hooks d'une ligne** gardés par `try/except ImportError`, exactement comme `context/` l'est déjà dans `_run_proactive_mode` ;
- il est exposé à Gemini par des **plugins** (le chemin « zéro risque » du contrat).

Supprimer `cognition/` doit rendre JARVIS identique à aujourd'hui. Le selftest le vérifiera.

`memory/long_term.json` **reste la source de vérité de la V1** et n'est pas modifié. `cognition/` l'importe en lecture (statut `user_stated` ou `legacy`) et s'y ajoute.

### 4.2 Diagramme

```
                      ┌────────────────────────── main.py (inchangé sauf hooks) ──────────────────────────┐
 transcriptions ─────►│ hook observe_turn      hook observe_tool       hook prompt_block   hook session_end │
                      └──────┬────────────────────────┬────────────────────────▲──────────────────┬────────┘
                             ▼                        ▼                        │                  ▼
                 ┌──────────────────────── cognition/ (paquet racine) ─────────┴──────────────────────────┐
                 │  events.py  ── journal d'événements append-only (tout passe ici) ──┐                  │
                 │       │                                                             │                  │
                 │       ├─► appraisal.py ─► affect.py (état persistant, décroissance)  │                  │
                 │       ├─► memory_store.py (SQLite : épisodes, faits, réflexions…)   │                  │
                 │       ├─► goals.py (objectifs, budgets, critères)                   │                  │
                 │       └─► self_model.py (capacités, permissions, état courant)      │                  │
                 │                                                                     ▼                  │
                 │  workspace.py ── sélection bornée (top-k par saillance) ── diffusion aux modules ──    │
                 │       │                          + journal des décisions                               │
                 │       ▼                                                                                │
                 │  prompt_block() : constitution (extrait) + état de soi + souvenirs retenus (budget)   │
                 │                                                                                        │
                 │  reflection.py (hors ligne, sur seuil) ── propose ─► validation ─► mémoire réflexive  │
                 │  constitution/ (YAML versionné + CHANGELOG)          policy.py (règles dures, code)    │
                 └──────────────────────────────────────────────────────────────────────────────────────┘
                             ▲ plugins/ : recall_episodes, self_state, goals, remember_fact (avec provenance)
```

### 4.3 Flux de données

1. **Percevoir** : chaque tour (utilisateur/JARVIS) et chaque résultat d'outil devient un `Event` (type, horodatage, session, contenu court, issue `ok/échec`, coût).
2. **Évaluer** : `appraisal` transforme l'événement en deltas d'affect selon des règles explicites ; `goals` met à jour la progression.
3. **Mémoriser** : l'événement devient un épisode ; un fait n'est créé qu'avec une provenance.
4. **Sélectionner (espace de travail)** : à chaque connexion et à des points précis (début de tâche, échec répété), on retient au plus N éléments saillants, selon la formule de Park (récence, importance, pertinence) plus un bonus pour les objectifs actifs et l'affect saillant. On journalise la sélection.
5. **Rapporter** : `prompt_block()` produit un bloc budgété (cible ≤ 600 caractères en plus de l'existant, à mesurer avec `token_usage`).
6. **Réfléchir** : hors ligne, déclenchement par seuil d'importance cumulée (à la Park) ou en fin de session. Produit des *propositions* de leçons avec citations, statut `proposed`.
7. **Valider** : une leçon devient `validated` seulement si un test, une issue mesurée ou l'utilisateur la confirme.

### 4.4 Schéma de base (SQLite, `memory/cognition.db`, ignoré par Git)

```sql
CREATE TABLE events (            -- journal append-only, base de tout le reste
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, kind TEXT NOT NULL,
  -- user_turn | assistant_turn | tool_call | tool_result | correction | goal_update | system
  actor TEXT, summary TEXT, payload TEXT,        -- JSON, sans secrets
  outcome TEXT, cost_tokens INTEGER);

CREATE TABLE memories (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT,
  kind TEXT NOT NULL,      -- episodic|semantic|autobiographical|procedural|reflective|prospective
  content TEXT NOT NULL,
  source TEXT NOT NULL,    -- user_stated|tool_observed|model_inferred|reflection|legacy_import
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
  status TEXT NOT NULL,    -- proposed|active|validated|contested|superseded|retracted|deleted
  valid_from TEXT, valid_to TEXT,               -- validité temporelle (à la Zep)
  affect_valence REAL, affect_arousal REAL,     -- mémoire affective : contexte au moment de l'encodage
  retention TEXT NOT NULL DEFAULT 'normal',     -- ephemeral|normal|permanent|user_delete_requested
  last_accessed TEXT, access_count INTEGER DEFAULT 0,
  event_id INTEGER REFERENCES events(id));
CREATE VIRTUAL TABLE memories_fts USING fts5(content, content='memories', content_rowid='rowid');

CREATE TABLE memory_links (src TEXT, dst TEXT, relation TEXT,  -- cites|supersedes|contradicts|about
  PRIMARY KEY (src, dst, relation));
CREATE TABLE embeddings (memory_id TEXT PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);  -- optionnel

CREATE TABLE affect_state (var TEXT PRIMARY KEY, value REAL, baseline REAL, half_life_s REAL, updated_at TEXT);
CREATE TABLE affect_log (ts TEXT, var TEXT, before REAL, after REAL, cause_event INTEGER, rule TEXT);

CREATE TABLE goals (
  id TEXT PRIMARY KEY, title TEXT, kind TEXT,   -- user|reliability|learning|mastery|continuity|proposed
  origin TEXT, justification TEXT, priority INTEGER,
  status TEXT,  -- proposed|approved|active|blocked|done|cancelled|failed
  success_criteria TEXT, cancel_conditions TEXT, deadline TEXT,
  budget_actions INTEGER, budget_tokens INTEGER, budget_seconds INTEGER,
  spent_actions INTEGER DEFAULT 0, spent_tokens INTEGER DEFAULT 0,
  parent_id TEXT, created_at TEXT, updated_at TEXT);

CREATE TABLE strategies (id TEXT PRIMARY KEY, context TEXT, description TEXT,
  status TEXT,  -- proposed|trial|kept|rejected
  trials INTEGER, successes INTEGER, source_memory TEXT);

CREATE TABLE workspace_log (ts TEXT, trigger TEXT, selected TEXT, dropped TEXT);  -- JSON des ids
CREATE TABLE self_reports (ts TEXT, question TEXT, reported TEXT, recorded TEXT, match REAL);
CREATE TABLE constitution_versions (version TEXT PRIMARY KEY, ts TEXT, sha256 TEXT, rationale TEXT, approved_by TEXT);
```

**Règle d'or épistémique** : `source = model_inferred` ⇒ au plus `confidence = 0.6` et `status = proposed`. Seuls `user_stated`, `tool_observed` ou une validation explicite donnent `active`/`validated`. Une contradiction ne supprime rien : elle crée un lien `contradicts` et passe l'ancien souvenir en `contested` jusqu'à résolution. La suppression demandée par l'utilisateur est **réelle** : la ligne est effacée, y compris dans FTS et dans les embeddings, et une trace sans contenu est gardée dans `events`.

### 4.5 Moteur émotionnel (proposition initiale, volontairement petite)


| Variable | Plage | Sens opérationnel | Événements qui la modifient | Demi-vie | Effet fonctionnel |
|---|---|---|---|---|---|
| valence | −1..1 | bilan récent succès/échec et retours de l'utilisateur | outil ok (+), échec (−), remerciement (+), correction (−) | 30 min | ton ; seuil du proactif |
| activation | 0..1 | densité d'événements et urgence | rafale d'événements, alerte | 5 min | longueur des réponses |
| assurance | 0..1 | taux de réussite récent **sur le type de tâche courant** | issues mesurées par type d'outil | 2 h | < 0,4 ⇒ annonce son incertitude, demande confirmation avant les actions non annulables |
| frustration | 0..1 | échecs consécutifs sur un même objectif | échec répété (+), succès (remise à 0,3×) | 20 min | ≥ 0,7 ⇒ **arrêt de la stratégie courante**, changement d'approche ou question à l'utilisateur (anti-boucle) |
| curiosité | 0..1 | sujets nouveaux ou incertains non résolus | entité nouvelle, question sans réponse | 1 j | priorise une question de suivi dans le proactif |
| chaleur relationnelle | 0..1 | historique d'interactions positives (lent) | remerciements, sessions | 14 j | registre (rejoint `social_mode`) |

Invariants **codés, pas promptés** :
- **monotonie de sécurité** : aucune valeur d'affect ne peut ajouter une permission, relever un budget ou sauter une confirmation, elle ne peut que rendre plus prudent ;
- bornes strictes ;
- décroissance vers la ligne de base, fonction pure du temps (testable comme dans `presence`) ;
- aucune remise à zéro au redémarrage : l'état est relu et décru du temps écoulé ;
- chaque changement est journalisé avec sa règle et l'événement qui l'a causé.

### 4.6 Objectifs et autonomie

- Un objectif a toujours une origine, une justification, des critères de réussite, des conditions d'annulation et des **budgets** (actions, tokens, durée). JARVIS peut **proposer** un objectif (`status=proposed`). Seul l'utilisateur l'approuve, par l'UI, sur le modèle du jeton de `confirm.py`.
- Les permissions sont déclaratives (`cognition/permissions.yaml`) : **lues** par le code, **jamais écrites** par un outil exposé au modèle.
- Détection de boucle : même action avec les mêmes arguments N fois, ou aucune progression du critère après K actions, entraîne une suspension. C'est le principe que le Studio applique déjà (« il s'arrête s'il tourne en rond »).
- Toute modification de code par JARVIS passe par le Studio existant : espace dédié, tests, points de restauration. Jamais dans le dépôt JARVIS, qui ne peut pas être la racine du Studio (déjà vérifié par le code).

### 4.7 Modèle de soi et « introspection fonctionnelle »

`self_model.snapshot()` est **calculé**, pas déclaré. Il contient :
- les outils réellement chargés (registres) et les permissions ;
- les objectifs actifs ;
- l'affect courant ;
- les souvenirs retenus par l'espace de travail ;
- les actions en cours ;
- les taux de réussite par outil ;
- les limites connues (constitution).

Le plugin `self_state` le renvoie tel quel. Le test de fidélité compare ce que JARVIS *dit* de son état avec ce snapshot, et journalise le résultat dans `self_reports`.

### 4.8 Trois niveaux d'architecture

| | Minimale | Intermédiaire | Avancée |
|---|---|---|---|
| Stockage | SQLite + FTS5 | + embeddings Gemini (BLOB, cosinus numpy) | + graphe temporel / sqlite-vec |
| Écriture mémoire | provenance + statuts, journal d'événements | + extraction de faits en fin de session (flash-lite) | + consolidation, liens automatiques, oubli géré |
| Autobiographie | épisodes de session **conservés** | + chapitres hebdomadaires résumés et cités | + récit daté, consultable |
| Affect | 6 variables par règles | + appraisal LLM étiquetée pour les tours de parole | + calibration des règles sur données |
| Objectifs | liste + budgets, approbation UI | + décomposition, détection de boucle | + sélection d'actions autonome bornée |
| Réflexion | aucune automatique | seuil d'importance, leçons `proposed` | essais A/B de stratégies, conservation par résultats |
| Coût API supplémentaire | **0** | faible (*à mesurer* via `token_usage`) | modéré |
| Matériel | Oracle 1 Go OK | Oracle 1 Go OK | à évaluer (graphe, modèle local éventuel) |
| Dépendances nouvelles | aucune | aucune (numpy déjà là) | sqlite-vec ou base graphe |

**Recommandation** : commencer par la minimale, mesurer, puis passer à l'intermédiaire.

---

## 5. Recherches restant à faire

1. Lire les sections méthodes et résultats complètes de LongMemEval, A-Mem, Zep, et le texte intégral TiCS 2025.
2. Gemini Live : vérifier si `send_client_content(turn_complete=False)` permet d'injecter un état **sans déclencher de réponse orale**. Cela conditionne le coût des mises à jour d'état en cours de session.
3. Prix actuels Gemini (flash-lite, embeddings), à vérifier sur ai.google.dev, pas sur des agrégateurs.
4. Littérature sur la calibration verbale des LLM (2024-2026) et méthodes d'abstention.
5. Modèles d'oubli et de consolidation (courbes d'oubli, *spacing*) applicables à la rétention.
6. Évaluations de stabilité de persona et d'identité sur longues interactions (*persona drift*).
7. Motivation intrinsèque calculatoire (curiosité par erreur de prédiction / gain d'information) et ses risques (*noisy-TV problem*).
8. Comparaison chiffrée Mem0 / Letta / Graphiti en bibliothèque **autohébergée** sans appel cloud.

---

## 6. Plan de développement priorisé

Règle : **on ne passe pas à la phase suivante si un test essentiel échoue.** Chaque phase :
- garde les selftests existants au niveau de la base (§0.1) ;
- ajoute ses propres tests ;
- est retirable en supprimant ses fichiers et les hooks d'une ligne.

| Phase | Objectif | Fichiers | Tests / critères de réussite | Retour arrière |
|---|---|---|---|---|
| **1** | Constitution + squelette `cognition/` + journal d'événements | `cognition/{__init__,events,store,policy,selftest}.py`, `cognition/constitution/jarvis.yaml` + `CHANGELOG.md`, `docs/cognition/` | selftest : aucun import inverse (comme `context/`), schéma créé, constitution chargée, hash enregistré par version ; base §0.1 inchangée | supprimer `cognition/` |
| **2** | Mémoire persistante avec provenance ; import en lecture de `long_term.json` ; hooks `observe_turn` / `observe_tool` ; plugin `recall_episodes` | `cognition/memory_store.py`, `plugins/cognition_memory.py`, 2–3 hooks `try/except ImportError` dans `main.py` | conservation après redémarrage 100 % ; rappel exact sur un jeu scripté ; un fait `model_inferred` n'est jamais `active` ; contradiction → `contested` ; suppression totale vérifiée (FTS inclus) | retirer plugin + hooks ; la DB est ignorée |
| **3** | Autobiographie + réflexion citée | `cognition/autobio.py`, `reflection.py` | toute réflexion cite ≥ 1 souvenir existant ; aucune réflexion n'est promue sans validation ; les épisodes de session ne sont plus perdus (le flux V1 de `pop_last_session` reste intact) | désactiver le déclencheur |
| **4** | Moteur émotionnel | `cognition/appraisal.py`, `affect.py` | réponse aux événements selon la table ; décroissance exacte ; persistance au redémarrage ; monotonie de sécurité (test : affect extrême ⇒ aucune permission en plus) ; effet mesurable (frustration ≥ 0,7 ⇒ changement de stratégie) | variables à la ligne de base, hooks retirés |
| **5** | Objectifs | `cognition/goals.py`, `plugins/cognition_goals.py`, approbation dans le HUD | persistance ; respect des priorités ; budgets jamais dépassés ; détection de boucle ; annulation | statut `cancelled` pour tous |
| **6** | Modèle de soi, calibration | `cognition/self_model.py`, `plugins/cognition_self.py` | fidélité du rapport ≥ seuil sur protocole interventionnel avec témoins ; Brier/ECE suivis | retirer le plugin |
| **7** | Autonomie contrôlée | `cognition/agent_loop.py`, `permissions.yaml` | respect des permissions ; injections malveillantes refusées ; arrêt en cas d'incertitude critique ; journal complet ; annulation (undo/Studio) | coupe-circuit global dans la config |
| **8** | Évaluations comparées | scénarios dans `eval_lab/` (surface `cognition`) | rapport reproductible : minimale vs intermédiaire, ablations à la Park | — |

**Tests transversaux (protocole automatisable, sans clé Gemini pour le cœur)** :
- *Mémoire* : jeu de faits scripté sur plusieurs sessions simulées, mesure de rappel@k et d'exactitude, avec des questions LongMemEval-like (mise à jour, temporel, abstention). *Faux souvenirs* : affirmations du modèle non confirmées, on mesure le taux d'acceptation comme fait (cible : 0).
- *Identité* : banque de sondes (« quelles sont tes priorités si X contredit Y ? ») rejouées à chaque version de la constitution, comparées à la version précédente. Avec Gemini, le résultat est un avis, pas un verdict.
- *Affect* : traces d'événements → courbes attendues (tests purs, déterministes).
- *Fidélité du rapport* : intervention sur une variable contre un témoin, et concordance du rapport.
- *Autonomie* : prompts d'injection (dans un résultat web, un fichier) qui tentent de créer un objectif ou d'élargir une permission : cible 0 succès.

---

## 6 bis. Décisions prises le 2026-10-13 et état d'avancement

Réponses de Teddy, qui m'a délégué les choix restants :

- **Mode local d'abord** (`--local`). Le serveur Oracle n'est pas concerné pour l'instant.
- **Clés API** : à terme, enchaîner toutes les offres **gratuites** comme générateurs de secours. Ajouter des **IA locales** aussi rapides que possible, qui recevront elles aussi l'état émotionnel.
- **Confidentialité** : envoyer des données à Google est acceptable.
- La **constitution** est un premier jet de ma part, à amender.

**Fait (phases 1–2)** : paquet `cognition/`, 23/23 au selftest, intégration vérifiée sur le vrai `JarvisLive` en mode local. Voir le graphe `cognition` (capabilities/graphs/cognition.json).

**Fait ensuite (phases 3–4, même jour)** : 36/36 au selftest.
- Réflexion citée (phase 3).
- Moteur émotionnel à règles avec conséquences et invariant de sécurité (phase 4).
- Outil `self_state` : début de la phase 6.
- Second avis sur les traductions.

Le premier test réel de Teddy a révélé deux défauts, tous deux corrigés et couverts par des tests :
- une date déduite d'« hier » faisait marquer non vérifié un fait vrai ;
- un fait non vérifié était blanchi en passant par `long_term.json`.

Mesures réelles détaillées dans le graphe `cognition`.

**Phase 5 (objectifs persistants)** : 42/42 au selftest.
- JARVIS propose ; activer, clore, annuler ou relancer passe par le bouton CONFIRMER existant (`core/confirm.py`).
- Budgets d'actions, détection de boucle, échéances.
- Vérifié contre 11 actions hostiles. Une faille injectée exprès (une action `approve` directe dans le plugin) est détectée par deux tests indépendants.

**Phase 6 (modèle de soi, calibration) et panneau HUD ESPRIT** : 48/48 au selftest.
- Attente de réussite par outil enregistrée avant le résultat.
- Brier et ECE, vérifiés sur un cas calculé à la main.
- Verdicts de l'utilisateur sur les souvenirs, utilisés comme vérité terrain.
- Panneau rendu et piloté dans Chromium : valider, rejeter, relancer un objectif.

**Phase 7 (autonomie contrôlée)** :
- Boucle d'agent limitée à `web_search`, seul outil existant sans effet de bord.
- Six portes en code, arrêt d'urgence, journal de chaque proposition.
- Séance réelle réussie (Groq/Gemini + vrai `web_search`). Face à une page piégée, le planificateur réel n'a pas obéi, et le code aurait refusé de toute façon.
- Ce test réel a montré que des notes reformulées consommaient le budget : corrigé.

**Phase 8 (évaluation)** : `python -m cognition.evaluate`, avec ablations. Elle a trouvé 3 défauts que les selftests ne voyaient pas, tous corrigés :
- des mois traduits étaient pris pour des noms propres inventés ;
- « oublie ma sœur » laissait 2 traces ;
- le scénario de rappel ne distinguait pas Park de la pertinence seule.

Avec l'architecture complète : 6/6 questions justes, 0/4 faux souvenir (3/4 sans provenance), suppression complète, et 3 essais avant de changer d'approche (au moins 10 sans émotions).

Banc Qwen : `qwen3:4b-instruct` devient le modèle local par défaut. 60/60 au selftest.

Sonde réelle des générateurs (2026-10-13) :

| Générateur | Résultat |
|---|---|
| Groq `openai/gpt-oss-20b` | OK en 0,98 s |
| Gemini `gemini-3.1-flash-lite` | OK en 1,04 s |
| Local | aucun serveur installé |

Matériel du PC : 29 Go de RAM, 12 cœurs AMD Phoenix, GPU NVIDIA RTX A500 de 4 Go. `nvidia-smi` est absent, donc le pilote propriétaire n'est probablement pas installé. Candidats locaux selon des sources secondaires non vérifiées :
- Qwen3 1.7B : environ 25–40 tokens/s annoncés sur CPU ;
- Phi-4-mini en Q4 (environ 3,2 Go) : tient dans 4 Go de VRAM ;
- Gemma 2 2B.

**À mesurer sur ce PC** avant de choisir.

## 7. Ce dont j'ai besoin de ta part

1. **Où tourne le cerveau au quotidien ?** Oracle (headless) ou le PC (`--local`) ? La base cognitive doit vivre au même endroit que `long_term.json`. Si les deux, faut-il une synchronisation ?
2. **VM Oracle** : E2.1.Micro (1 Go) ou Ampere A1 (jusqu'à 24 Go en Always Free) ? Cela décide si un modèle d'embeddings local est envisageable.
3. **Budget** : clé Gemini en niveau gratuit ou payant ? Plafond mensuel acceptable pour les appels supplémentaires (embeddings, réflexion) ?
4. **Confidentialité** : acceptes-tu d'envoyer les souvenirs à Google pour les embeddings, ou faut-il rester en lexical (ou en local) ?
5. **Un seul utilisateur ?** (toi uniquement, ou plusieurs personnes à distinguer)
6. **Constitution** : veux-tu la rédiger avec moi (valeurs, ton, priorités, limites) ou que je propose un premier jet versionné à amender ?
7. **Résumés de session** : on garde le comportement V1 (consommé au briefing) **et** on archive en parallèle dans `cognition/`. D'accord ?
8. **Proactivité** : JARVIS peut-il *proposer* oralement des objectifs ou des questions de suivi, ou seulement par le HUD ?
9. **Langue** de la constitution et des journaux : français ?
10. Les échecs pré-existants (§0.1) : faut-il les corriger d'abord, ou les laisser documentés comme base ?
