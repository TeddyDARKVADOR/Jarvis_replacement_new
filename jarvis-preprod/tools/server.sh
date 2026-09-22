#!/usr/bin/env bash
#
# tools/server.sh — le MARK LIII du laboratoire.
#
#     tools/server.sh start | stop | health | mode | notify <PRIO> <TITRE> <TEXTE>
#
# DEUX MODES, ET LE RAPPORT DIT TOUJOURS LEQUEL
#
#   full  config/api_keys.json présent  →  python -m server.run_headless
#         Le vrai assistant. Tout est testable, y compris ce qui passe par
#         Gemini. Chaque exécution consomme du quota.
#
#   lab   pas de clé                    →  tools/lab_server.py
#         Le même dashboard, les mêmes routes, le même hub de notifications,
#         sans session Live. Gratuit, déterministe, et incapable de tester ce
#         qui dépend du modèle.
#
# Un test qui a besoin du mode `full` doit interroger `tools/server.sh mode` et
# se déclarer SKIP en mode `lab`. Échouer serait faux : la fonctionnalité n'est
# pas cassée, elle n'est pas observable ici.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
REPO="$(cd "$HERE/../.." && pwd)"
RUN="$HERE/../reports"
PIDFILE="$RUN/server.pid"
LOGFILE="$RUN/server.log"
BASE="http://${LAB_SERVER_HOST_FROM_PC}:${LAB_SERVER_PORT}"

mkdir -p "$RUN"

server_mode() {
    [ -f "$REPO/config/api_keys.json" ] && echo "full" || echo "lab"
}

server_health() {
    curl -s --max-time 4 "$BASE/health" 2>/dev/null
}

case "${1:-}" in

start)
    if server_health | grep -q '"ok":true'; then
        echo "  Serveur deja en ecoute sur $BASE  (mode $(server_mode))"
        exit 0
    fi
    mode="$(server_mode)"
    echo "  Demarrage du serveur, mode '$mode'..."
    cd "$REPO"
    if [ "$mode" = "full" ]; then
        LAB_SERVER_PORT="$LAB_SERVER_PORT" \
            python -m server.run_headless > "$LOGFILE" 2>&1 &
    else
        LAB_SERVER_PORT="$LAB_SERVER_PORT" \
            python "$HERE/lab_server.py" > "$LOGFILE" 2>&1 &
    fi
    echo $! > "$PIDFILE"

    for _ in $(seq 1 40); do
        if server_health | grep -q '"ok":true'; then
            echo "  En ecoute : $BASE  (mode $mode)"
            echo "  Depuis l'emulateur : ${LAB_SERVER_HOST_FROM_EMULATOR}:${LAB_SERVER_PORT}"
            exit 0
        fi
        sleep 1
    done
    echo "  Le serveur n'a pas repondu en 40 s. Journal :" >&2
    tail -20 "$LOGFILE" >&2 || true
    exit 1
    ;;

stop)
    if [ -f "$PIDFILE" ]; then
        pid="$(cat "$PIDFILE")"
        kill "$pid" 2>/dev/null || true
        rm -f "$PIDFILE"
        echo "  Serveur arrete (pid $pid)."
    else
        echo "  Aucun pid enregistre — rien a arreter."
    fi
    ;;

health)
    out="$(server_health)"
    if [ -n "$out" ]; then echo "$out"; else echo "  Injoignable : $BASE" >&2; exit 1; fi
    ;;

mode)
    server_mode
    ;;

bearer)
    # Echange le device token contre un bearer. Le token n'est jamais affiche,
    # ni ici ni dans les journaux : seul le bearer ephemere ressort.
    cd "$REPO"
    python - "$BASE" <<'PY'
import json, sys, urllib.request
from server import auth
base = sys.argv[1]
req = urllib.request.Request(
    base + "/api/device-login",
    data=json.dumps({"device_token": auth.load_or_create()["device_token"]}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=8) as r:
    print(json.loads(r.read())["token"])
PY
    ;;

notify)
    shift
    prio="${1:-USEFUL}"; title="${2:-JARVIS}"; text="${3:-}"
    bearer="$("$HERE/server.sh" bearer)"
    curl -s -X POST "$BASE/api/notify" \
        -H "Authorization: Bearer $bearer" \
        -H "Content-Type: application/json" \
        -d "$(python -c "
import json,sys
print(json.dumps({'priority': sys.argv[1], 'title': sys.argv[2], 'text': sys.argv[3]}))
" "$prio" "$title" "$text")"
    echo
    ;;

status)
    bearer="$("$HERE/server.sh" bearer)"
    curl -s --max-time 5 "$BASE/status" -H "Authorization: Bearer $bearer"
    echo
    ;;

devices)
    # Le registre tel que /status l'expose : identite et capacites, jamais un
    # token. Une ligne par appareil, tabulee, pour que les tests lisent des
    # colonnes plutot que du JSON.
    bearer="$("$HERE/server.sh" bearer)"
    curl -s --max-time 5 "$BASE/status" -H "Authorization: Bearer $bearer"         | python -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for d in (data.get('devices') or []):
    print('	'.join([
        d.get('device_id', ''),
        d.get('device_type', ''),
        d.get('display_name', ''),
        'online' if d.get('connected') else 'offline',
        ','.join(d.get('capabilities') or []) or '-',
    ]))
"
    ;;

device-command)
    # La commande que Gemini aurait routee. N'existe qu'en mode laboratoire ;
    # voir la docstring de tools/lab_server.py pour ce que cette route est et
    # n'est pas.
    shift
    dev="${1:?device_id requis}"; act="${2:?action requise}"; params="${3:-{\}}"
    if [ "$(server_mode)" = "full" ]; then
        echo "  /lab/device-command n'existe pas en mode full." >&2
        exit 2
    fi
    bearer="$("$HERE/server.sh" bearer)"
    curl -s --max-time 70 -X POST "$BASE/lab/device-command"         -H "Authorization: Bearer $bearer"         -H "Content-Type: application/json"         -d "$(python -c "
import json, sys
print(json.dumps({'device_id': sys.argv[1], 'action': sys.argv[2],
                  'parameters': json.loads(sys.argv[3])}))
" "$dev" "$act" "$params")"
    echo
    ;;

*)
    echo "usage: server.sh start|stop|health|mode|bearer|status|devices" >&2
    echo "       server.sh notify <PRIO> <TITRE> <TEXTE>" >&2
    echo "       server.sh device-command <DEVICE_ID> <ACTION> [JSON]" >&2
    exit 1
    ;;
esac
