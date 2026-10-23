#!/usr/bin/env bash
# =============================================================================
#  Matthew PORTABLE (Linux) -- se lance depuis la cle, n'ecrit rien sur l'hote.
#  Equivalent de Lancer-JARVIS.bat : meme principe de redirection des dossiers
#  "maison" (HOME, XDG_*, caches) vers la cle, meme Python portable (astral
#  python-build-standalone), meme mode --local.
#
#  Pose ce fichier a cote de app/, data/, runtime/. Rends-le executable :
#      chmod +x lancer-jarvis.sh && ./lancer-jarvis.sh
#  1er lancement : internet requis (Python portable + dependances).
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/app"
DATA="$ROOT/data"
RUNTIME="$ROOT/runtime"
PYDIR="$RUNTIME/python-linux"
PY="$PYDIR/bin/python3"

echo "[Matthew portable] Cle         : $ROOT"
echo "[Matthew portable] Application : $APP"
[ -f "$APP/main.py" ] || { echo "ERREUR : $APP/main.py introuvable. Lance d'abord sync_to_key.ps1."; exit 1; }

# --- Dossiers de donnees sur la cle -----------------------------------------
mkdir -p "$DATA/home" "$DATA/home/.config" "$DATA/home/.cache" "$DATA/home/.local/share" \
         "$DATA/tmp" "$DATA/hf" "$DATA/logs" "$DATA/ollama-models"

# --- Redirection : tout ce que le code ecrit "chez l'utilisateur" va sur la cle
export MATTHEW_ASSISTANT_PORTABLE=1
export MATTHEW_ASSISTANT_DATA_DIR="$DATA"
export HOME="$DATA/home"
export XDG_CONFIG_HOME="$DATA/home/.config"
export XDG_CACHE_HOME="$DATA/home/.cache"
export XDG_DATA_HOME="$DATA/home/.local/share"
export TMPDIR="$DATA/tmp"
export HF_HOME="$DATA/hf"
export HUGGINGFACE_HUB_CACHE="$DATA/hf/hub"
export OLLAMA_MODELS="$DATA/ollama-models"
export PLAYWRIGHT_BROWSERS_PATH="$DATA/ms-playwright"
export PIP_CACHE_DIR="$DATA/tmp/pip-cache"
# Responsivite sur cle USB : pas de .pyc ecrits sur la cle, UTF-8 partout
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1

# --- 1. Python portable Linux ? sinon bootstrap -----------------------------
if [ ! -x "$PY" ]; then
  echo "[Matthew portable] Python portable absent -- telechargement initial..."
  TAG="20250106"; VER="3.12.8"
  FILE="cpython-${VER}+${TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"
  URL="https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/${FILE}"
  ARC="$DATA/tmp/$FILE"
  if [ -f "$RUNTIME/$FILE" ]; then cp -f "$RUNTIME/$FILE" "$ARC"; else
    (command -v curl >/dev/null && curl -fL "$URL" -o "$ARC") || wget -O "$ARC" "$URL"
  fi
  mkdir -p "$DATA/tmp/pyx"; tar -xf "$ARC" -C "$DATA/tmp/pyx"
  rm -rf "$PYDIR"; mv "$DATA/tmp/pyx/python" "$PYDIR"
  rm -rf "$ARC" "$DATA/tmp/pyx"
  [ -x "$PY" ] || { echo "FATAL : Python portable introuvable apres extraction."; exit 1; }
fi
echo "[Matthew portable] Python      : $PY"

# --- 2. Dependances ---------------------------------------------------------
if [ ! -f "$PYDIR/.deps-ok" ]; then
  echo "[Matthew portable] Installation des dependances (internet requis)..."
  "$PY" -m pip install --upgrade pip wheel setuptools
  "$PY" -m pip install -r "$APP/requirements.txt"
  touch "$PYDIR/.deps-ok"
fi

# --- 3. Ollama hors-ligne, si fourni ----------------------------------------
if [ -x "$RUNTIME/ollama/ollama" ]; then
  pgrep -f "$RUNTIME/ollama/ollama serve" >/dev/null 2>&1 || "$RUNTIME/ollama/ollama" serve &
fi

# --- 4. Coffre : deverrouiller si present -----------------------------------
"$PY" "$APP/portable/vault.py" unlock --dir "$APP/config" || { echo "Coffre non deverrouille."; exit 1; }

# --- 5. Lancement sous gardien d'ejection -----------------------------------
echo "[Matthew portable] Demarrage de Matthew..."
"$PY" "$APP/portable/eject_guard.py" --anchor "${BASH_SOURCE[0]}" -- "$PY" "$APP/main.py" --local || true

# --- 6. Fin de session ------------------------------------------------------
"$PY" "$APP/portable/vault.py" relock --dir "$APP/config" || true
echo "[Matthew portable] Matthew s'est arrete."
