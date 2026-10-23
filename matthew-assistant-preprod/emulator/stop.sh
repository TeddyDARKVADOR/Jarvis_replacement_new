#!/usr/bin/env bash
#
# emulator/stop.sh — éteint l'AVD du laboratoire, et lui seul.
#
# `adb emu kill` passe par le wrapper, donc la cible est validée avant que quoi
# que ce soit s'éteigne : impossible d'éteindre autre chose que
# l'émulateur nommé dans lab.env.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
ADB="$HERE/../tools/adb.sh"
ADB_BIN="$LAB_SDK/platform-tools/adb.exe"

if ! "$ADB_BIN" devices | grep -q "^${LAB_SERIAL}[[:space:]]"; then
    echo "  $LAB_SERIAL n'est pas la — rien a arreter."
    exit 0
fi

echo "  Arret de $LAB_SERIAL..."
"$ADB" emu kill >/dev/null 2>&1 || true

for _ in $(seq 1 30); do
    "$ADB_BIN" devices | grep -q "^${LAB_SERIAL}[[:space:]]" || { echo "  Arrete."; exit 0; }
    sleep 1
done

echo "  Toujours present apres 30 s. Le processus emulator.exe survit parfois" >&2
echo "  a son propre kill ; il faut alors le terminer a la main." >&2
exit 1
