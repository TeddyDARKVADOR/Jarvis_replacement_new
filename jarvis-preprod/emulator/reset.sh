#!/usr/bin/env bash
#
# emulator/reset.sh — remet l'AVD dans un état propre.
#
# Deux niveaux, parce qu'ils ne coûtent pas le même temps :
#
#   (défaut)  désinstalle l'application et efface ses données. Quelques
#             secondes. Suffisant entre deux campagnes : ce que les tests
#             salissent, c'est l'état de JARVIS, pas celui d'Android.
#
#   --wipe    efface toute la partition data et redémarre l'AVD à neuf.
#             Plusieurs minutes. Pour les cas où l'on soupçonne l'état du
#             système lui-même — permissions accordées, canaux de notification
#             retouchés à la main, compte ajouté.
#
# Le wipe passe par emulator -wipe-data et non par une suppression de fichiers :
# effacer userdata-qemu.img à la main pendant que l'émulateur tourne laisse un
# AVD qui démarre mais se comporte mal, et c'est très long à diagnostiquer.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
ADB="$HERE/../tools/adb.sh"
ADB_BIN="$LAB_SDK/platform-tools/adb.exe"

if [ "${1:-}" = "--wipe" ]; then
    echo "  Wipe complet de $LAB_AVD."
    "$HERE/stop.sh" || true
    export ANDROID_HOME="${LAB_SDK//\//\\}"
    export ANDROID_SDK_ROOT="$ANDROID_HOME"
    export ANDROID_AVD_HOME="${LAB_AVD_HOME//\//\\}"
    LOG="$HERE/../reports/emulator.log"
    mkdir -p "$(dirname "$LOG")"
    "$LAB_SDK/emulator/emulator.exe" -avd "$LAB_AVD" -port "$LAB_EMULATOR_PORT" \
        -wipe-data -no-boot-anim -gpu swiftshader_indirect > "$LOG" 2>&1 &
    sleep 5
    exec "$HERE/start.sh"
fi

if ! "$ADB_BIN" devices | grep -q "^${LAB_SERIAL}[[:space:]]*device"; then
    echo "  $LAB_SERIAL n'est pas en ligne — rien a nettoyer."
    exit 0
fi

echo "  Nettoyage de $LAB_PACKAGE sur $LAB_SERIAL"
"$ADB" shell am force-stop "$LAB_PACKAGE" >/dev/null 2>&1 || true
"$ADB" shell pm clear "$LAB_PACKAGE" >/dev/null 2>&1 || true
"$ADB" uninstall "$LAB_PACKAGE" >/dev/null 2>&1 || true
"$ADB" shell cmd notification post-dismiss-all >/dev/null 2>&1 || true
echo "  Etat propre."
