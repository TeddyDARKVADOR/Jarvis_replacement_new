#!/usr/bin/env bash
#
# tools/ui.sh — interagir avec l'écran sans coder une seule coordonnée.
#
#     ui.sh dump                          arbre UI brut sur stdout
#     ui.sh texts                         tout ce qui est visible, une ligne par libellé
#     ui.sh exists text CONNECT           code retour 0/1
#     ui.sh wait   text CONNECT [30]      attend qu'il apparaisse
#     ui.sh tap    text CONNECT           tape au centre du nœud trouvé
#     ui.sh type   "192.168.1.72"         saisit du texte dans le champ focalisé
#     ui.sh tap-xy 540 1200               DERNIER RECOURS, voir uiquery.py
#
# `dump` est refait à chaque appel plutôt que mis en cache : l'écran bouge entre
# deux commandes, et un arbre périmé fait taper au mauvais endroit — l'exact
# défaut que chercher par identifiant est censé éliminer.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
ADB="$HERE/adb.sh"

dump() {
    # uiautomator écrit sur le device puis on relit : `dump /dev/tty` existe
    # mais mélange sa ligne de statut au XML sur certaines versions.
    #
    # MSYS_NO_PATHCONV=1 est obligatoire et non cosmétique. Sous Git Bash,
    # `/sdcard/ui.xml` est réécrit en `C:/Program Files/Git/sdcard/ui.xml`
    # avant même d'atteindre adb : uiautomator répond alors « dumped to
    # /Files/Git/sdcard/ui.xml », le fichier n'existe nulle part, et le dump
    # revient vide sans qu'aucune commande n'ait signalé d'erreur.
    #
    # Portée volontairement limitée à cette fonction : le réglage empêche aussi
    # la conversion des chemins de l'HÔTE, ce qui casserait `adb install
    # /d/DEV/...` ailleurs dans le laboratoire.
    MSYS_NO_PATHCONV=1 "$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || return 3
    MSYS_NO_PATHCONV=1 "$ADB" shell cat /sdcard/ui.xml 2>/dev/null | tr -d '\r'
}

case "${1:-}" in

dump)  dump ;;

texts) dump | python "$HERE/uiquery.py" texts ;;

exists)
    dump | python "$HERE/uiquery.py" exists --by "${2:-text}" --value "${3:-}"
    ;;

find)
    dump | python "$HERE/uiquery.py" find --by "${2:-text}" --value "${3:-}"
    ;;

wait)
    by="${2:-text}"; value="${3:-}"; timeout="${4:-$LAB_UI_TIMEOUT}"
    deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if dump | python "$HERE/uiquery.py" exists --by "$by" --value "$value"; then
            exit 0
        fi
        sleep 1
    done
    echo "  '$value' ($by) jamais apparu en ${timeout}s" >&2
    exit 1
    ;;

tap)
    by="${2:-text}"; value="${3:-}"
    coords="$(dump | python "$HERE/uiquery.py" center --by "$by" --value "$value")" || {
        echo "  impossible de taper sur '$value' ($by)" >&2; exit 1; }
    # shellcheck disable=SC2086
    "$ADB" shell input tap $coords
    ;;

type)
    # input text n'accepte ni espace ni la plupart des caractères spéciaux tels
    # quels ; %s est la convention d'échappement d'adb pour l'espace.
    escaped="$(printf '%s' "${2:-}" | sed 's/ /%s/g')"
    "$ADB" shell input text "$escaped"
    ;;

tap-xy)
    "$ADB" shell input tap "${2:-0}" "${3:-0}"
    ;;

key)
    "$ADB" shell input keyevent "${2:-}"
    ;;

*)
    echo "usage: ui.sh dump|texts|exists|find|wait|tap|type|tap-xy|key" >&2
    exit 1
    ;;
esac
