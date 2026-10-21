"""
matthew/cloud.py — entraîner un Matthew plus gros (9B) sur un GPU loué.

Qwen3.5-9B demande ~22 Go en LoRA bf16 : plus que la RTX 3060. On loue une
carte de 24 Go ou plus quelques heures (RunPod, Vast.ai, Lambda…) : il faut le
compte de l'utilisateur et un moyen de paiement, donc ce module ne loue rien.
Il prépare tout le reste.

    python -m matthew.cloud pack [--base Qwen/Qwen3.5-9B]
        → runs/<date>-cloud/matthew-cloud.zip : jeu de données, plan, script,
          requirements, run.sh (installe, entraîne, exporte le GGUF)
    Sur la machine louée :  unzip matthew-cloud.zip && bash run.sh
    Rapatrier gguf/*.gguf dans le dossier du run, puis :
    python -m matthew.cloud import <dossier du run>
        → ollama create matthew-candidate, évaluation contre le champion, promotion si gagné

Le même gate que la boucle de nuit : un 9B n'entre en service que s'il bat le
champion sur le même hold-out.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import config, data_dir
from matthew import dataset as D
from matthew import evaluate as E
from matthew import train as T
from matthew.client import Client

RUN_SH = """#!/usr/bin/env bash
# Matthew — entraînement sur GPU loué (Linux + CUDA). Généré par matthew/cloud.py.
set -e
cd "$(dirname "$0")"
python3 -m pip install -q --upgrade pip
python3 -m pip install -q -r requirements.txt
sed -i "s#{TRAIN_PATH}#$(pwd)/data/train.jsonl#; s#{EVAL_PATH}#$(pwd)/data/eval.jsonl#" plan.json
python3 train_matthew.py
ls -la gguf/
echo "Rapatrier gguf/*.gguf, puis : python -m matthew.cloud import <dossier du run>"
"""


def pack(base: str = "Qwen/Qwen3.5-9B", cfg: dict | None = None, root: Path | None = None,
         clock=time.time) -> Path:
    cfg = dict(cfg or config())
    cfg["base_model"] = base
    root = root or data_dir()
    man = D.build(D.Journal(root), None, cfg=cfg, clock=clock)
    ds = Path(man["dir"])
    run = T.plan(ds, cfg, root=root, clock=clock)
    run = run.rename(run.with_name(run.name + "-cloud"))
    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    plan.update({"train": "{TRAIN_PATH}", "eval": "{EVAL_PATH}", "where": "cloud",
                 "command": "bash run.sh"})
    (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "requirements.txt").write_text("unsloth\ntrl\ndatasets\n", encoding="utf-8")
    (run / "run.sh").write_bytes(RUN_SH.encode("utf-8"))
    z = run / "matthew-cloud.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("plan.json", "train_matthew.py", "requirements.txt", "run.sh", "Modelfile"):
            zf.write(run / name, name)
        zf.write(ds / "train.jsonl", "data/train.jsonl")
        zf.write(ds / "eval.jsonl", "data/eval.jsonl")
    return z


def import_run(run: Path, cfg: dict | None = None, runner=subprocess.run, client_factory=None) -> dict:
    """GGUF rapatrié → candidat Ollama → évalué contre le champion → promu si gagné."""
    cfg = cfg or config()
    run = Path(run)
    if T.finish(run) is None:
        return {"ok": False, "reason": f"aucun GGUF quantifié dans {run}/gguf ni gguf_gguf"}
    cand = f"{cfg['serve_model']}-candidate"
    res = T.ollama_create(run, cand, runner=runner)
    if not res["ok"]:
        return {"ok": False, "reason": f"ollama create : {res['reason']}"}
    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    eval_path = run / "data" / "eval.jsonl"
    rows = E.load(eval_path if eval_path.exists() else Path(plan["manifest"]["dir"]) / "eval.jsonl")
    from matthew.flywheel import SERVE_EXTRA
    make = client_factory or (lambda m: Client(cfg["endpoint"], m, purpose="matthew", extra=SERVE_EXTRA))
    cand_score = E.score(make(cand), rows, cfg["eval_repeats"])
    champ_path = data_dir() / "champion.json"
    champ_score = E.score(make(cfg["serve_model"]), rows, cfg["eval_repeats"]) if champ_path.exists() else None
    ok, why = E.gate(cand_score, champ_score, cfg["promote_margin"], min_eval=cfg.get("min_eval", 30))
    if ok:
        cp = runner(["ollama", "cp", cand, cfg["serve_model"]], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        ok = cp.returncode == 0
        if ok:
            champ_path.write_text(json.dumps({"model": cfg["serve_model"], "since": time.strftime("%Y%m%d-%H%M%S"),
                                              "run": str(run), "score": cand_score,
                                              "base": plan["base_model"]}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return {"ok": ok, "why": why, "candidate": cand_score, "champion": champ_score}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.cloud")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--base", default="Qwen/Qwen3.5-9B")
    i = sub.add_parser("import")
    i.add_argument("run")
    a = ap.parse_args(argv)
    if a.cmd == "pack":
        print(pack(a.base))
    else:
        print(json.dumps(import_run(Path(a.run)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
