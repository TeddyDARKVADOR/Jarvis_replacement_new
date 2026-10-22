#!/usr/bin/env bash
# deployment/matthew-train-linux.sh — l'environnement d'entraînement de Matthew sous Linux (ou WSL2).
#
# Le pendant Linux de D:\DEV\matthew-train (Windows) : même contenu, dans ~/matthew-train.
#   Python 3.12 (uv) · torch CUDA 12.8 · unsloth · trl · datasets · cache Hugging Face local
# Ce sont les réglages par défaut de matthew/__init__.py hors Windows (train_python, hf_home) :
# rien à configurer ensuite, la boucle de nuit le trouve seule.
#
# WSL2 : le pilote NVIDIA est celui de WINDOWS (ne rien installer côté Linux) ; `nvidia-smi`
# doit répondre dans WSL avant de lancer ce script. Linux natif : pilote NVIDIA du système.
# Unsloth compile llama.cpp pour l'export GGUF : build-essential et cmake (sudo, une fois).
#
#   bash deployment/matthew-train-linux.sh            installe ou met à jour
#   bash deployment/matthew-train-linux.sh --check    vérifie seulement
set -euo pipefail

HOME_TRAIN="${MATTHEW_TRAIN_HOME:-$HOME/matthew-train}"
VENV="$HOME_TRAIN/.venv"
export HF_HOME="$HOME_TRAIN/hf"

check() {
    "$VENV/bin/python" - <<'PY'
import importlib.util as u
missing = [m for m in ("torch", "unsloth", "trl", "datasets") if u.find_spec(m) is None]
import torch
print("modules manquants :", missing or "aucun")
print("CUDA :", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
PY
}

if [ "${1:-}" = "--check" ]; then check; exit $?; fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi introuvable : installez le pilote NVIDIA (WSL2 : celui de Windows suffit)." >&2
    exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

for tool in gcc cmake; do
    command -v "$tool" >/dev/null 2>&1 || echo "Conseillé pour l'export GGUF : sudo apt install build-essential cmake"
done

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

mkdir -p "$HOME_TRAIN" "$HF_HOME"
[ -x "$VENV/bin/python" ] || uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" --index-url https://download.pytorch.org/whl/cu128 torch
uv pip install --python "$VENV/bin/python" unsloth trl datasets
check

command -v ollama >/dev/null 2>&1 || echo "Ollama manque (il sert Matthew) : curl -fsSL https://ollama.com/install.sh | sh"
echo "Prêt : $VENV/bin/python — puis « python -m matthew.flywheel --install » pour la nuit (minuterie systemd)."
