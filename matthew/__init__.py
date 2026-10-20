"""
matthew/ — le futur cerveau à nous : un modèle ouvert, entraîné sur nos échanges.

Aujourd'hui JARVIS pense avec Gemini. Matthew est la transition : un modèle aux
poids ouverts (Qwen3.5, Apache-2.0), spécialisé sur ce que l'utilisateur
demande vraiment, servi par Ollama sur la machine, avec ses propres tokens.
Gemini reste en place ; Matthew apprend à côté, puis prend des tâches une à
une quand l'évaluation prouve qu'il fait aussi bien.

    journal.py    capte chaque tour (demande, outils, résultats), nettoyé
    teacher.py    fait réécrire les réponses par un modèle ouvert « professeur »
    dataset.py    assemble train / eval / dpo, sans aucune cible Gemini
    train.py      prépare (et lance si le GPU le permet) un fine-tuning LoRA
    evaluate.py   note un candidat, ne le promeut que s'il bat le champion
    flywheel.py   enchaîne le tout : la boucle d'amélioration automatique
    client.py     appels compatibles OpenAI, et le compteur de tokens de Matthew

RÈGLE DE LICENCE (non négociable, vérifiée par le selftest)
    Conditions de l'API Gemini, version du 2026-03-23 : « You may not use the
    Services to develop models that compete with the Services ». Une réponse
    écrite par Gemini n'est donc JAMAIS une cible d'entraînement. Les cibles
    viennent de l'utilisateur (corrections) ou d'un professeur sous licence
    ouverte. Les demandes de l'utilisateur et les résultats réels des outils
    sont à lui ; ils servent d'entrées.

Paquet racine (contrat V2) : bibliothèque standard + requests. Le retirer, avec
plugins/matthew.py et les trois hooks d'une ligne (cognition/runtime.py,
cognition/providers.py, offline/llm.py), rend JARVIS identique à avant.
Les données vivent dans memory/matthew/ (ignoré par Git), ou sous
JARVIS_DATA_DIR en mode portable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

NAME = "Matthew"
EPISODE_SCHEMA = "matthew.episode/1"
REPO = Path(__file__).resolve().parent.parent

#: Les origines qu'on a le droit d'apprendre comme réponse.
TRAINABLE_ORIGINS = frozenset({"human", "teacher"})
#: Licences acceptées pour un professeur (ses sorties deviennent des cibles).
OPEN_LICENSES = frozenset({"apache-2.0", "mit"})

DEFAULTS: dict = {
    "collect": True,
    # Modèle de base : 4B tient en LoRA bf16 dans les 12 Go de la RTX 3060
    # (Unsloth, ~10 Go) ; 9B en demande ~22 : GPU loué.
    "base_model": "Qwen/Qwen3.5-4B",
    "base_license": "apache-2.0",
    "serve_model": "matthew",
    "endpoint": "http://127.0.0.1:11434/v1",
    # Professeur : modèle ouvert, plus fort que l'élève. Clé groq_api_key
    # présente → gpt-oss-120b chez Groq, sinon le modèle local d'Ollama.
    "teacher": None,
    "teacher_budget": 60,            # demandes réécrites par cycle (quotas gratuits)
    "bootstrap_budget": 150,         # appels d'amorçage par cycle, tant qu'un outil manque
    "match_mode": "trace",           # "trace" | "schema" — voir teacher.py
    "min_new_examples": 60,          # ~1 nuit d'amorçage ; un cycle coûte ~20 min de GPU
    "eval_share": 0.15,              # les plus récents → évaluation (hold-out temporel)
    "eval_repeats": 2,               # les bancs d'outils varient d'une passe à l'autre
    "promote_margin": 0.02,
    # Environnement d'entraînement séparé (Python 3.12 + torch CUDA + unsloth),
    # et cache des modèles Hugging Face : sur D: par défaut, C: étant plein.
    "train_python": r"D:\DEV\matthew-train\.venv\Scripts\python.exe",
    "hf_home": r"D:\DEV\matthew-train\hf",
    "use_in_cognition": True,
    # Autres bases essayées à tour de rôle (licence ouverte obligatoire) ; un challenger
    # qui bat le champion devient la base. Ex. : [{"model": "Qwen/Qwen3.5-9B", "license": "apache-2.0"}]
    "base_challengers": [],
    # Vitrine publique (matthew/vitrine.py) : clone local du dépôt GitHub Pages, publié
    # après chaque cycle. Approuvé par l'utilisateur le 2026-10-20 ; false la coupe.
    "vitrine_repo": r"D:\DEV\matthew-vitrine",
    "vitrine_publish": True,
}


def data_dir() -> Path:
    """memory/matthew, ou <JARVIS_DATA_DIR>/matthew (clé USB), ou MATTHEW_HOME."""
    env = os.environ.get("MATTHEW_HOME", "").strip()
    if env:
        d = Path(env)
    else:
        portable = os.environ.get("JARVIS_DATA_DIR", "").strip()
        d = Path(portable) / "matthew" if portable else REPO / "memory" / "matthew"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _api_keys() -> dict:
    try:
        return json.loads((REPO / "config" / "api_keys.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def config(raw: dict | None = None) -> dict:
    """Réglages : DEFAULTS, puis la section "matthew" d'api_keys.json."""
    keys = _api_keys() if raw is None else raw
    cfg = {**DEFAULTS, **(keys.get("matthew") or {})}
    if not cfg.get("teacher"):
        if str(keys.get("groq_api_key") or "").strip():
            cfg["teacher"] = {"base_url": "https://api.groq.com/openai/v1",
                              "model": "openai/gpt-oss-120b", "license": "apache-2.0",
                              "verifier_model": "openai/gpt-oss-20b",
                              "api_key": keys["groq_api_key"]}
        else:
            cfg["teacher"] = {"base_url": "http://127.0.0.1:11434/v1",
                              "model": "qwen3:4b-instruct", "license": "apache-2.0"}
    return cfg


def champion() -> dict | None:
    try:
        return json.loads((data_dir() / "champion.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ready(floor: float = 0.5) -> bool:
    """Un champion promu par la boucle, dont le score dépasse le plancher."""
    c = champion()
    return bool(c and ((c.get("score") or {}).get("composite") or 0) >= floor)


def cognition_order(order: list[str], keys: dict | None = None) -> list[str]:
    """Hook de cognition/providers.py (phase 1 de la transition) : Matthew passe
    en tête des tâches de fond quand il est prêt. Un ordre écrit à la main dans
    api_keys.json (cognition_provider_order) gagne toujours ; "use_in_cognition":
    false dans la section matthew le coupe."""
    keys = keys or {}
    if keys.get("cognition_provider_order") or "matthew" in order:
        return list(order)
    if not config(keys).get("use_in_cognition", True) or not ready():
        return list(order)
    return ["matthew"] + list(order)


def paused() -> bool:
    return (data_dir() / "PAUSED").exists()


def attach(jarvis):
    """Hook d'une ligne au début de cognition.attach (appelé par chaque
    JarvisLive, quel que soit le mode) : branche le journal. Ne lève jamais."""
    # Un faux JarvisLive (selftests de la cognition) n'a pas de _execute_tool : rien à capter.
    if not hasattr(jarvis, "_execute_tool") or not hasattr(getattr(jarvis, "ui", None), "write_log"):
        return None
    if not config().get("collect", True):
        return None
    try:
        from matthew.journal import Tap
        return Tap.install(jarvis)
    except Exception as e:
        print(f"[Matthew] journal désactivé : {type(e).__name__}: {e}")
        return None
