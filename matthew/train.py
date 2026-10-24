"""
matthew/train.py — préparer, et lancer si la machine le permet, un entraînement.

Écrit dans runs/<date>/ :
    train_matthew.py   script Unsloth autonome (LoRA bf16, réponses seules)
    Modelfile          pour `ollama create` à partir du GGUF exporté
    plan.json          réglages, jeu de données, et commande à lancer

Choix fondés sur la recherche du 2026-10-20 (graphe matthew) :
  - Qwen3.5-4B, Apache-2.0 : LoRA bf16 ~10 Go, tient dans la RTX 3060 (12 Go).
    Unsloth déconseille le QLoRA 4 bits pour Qwen3.5 ; 9B (~22 Go) = GPU loué.
  - CHAQUE cycle repart du modèle de base, jamais du candidat précédent :
    l'entraînement itératif sur ses propres sorties amplifie des traits
    (arXiv 2605.01130), surtout en DPO ; réinitialiser l'évite. L'historique
    complet est réappris à chaque fois (mélange ancien + neuf, Agent-in-the-Loop).
  - Perte sur les réponses seulement ; réflexion coupée (enable_thinking=False) :
    une réponse vocale rapide d'abord.
  - Export GGUF q4_k_m avec le gabarit de chat d'entraînement : un gabarit
    différent à l'inférence est la première cause de modèle « dégradé ».

Rien n'est installé ici. `execute()` ne lance le script que si unsloth et torch
sont importables ; sinon il dit quoi installer.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from matthew import config, data_dir

# Recherche du 2026-10-20 (graphe matthew, nœud r.reglages) :
#  - Époques évaluées à chacune ; on garde la meilleure sur la perte d'évaluation
#    (Unsloth : 1–3 époques, au-delà sur-apprentissage ; arrêt précoce sur le hold-out).
#    3 → 2 le 2026-10-21 : la meilleure fut la 2e, la 2e et la 1re aux trois cycles réels ;
#    la 3e n'a jamais servi (≈ 10 min de carte graphique par cycle).
#  - Q5_K_M plutôt que Q4_K_M : un 4B perd davantage à la quantification, et un appel
#    d'outil est une sortie exacte (comme le code, qu'on conseille ≥ Q5).
#  - r = alpha = 16, lr 2e-4 : point de départ conseillé ; rsLoRA inutile à ce rang.
HYPER = {"lora_r": 16, "lora_alpha": 16, "lora_dropout": 0.0, "lr": 2e-4, "epochs": 2,
         "batch": 1, "grad_accum": 8, "max_seq": 2048, "warmup_ratio": 0.05, "seed": 3407,
         "quant": "q5_k_m"}

SCRIPT = r'''"""Entraînement de Matthew — généré par matthew/train.py. Lancer : python train_matthew.py"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
PLAN = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
H = PLAN["hyper"]
# Chemins relatifs au dossier de l'entraînement (paquet envoyé à Kaggle), absolus sur le PC.
here = lambda p: Path(p) if Path(p).is_absolute() else HERE / p

# bf16 sur la RTX 3060 ; la T4 de Kaggle ne le gère pas : fp16.
import torch
BF16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
print("précision :", "bf16" if BF16 else "fp16", "·", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "sans GPU")

from unsloth.chat_templates import train_on_responses_only
from datasets import Dataset
from trl import SFTTrainer, SFTConfig

# Qwen3.5 porte un encodeur d'images : selon la version d'Unsloth, il se charge
# par FastLanguageModel ou par FastModel. On essaie l'un puis l'autre.
try:
    from unsloth import FastLanguageModel as FM
    model, tok = FM.from_pretrained(
        PLAN["base_model"], max_seq_length=H["max_seq"], load_in_4bit=False, load_in_16bit=True)
except Exception as e:
    print("FastLanguageModel refuse ce modèle, essai FastModel :", e)
    from unsloth import FastModel as FM
    model, tok = FM.from_pretrained(
        PLAN["base_model"], max_seq_length=H["max_seq"], load_in_4bit=False, load_in_16bit=True)
model = FM.get_peft_model(
    model, r=H["lora_r"], lora_alpha=H["lora_alpha"], lora_dropout=H["lora_dropout"], bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth", random_state=H["seed"])

def render(row):
    # Le gabarit de Qwen3.5 parcourt les arguments (|items) : un dict, pas du JSON en texte.
    for m in row["messages"]:
        for c in m.get("tool_calls") or []:
            if isinstance(c["function"]["arguments"], str):
                c["function"]["arguments"] = json.loads(c["function"]["arguments"] or "{}")
    kw = {"tools": row["tools"]} if row.get("tools") else {}
    try:
        text = tok.apply_chat_template(row["messages"], tokenize=False, enable_thinking=False, **kw)
    except TypeError:
        text = tok.apply_chat_template(row["messages"], tokenize=False, **kw)
    return {"text": text}

# Lu à la main : les schémas d'outils varient d'une ligne à l'autre, ce
# qu'Arrow (load_dataset("json")) ne sait pas unifier. Seul le texte rendu passe.
def rows(path):
    p = Path(path)
    if not p.exists():
        return []
    return [render(json.loads(l)) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

train_ds = Dataset.from_list(rows(here(PLAN["train"])))
eval_rows = rows(here(PLAN["eval"]))
print("exemples :", len(train_ds), "entraînement,", len(eval_rows), "évaluation")
print(train_ds[0]["text"][:1500])
# Avec un hold-out : évaluer à chaque époque et garder la meilleure (arrêt précoce).
best = dict(eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
            metric_for_best_model="eval_loss", greater_is_better=False, save_total_limit=2)     if eval_rows and H.get("max_steps", -1) < 0 else {}
trainer = SFTTrainer(
    model=model, tokenizer=tok, train_dataset=train_ds,
    eval_dataset=Dataset.from_list(eval_rows) if eval_rows else None,
    args=SFTConfig(**best, dataset_text_field="text", max_seq_length=H["max_seq"],
                   per_device_train_batch_size=H["batch"], gradient_accumulation_steps=H["grad_accum"],
                   num_train_epochs=H["epochs"], max_steps=H.get("max_steps", -1),
                   learning_rate=H["lr"], warmup_ratio=H["warmup_ratio"],
                   optim="adamw_8bit", logging_steps=5, seed=H["seed"], output_dir=str(HERE / "ckpt"),
                   report_to="none", bf16=BF16, fp16=not BF16))
trainer = train_on_responses_only(trainer, instruction_part=PLAN["instruction_part"],
                                  response_part=PLAN["response_part"])
stats = trainer.train()
model.save_pretrained(str(HERE / "lora"))
tok.save_pretrained(str(HERE / "lora"))
# Dans le tournoi du cloud (matthew/kaggle.py), deux entraînements partagent le disque : un seul
# export à la fois (~17 Go le temps de la conversion), et la place est rendue avant le suivant.
import os, shutil
LOCK = os.environ.get("MATTHEW_EXPORT_LOCK")
if LOCK:
    import fcntl
    _lock = open(LOCK, "w")
    fcntl.flock(_lock, fcntl.LOCK_EX)
    shutil.rmtree(HERE / "ckpt", ignore_errors=True)
model.save_pretrained_gguf(str(HERE / "gguf"), tok, quantization_method=H["quant"])
if LOCK:
    for g in list(HERE.glob("gguf*/*")):
        n = g.name.upper()
        if g.suffix == ".safetensors" or (g.suffix == ".gguf" and any(x in n for x in ("BF16", "F16", "F32"))):
            g.unlink()
(HERE / "result.json").write_text(json.dumps({
    "train_loss": stats.training_loss, "best_eval_loss": trainer.state.best_metric,
    "train_runtime": stats.metrics.get("train_runtime"),
    "log": [h for h in trainer.state.log_history if "eval_loss" in h]}), encoding="utf-8")
print("MATTHEW_TRAIN_OK", stats.training_loss)
'''


# RENDERER / PARSER qwen3.5 : le format de chat et d'appels d'outils de Qwen3.5,
# intégré à Ollama (lu sur qwen3.6 le 2026-10-20, Ollama 0.40.6). Sans eux, le
# modèle n'a pas la capacité « tools » et offline/llm.py ne le choisit jamais.
MODELFILE = """# ollama create matthew-candidate -f Modelfile
FROM ./gguf/unsloth.Q4_K_M.gguf
RENDERER qwen3.5
PARSER qwen3.5
PARAMETER temperature 0.4
PARAMETER top_p 0.9
PARAMETER num_ctx 8192
"""


def train_python(cfg: dict | None = None) -> str:
    """Le Python d'entraînement configuré s'il existe, sinon celui-ci."""
    p = (cfg or config()).get("train_python")
    return p if p and Path(p).exists() else sys.executable


def train_env(cfg: dict | None = None) -> dict:
    import os
    env = dict(os.environ)
    hf = (cfg or config()).get("hf_home")
    if hf:
        env["HF_HOME"] = hf
    env["PYTHONIOENCODING"] = "utf-8"
    # Sortie non tamponnée : sinon les pertes n'arrivent au direct que par blocs de 8 Ko
    # (constaté au deuxième cycle réel, 2026-10-21).
    env["PYTHONUNBUFFERED"] = "1"
    return env


def missing_deps(cfg: dict | None = None) -> list[str]:
    py = train_python(cfg)
    if py == sys.executable:
        return [m for m in ("torch", "unsloth", "trl", "datasets") if importlib.util.find_spec(m) is None]
    probe = ("import importlib.util as u;print(','.join(m for m in ('torch','unsloth','trl','datasets')"
             " if u.find_spec(m) is None))")
    try:
        cp = subprocess.run([py, "-c", probe], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"python d'entraînement injoignable ({e})"]
    return [m for m in cp.stdout.strip().split(",") if m]


def plan(dataset_dir: Path, cfg: dict | None = None, root: Path | None = None, clock=time.time,
         **hyper) -> Path:
    """`hyper` remplace des réglages (ex. max_steps=5 pour un essai de bout en bout)."""
    cfg = cfg or config()
    dataset_dir = Path(dataset_dir)
    run = (root or data_dir()) / "runs" / datetime.fromtimestamp(clock()).strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    p = {"schema": "matthew.run/1", "base_model": cfg["base_model"],
         "base_license": cfg["base_license"], "from": "base",
         "train": str(dataset_dir / "train.jsonl"), "eval": str(dataset_dir / "eval.jsonl"),
         "manifest": json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8")),
         "hyper": {**HYPER, **hyper},
         # Gabarit ChatML des Qwen : la perte ne porte que sur ce qui suit l'assistant.
         "instruction_part": "<|im_start|>user\n", "response_part": "<|im_start|>assistant\n",
         "command": f'"{train_python(cfg)}" "{run / "train_matthew.py"}"'}
    (run / "plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "train_matthew.py").write_text(SCRIPT, encoding="utf-8")
    (run / "Modelfile").write_text(MODELFILE, encoding="utf-8")
    return run


def free_gpu(runner=subprocess.run) -> list[str]:
    """Décharge les modèles qu'Ollama garde en mémoire vidéo : l'entraînement prend
    ~10 Go des 12 de la RTX 3060, un modèle chargé de plus et c'est le dépassement."""
    try:
        cp = runner(["ollama", "ps"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = [l.split()[0] for l in (cp.stdout or "").splitlines()[1:] if l.strip()]
    for n in names:
        try:
            runner(["ollama", "stop", n], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return names


_NUM = r"'?(-?\d+(?:\.\d+)?(?:e-?\d+)?)'?"


def parse_progress(line: str) -> dict | None:
    """Une ligne de journal du Trainer ({'loss': '0.12', …, 'epoch': '1.5'}) → dict, sinon None."""
    import re
    if "'epoch'" not in line or ("'loss'" not in line and "'eval_loss'" not in line):
        return None
    out = {}
    for key in ("loss", "eval_loss", "epoch"):
        m = re.search(rf"'{key}': {_NUM}", line)
        if m:
            out[key] = float(m.group(1))
    return out or None


def execute(run: Path, timeout_h: float = 6.0, cfg: dict | None = None, on_progress=None,
            should_stop=None) -> dict:
    """`should_stop()` : interroge toutes les 15 s ; une raison (texte) arrête l'entraînement net
    (entraînement de jour : l'utilisateur est revenu). Pas de reprise exacte : le cycle suivant
    repart de zéro — ~20 min de GPU perdues au plus, rien de faux gardé."""
    cfg = cfg or config()
    missing = missing_deps(cfg)
    if missing:
        return {"ok": False, "reason": f"à installer : {', '.join(missing)} "
                f"(voir le nœud « Installer l'entraînement local » du graphe matthew)"}
    free_gpu()
    # Lu ligne par ligne : chaque perte relevée part à `on_progress` (vitrine en direct).
    plan_h = json.loads((run / "plan.json").read_text(encoding="utf-8")).get("hyper", {})
    epochs = int(plan_h.get("epochs", 3))
    proc = subprocess.Popen([train_python(cfg), str(run / "train_matthew.py")], cwd=run,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=train_env(cfg))
    lines, deadline, losses = [], time.monotonic() + timeout_h * 3600, []
    stopped: list[str] = []
    if should_stop:
        import threading

        def watch():
            while proc.poll() is None:
                try:
                    why = should_stop()
                except Exception:
                    why = None
                if why:
                    stopped.append(str(why))
                    proc.kill()
                    return
                time.sleep(15)
        threading.Thread(target=watch, daemon=True).start()
    for line in proc.stdout:
        lines.append(line)
        if len(lines) > 20_000:
            del lines[:5_000]
        p = parse_progress(line)
        if p and on_progress:
            if "loss" in p:
                losses.append(round(p["loss"], 4))
            try:
                on_progress({"phase": "entraînement", "epoch": p.get("epoch"), "epochs": epochs,
                             "losses": losses[-60:], "eval_loss": p.get("eval_loss")})
            except Exception:
                pass
        if time.monotonic() > deadline:
            proc.kill()
            return {"ok": False, "reason": f"délai de {timeout_h} h dépassé"}
    proc.wait()
    out = "".join(lines)
    (run / "train.log").write_text(out[-400_000:], encoding="utf-8")
    if stopped:
        return {"ok": False, "interrupted": True, "reason": f"interrompu : {stopped[0]}"}
    if proc.returncode != 0 or "MATTHEW_TRAIN_OK" not in out:
        return {"ok": False, "reason": f"échec (code {proc.returncode}), voir {run / 'train.log'}"}
    if on_progress:
        try:
            on_progress({"phase": "export", "epoch": epochs, "epochs": epochs, "losses": losses[-60:]})
        except Exception:
            pass
    gguf = finish(run)
    if gguf is None:
        return {"ok": False, "reason": f"entraînement fini mais aucun GGUF quantifié dans {run}"}
    return {"ok": True, "gguf": gguf}


def quantized_gguf(run: Path) -> Path | None:
    """Le GGUF à servir. Unsloth (2026.10) l'écrit dans gguf_gguf/, à côté de
    gguf/ qui contient le modèle fusionné en 16 bits ; on écarte BF16 et mmproj."""
    found = [p for d in ("gguf_gguf", "gguf") for p in sorted((Path(run) / d).glob("*.gguf"))
             if "mmproj" not in p.name.lower() and "bf16" not in p.name.lower()]
    return found[0] if found else None


def finish(run: Path) -> str | None:
    """Pointe le Modelfile sur le GGUF quantifié et libère ~8,7 Go : le modèle
    fusionné en 16 bits (gguf/*.safetensors), reconstructible depuis lora/."""
    import re
    import shutil
    run = Path(run)
    q = quantized_gguf(run)
    if q is None:
        return None
    rel = q.relative_to(run).as_posix()
    mf = run / "Modelfile"
    mf.write_text(re.sub(r"(?m)^FROM \S+", f"FROM ./{rel}", mf.read_text(encoding="utf-8")), encoding="utf-8")
    merged = run / "gguf"
    if q.parent != merged and merged.is_dir() and any(merged.glob("*.safetensors")):
        shutil.rmtree(merged, ignore_errors=True)
    for bf16 in run.glob("gguf*/*BF16.gguf"):
        bf16.unlink(missing_ok=True)
    return rel


def ollama_create(run: Path, name: str, runner=subprocess.run) -> dict:
    try:
        cp = runner(["ollama", "create", name, "-f", "Modelfile"], cwd=run,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": cp.returncode == 0, "reason": (cp.stderr or cp.stdout)[-300:]}
