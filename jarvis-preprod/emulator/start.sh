#!/usr/bin/env bash
#
# emulator/start.sh — démarre l'AVD et rend la main quand Android est VRAIMENT prêt.
#
# « Prêt » se mesure en trois temps, et sauter le troisième est l'erreur qui
# produit des tests instables :
#
#   1. adb voit `emulator-5554  device`   — le pont existe
#   2. sys.boot_completed = 1             — le système a fini de démarrer
#   3. le package manager répond          — on peut installer
#
# Entre 1 et 3 il s'écoule facilement une minute. Un test qui installe dès 1
# échoue par intermittence avec des erreurs qui n'ont rien à voir avec lui.
#
#     emulator/start.sh              # normal, état propre
#     emulator/start.sh --headless   # sans fenêtre (CI, machine distante)
#     emulator/start.sh --snapshot   # repart du snapshot, plus rapide
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
ADB="$HERE/../tools/adb.sh"
ADB_BIN="$LAB_SDK/platform-tools/adb.exe"

export ANDROID_HOME="${LAB_SDK//\//\\}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_AVD_HOME="${LAB_AVD_HOME//\//\\}"

HEADLESS=0
SNAPSHOT="-no-snapshot-load"
for arg in "$@"; do
    case "$arg" in
        --headless) HEADLESS=1 ;;
        --snapshot) SNAPSHOT="" ;;
    esac
done

if "$ADB_BIN" devices | grep -q "^${LAB_SERIAL}[[:space:]]*device"; then
    echo "  $LAB_SERIAL deja en ligne."
else
    LOG="$HERE/../reports/emulator.log"
    mkdir -p "$(dirname "$LOG")"
    ARGS=(-avd "$LAB_AVD" -port "$LAB_EMULATOR_PORT" -no-boot-anim
          -gpu "$LAB_EMULATOR_GPU")
    [ -n "$LAB_EMULATOR_MEMORY" ] && ARGS+=(-memory "$LAB_EMULATOR_MEMORY")
    [ -n "$SNAPSHOT" ] && ARGS+=("$SNAPSHOT")
    [ "$HEADLESS" = "1" ] && ARGS+=(-no-window)

    echo "  Demarrage de $LAB_AVD sur le port $LAB_EMULATOR_PORT..."
    "$LAB_SDK/emulator/emulator.exe" "${ARGS[@]}" > "$LOG" 2>&1 &
    echo "  (journal : $LOG)"
fi

echo "  1/3  attente du pont adb..."
deadline=$(( $(date +%s) + LAB_BOOT_TIMEOUT ))
until "$ADB_BIN" devices | grep -q "^${LAB_SERIAL}[[:space:]]*device"; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "  echec : adb n'a jamais vu $LAB_SERIAL" >&2; exit 1; }
    sleep 2
done

echo "  2/3  attente de sys.boot_completed..."
until [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')" = "1" ]; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "  echec : boot jamais termine" >&2; exit 1; }
    sleep 2
done

echo "  3/3  attente du package manager..."
until "$ADB" shell pm list packages >/dev/null 2>&1; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "  echec : pm ne repond pas" >&2; exit 1; }
    sleep 2
done

# Déverrouiller : un écran verrouillé fait échouer toute inspection UI, et le
# message d'erreur ne dit jamais « l'appareil est verrouillé ».
"$ADB" shell input keyevent 82 >/dev/null 2>&1 || true
"$ADB" shell wm dismiss-keyguard >/dev/null 2>&1 || true

echo "  Pret : $LAB_SERIAL  ($("$ADB" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r\n'))"
