#!/usr/bin/env bash
#
# MARK LIII — headless server install.
#
#   sudo bash deployment/install.sh
#
# Idempotent: safe to re-run after a git pull. It never touches config/ once the
# files exist, so a re-run cannot overwrite your API key or un-pair your phone.
#
# It installs NO secrets. The Gemini key is yours to put in place; the device
# credential is generated on first start. See deployment/ORACLE.md.

set -euo pipefail

JARVIS_USER="${JARVIS_USER:-jarvis}"
JARVIS_HOME="${JARVIS_HOME:-/opt/jarvis}"
PYTHON="${PYTHON:-}"

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash deployment/install.sh"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 1. Python ────────────────────────────────────────────────────────────────
# main.py uses asyncio.TaskGroup and BaseExceptionGroup, so 3.11 is the floor.
say "Looking for a suitable Python (3.11+)"
if [ -z "$PYTHON" ]; then
    for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
                PYTHON="$(command -v "$candidate")"
                break
            fi
        fi
    done
fi
[ -n "$PYTHON" ] || die "No Python 3.11+ found. Install one, or set PYTHON=/path/to/python"
echo "  using $PYTHON ($("$PYTHON" --version))"

"$PYTHON" -c 'import venv' 2>/dev/null || die \
    "The venv module is missing. On Debian/Ubuntu: apt install python3-venv"

# ── 2. System user ───────────────────────────────────────────────────────────
say "System user '$JARVIS_USER'"
if id "$JARVIS_USER" >/dev/null 2>&1; then
    echo "  already exists"
else
    # --system: no login, no password, no mail spool. The assistant does not
    # need a shell account, and giving it one only widens what a mistake costs.
    useradd --system --home-dir "$JARVIS_HOME" --shell /usr/sbin/nologin "$JARVIS_USER"
    echo "  created"
fi

# ── 3. Code ──────────────────────────────────────────────────────────────────
say "Installing to $JARVIS_HOME"
if [ "$REPO_DIR" != "$JARVIS_HOME" ]; then
    mkdir -p "$JARVIS_HOME"
    # -a preserves the layout; --exclude keeps build junk and, crucially, the
    # destination's own config/ out of the way.
    # config/ is NOT excluded as a directory. config/__init__.py is *code* —
    # get_os()/is_windows()/is_mac()/is_linux(), which flight_finder, game_updater
    # and youtube_video import at module level. Excluding the whole directory left
    # `config` as an empty namespace package on the server (config.__file__ was
    # None) and those three actions were rejected at discovery with an ImportError
    # that named a symbol, not a missing file. Only the secrets are protected, by
    # name; rsync does not delete excluded files on the receiver.
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
              --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
              --exclude 'config/api_keys.json' \
              --exclude 'config/device_credentials.json' \
              --exclude 'config/certs/' --exclude 'config/whatsapp_web/' \
              --exclude 'memory/long_term.json' \
              --exclude 'client-android/' \
              "$REPO_DIR"/ "$JARVIS_HOME"/
    else
        warn "rsync not found — copying with cp, stale files will not be removed"
        # The header promises a re-run never overwrites config/. cp has no
        # --exclude, so the secrets are set aside and put back around the copy.
        _keep="$(mktemp -d)"
        for _f in config/api_keys.json config/device_credentials.json \
                  memory/long_term.json; do
            if [ -f "$JARVIS_HOME/$_f" ]; then
                mkdir -p "$_keep/$(dirname "$_f")"
                cp -p "$JARVIS_HOME/$_f" "$_keep/$_f"
            fi
        done
        cp -r "$REPO_DIR"/. "$JARVIS_HOME"/
        cp -rp "$_keep"/. "$JARVIS_HOME"/ 2>/dev/null || true
        rm -rf "$_keep"
    fi
    echo "  code copied (secrets in config/ and memory/long_term.json preserved)"
else
    echo "  already running from $JARVIS_HOME"
fi

mkdir -p "$JARVIS_HOME/config" "$JARVIS_HOME/memory" "$JARVIS_HOME/uploads"

# ── 4. Virtualenv ────────────────────────────────────────────────────────────
say "Virtualenv and dependencies"
if [ ! -x "$JARVIS_HOME/.venv/bin/python" ]; then
    "$PYTHON" -m venv "$JARVIS_HOME/.venv"
    echo "  created"
fi
"$JARVIS_HOME/.venv/bin/python" -m pip install --quiet --upgrade pip
"$JARVIS_HOME/.venv/bin/python" -m pip install --quiet \
    -r "$JARVIS_HOME/deployment/requirements-server.txt"
echo "  dependencies installed"

# Fails loudly here rather than in a 3 a.m. restart loop. python-multipart is
# checked explicitly because FastAPI needs it to build the upload route: without
# it the whole app refuses to start, and the error names a file upload nobody
# was attempting.
say "Verifying the imports the server cannot start without"
"$JARVIS_HOME/.venv/bin/python" - <<'PY'
import sys
missing = []
for mod, pkg in (("google.genai", "google-genai"), ("numpy", "numpy"),
                 ("psutil", "psutil"), ("fastapi", "fastapi"),
                 ("uvicorn", "uvicorn[standard]"), ("multipart", "python-multipart"),
                 ("cryptography", "cryptography")):
    try:
        __import__(mod)
    except Exception:
        missing.append(pkg)
if missing:
    print("MISSING: " + ", ".join(missing)); sys.exit(1)
print("  all present")
PY

# ── 5. Permissions ───────────────────────────────────────────────────────────
say "Permissions"
chown -R "$JARVIS_USER":"$JARVIS_USER" "$JARVIS_HOME"
chmod 750 "$JARVIS_HOME"
# config/ holds the Gemini key and the device credential. 700 on the directory
# means even a world-readable file inside it cannot be reached by another user.
chmod 700 "$JARVIS_HOME/config"
find "$JARVIS_HOME/config" -type f -exec chmod 600 {} \; 2>/dev/null || true
chmod 700 "$JARVIS_HOME/memory"
echo "  config/ and memory/ locked to $JARVIS_USER"

# ── 6. systemd ───────────────────────────────────────────────────────────────
say "systemd unit"
# Not every target has systemd: containers, chroots, WSL without systemd
# enabled. Everything above this point is still valid on such a host, so a
# missing systemctl must not throw away a successful install — it is a warning,
# not a failure.
if [ -d /etc/systemd/system ]; then
    install -m 644 "$JARVIS_HOME/deployment/jarvis.service" /etc/systemd/system/jarvis.service
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload
        echo "  installed (not started — see below)"
    else
        warn "unit copied, but systemctl is not available here — reload it yourself"
    fi
else
    warn "no /etc/systemd/system on this host; skipping the unit."
    warn "The assistant still runs with: $JARVIS_HOME/.venv/bin/python -m server.run_headless"
fi

# ── 7. What is still missing ─────────────────────────────────────────────────
KEY_FILE="$JARVIS_HOME/config/api_keys.json"
say "Done. Two things are deliberately NOT automated:"
echo
if [ ! -f "$KEY_FILE" ]; then
    echo "  1. The Gemini API key. Create $KEY_FILE:"
    echo
    echo '       {"gemini_api_key": "AIza..."}'
    echo
    echo "     then:  chown $JARVIS_USER:$JARVIS_USER $KEY_FILE && chmod 600 $KEY_FILE"
else
    echo "  1. config/api_keys.json is already in place."
fi
echo
echo "  2. Check it works before enabling the service:"
echo
# The cd has to happen inside the sudo: $JARVIS_HOME is 750 and owned by
# $JARVIS_USER, so your own shell cannot enter it — and `python -m` needs the
# install root on sys.path, not your home directory.
echo "       sudo -u $JARVIS_USER sh -c 'cd $JARVIS_HOME && exec .venv/bin/python -m server.selftest'"
echo "       sudo -u $JARVIS_USER sh -c 'cd $JARVIS_HOME && exec .venv/bin/python -m server.run_headless'"
echo "       curl http://127.0.0.1:8000/health"
echo
echo "     Then start it for real:"
echo
echo "       sudo systemctl enable --now jarvis"
echo "       journalctl -u jarvis -f"
echo
echo "  Pair the phone with:"
echo "       sudo -u $JARVIS_USER sh -c 'cd $JARVIS_HOME && exec .venv/bin/python -m server.run_headless --pairing'"
echo
