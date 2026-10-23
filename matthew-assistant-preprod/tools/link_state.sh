#!/usr/bin/env bash
#
# tools/link_state.sh — l'état du lien, tel que le service le publie lui-même.
#
#     link_state.sh            imprime "Connected", "Reconnecting in 4s", ...
#     link_state.sh --is-up    code retour 0 si connecté
#     link_state.sh --wait 60  attend la connexion, 0 si elle arrive
#
# POURQUOI CETTE SOURCE ET PAS UNE AUTRE
#     La notification permanente du service de premier plan est écrite par
#     JarvisForegroundService.notificationText(), à partir de son propre
#     LinkState. Elle ne peut donc pas être en avance sur la réalité, et elle
#     survit à l'Activity : elle reste lisible quand l'application est en
#     arrière-plan ou l'écran éteint, là où l'arbre UI ne dit plus rien.
#
#     Deux sources ont été essayées avant et sont à éviter :
#       counts.phone_connects — suit le socket AUDIO, pas /ws. Reste à zéro
#                               pour un client connecté qui ne streame pas.
#       grep -A3 sur dumpsys  — dumpsys imprime chaque notification deux fois,
#                               en compact et en verbeux, et l'écart entre le
#                               canal et le texte n'est pas le même dans les
#                               deux. Le grep marchait une fois sur deux.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
ADB="$HERE/adb.sh"

state() {
    MSYS_NO_PATHCONV=1 "$ADB" shell dumpsys notification --noredact 2>/dev/null \
        | python "$HERE/notifdump.py" --package "$LAB_PACKAGE" \
        | awk -F'|' '$1 == 5301 { print $5 }' | head -1
}

case "${1:-}" in
    --is-up)
        [ "$(state)" = "Connected" ]
        ;;
    --wait)
        timeout="${2:-$LAB_CONNECT_TIMEOUT}"
        deadline=$(( $(date +%s) + timeout ))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            [ "$(state)" = "Connected" ] && exit 0
            sleep 2
        done
        echo "  lien non etabli apres ${timeout}s (etat: '$(state)')" >&2
        exit 1
        ;;
    *)
        s="$(state)"
        [ -n "$s" ] && printf '%s\n' "$s" || { echo "(service absent)"; exit 1; }
        ;;
esac
