#!/usr/bin/env bash
#
# tools/collect_logcat.sh — le journal du device, filtré sur ce qui nous concerne.
#
#     collect_logcat.sh <nom-du-test> [--all]
#
# Écrit reports/latest/<nom>/logcat.txt, et logcat.all.txt avec --all.
#
# POURQUOI DEUX FICHIERS
#     Le filtré est celui qu'on lit : les tags de l'application plus les lignes
#     du système qui expliquent pourquoi Android a refusé quelque chose
#     (NotificationService, ActivityManager). Le complet est celui qu'on garde
#     pour le jour où la cause n'est dans aucun des deux — un OOM killer, un
#     SELinux denial, un crash natif de TensorFlow Lite.
#
#     Filtrer seulement, ce serait perdre la cause ; garder seulement le brut,
#     ce serait 40 000 lignes à trier à chaque échec.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
ADB="$HERE/adb.sh"

NAME="${1:-run}"
OUT="$HERE/../reports/latest/$NAME"
mkdir -p "$OUT"

# Les tags de l'application, tels qu'ils sont écrits dans le code Kotlin.
TAGS=(JarvisService JarvisNotify JarvisClient JarvisLog Jarvis
      WakeWord AudioPlayer AudioRecorder DeviceState)

PATTERN="$(IFS='|'; echo "${TAGS[*]}")"
# Les lignes systeme qui expliquent un refus : sans elles, « la notification ne
# s'affiche pas » reste sans cause.
PATTERN="$PATTERN|NotificationService|ActivityManager.*$LAB_PACKAGE|$LAB_PACKAGE"

"$ADB" logcat -d -v time 2>/dev/null | grep -E "$PATTERN" > "$OUT/logcat.txt" || true

if [ "${2:-}" = "--all" ]; then
    "$ADB" logcat -d -v time > "$OUT/logcat.all.txt" 2>/dev/null || true
fi

lines="$(wc -l < "$OUT/logcat.txt" 2>/dev/null | tr -d ' ')"
echo "  logcat : ${lines:-0} lignes -> $OUT/logcat.txt"
