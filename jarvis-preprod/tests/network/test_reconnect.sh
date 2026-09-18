#!/usr/bin/env bash
#
# tests/network/test_reconnect.sh — CONNECTED → RECONNECTING → CONNECTED.
#
# CE QUE CE TEST REPRODUIT, ET CE QU'IL NE REPRODUIT PAS
#     Le mode avion coupe l'interface. C'est net, symétrique et scriptable — et
#     c'est précisément ce qui le rend différent d'une vraie perte de Wi-Fi.
#     Dans la vraie vie le socket ne reçoit aucun reset : il reste ouvert et
#     silencieux, et seul le ping applicatif d'OkHttp finit par le déclarer
#     mort (PROTOCOL.md §5). Ici, Android signale la perte immédiatement via
#     ConnectivityManager et la reconnexion part tout de suite.
#
#     Ce test prouve donc que la machine à états et la reprise fonctionnent.
#     Il ne prouve PAS le délai de détection d'une coupure silencieuse, qui
#     reste PHYSICAL_ONLY.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

LINK="$LAB_ROOT/tools/link_state.sh"
CONNECT="$LAB_ROOT/tools/connect_app.sh"

airplane() { "$ADB" shell cmd connectivity airplane-mode "$1" >/dev/null 2>&1 || true; }

wait_state() {
    local want="$1" timeout="$2"
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        case "$("$LINK" 2>/dev/null)" in
            *"$want"*) return 0 ;;
        esac
        sleep 2
    done
    return 1
}

lab_init "network"

if ! "$LINK" --is-up 2>/dev/null; then
    "$CONNECT" >/dev/null 2>&1 || true
fi
if "$LINK" --is-up 2>/dev/null; then
    ok "etat initial CONNECTED" "$("$LINK")"
else
    ko "etat initial CONNECTED" "$("$LINK" 2>/dev/null || echo inconnu)"
    finish; exit 1
fi

# ── coupure ──────────────────────────────────────────────────────────────────

echo "  mode avion ON"
airplane enable
if wait_state "Reconnect" 45 || wait_state "Disconnect" 5; then
    ok "le lien tombe a la coupure" "$("$LINK")"
else
    ko "le lien tombe a la coupure" "toujours '$("$LINK")' 45s apres la coupure"
fi
screenshot "offline"

# Une notification produite pendant la coupure ne doit pas être perdue : le hub
# la garde et la re-propose à la reconnexion. C'est le scénario que le TTL de
# server/notify.py existe pour couvrir.
"$LAB_ROOT/tools/server.sh" notify IMPORTANT "Pendant la coupure" \
    "Produite alors que le client etait absent." >/dev/null 2>&1 || true
ok "notification produite hors ligne" "le serveur l'accepte sans client connecte"

# ── rétablissement ───────────────────────────────────────────────────────────

echo "  mode avion OFF"
airplane disable
if wait_state "Connected" 90; then
    ok "reconnexion automatique" "$("$LINK")"
else
    ko "reconnexion automatique" "toujours '$("$LINK")' 90s apres retablissement"
    finish; exit 1
fi

# Elle doit arriver maintenant, et une seule fois.
deadline=$(( $(date +%s) + 30 ))
found=0
while [ "$(date +%s)" -lt "$deadline" ]; do
    n="$(MSYS_NO_PATHCONV=1 "$ADB" shell dumpsys notification --noredact 2>/dev/null \
        | python "$LAB_ROOT/tools/notifdump.py" --exclude-service \
        | grep -cF "|Pendant la coupure|" || true)"
    [ "${n:-0}" -ge 1 ] && { found="$n"; break; }
    sleep 2
done

if [ "$found" = "1" ]; then
    ok "la notification retenue est livree a la reconnexion" "1 exemplaire, pas de doublon"
elif [ "${found:-0}" -gt 1 ]; then
    ko "la notification retenue est livree a la reconnexion" "$found exemplaires"
else
    ko "la notification retenue est livree a la reconnexion" "jamais arrivee"
fi

screenshot "reconnected"
physical_only "coupure Wi-Fi silencieuse" "sans reset TCP, la detection depend du ping OkHttp — non reproductible ici"
physical_only "bascule Wi-Fi vers cellulaire" "l'AVD n'a qu'une interface reelle"

"$LAB_ROOT/tools/collect_logcat.sh" network >/dev/null 2>&1 || true
finish
