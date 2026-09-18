#!/usr/bin/env bash
#
# tools/connect_app.sh — appaire l'application au serveur du laboratoire.
#
# POURQUOI LE TOKEN NE PASSE PAS PAR LE CLAVIER
#     La première version remplissait les trois champs avec `input text`. Les
#     deux premiers marchaient ; le troisième, 43 caractères d'un token
#     url-safe, arrivait abîmé — et l'application ne disait pas « saisie
#     incorrecte », elle disait « le serveur ne connaît pas ce device token ».
#     Le symptôme désignait le serveur alors que la faute était dans le clavier.
#
#     Le token est donc écrit directement dans les SharedPreferences via
#     `run-as`, possible parce que l'APK debug est debuggable. Il transite par
#     STDIN : il n'apparaît ni dans argv, ni dans la liste des processus, ni
#     dans aucun journal.
#
# CE QUI RESTE TESTÉ PAR L'INTERFACE
#     Le bouton CONNECT. Pré-remplir les préférences rend l'appairage
#     déterministe ; taper CONNECT continue de vérifier que le bouton démarre
#     réellement le service. On ne remplace pas le test, on retire de la boucle
#     la seule partie qui n'était pas fiable.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"
REPO="$(cd "$HERE/../.." && pwd)"
ADB="$HERE/adb.sh"
UI="$HERE/ui.sh"

HOST="${1:-$LAB_SERVER_HOST_FROM_EMULATOR}"
PORT="${2:-$LAB_SERVER_PORT}"
PREFS="/data/data/$LAB_PACKAGE/shared_prefs/jarvis_client.xml"

echo "  Appairage sur $HOST:$PORT"

# L'application doit être arrêtée : SharedPreferences garde une copie en
# mémoire et la réécrit en sortant, ce qui effacerait ce qu'on vient d'écrire.
"$ADB" shell am force-stop "$LAB_PACKAGE" >/dev/null 2>&1 || true
sleep 1

# Le token est lu et envoyé sans jamais toucher argv ni stdout.
(
    cd "$REPO"
    python - "$HOST" "$PORT" <<'PY'
import sys
from xml.sax.saxutils import escape
from server import auth

host, port = sys.argv[1], sys.argv[2]
token = auth.load_or_create()["device_token"]
print(
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    f'    <string name="host">{escape(host)}</string>\n'
    f'    <int name="port" value="{int(port)}" />\n'
    f'    <boolean name="tls" value="false" />\n'
    f'    <boolean name="wake_word_enabled" value="false" />\n'
    f'    <string name="device_token">{escape(token)}</string>\n'
    "</map>"
)
PY
) | MSYS_NO_PATHCONV=1 "$ADB" shell "run-as $LAB_PACKAGE sh -c 'mkdir -p /data/data/$LAB_PACKAGE/shared_prefs && cat > $PREFS'"

# Vérifier que l'écriture a pris, sans révéler le contenu.
written="$(MSYS_NO_PATHCONV=1 "$ADB" shell "run-as $LAB_PACKAGE sh -c 'wc -c < $PREFS'" 2>/dev/null | tr -d ' \r\n' || echo 0)"
if [ "${written:-0}" -lt 100 ]; then
    echo "  Echec d'ecriture des preferences (${written} octets)." >&2
    echo "  run-as exige une APK debuggable : verifie que c'est bien l'APK debug." >&2
    exit 1
fi
echo "  Preferences ecrites (${written} octets, token non affiche)."

# ── relance et connexion par l'interface ─────────────────────────────────────

"$ADB" shell am start -n "$LAB_PACKAGE/$LAB_ACTIVITY" >/dev/null 2>&1 || true
sleep 3

if "$UI" exists text "SETTINGS" >/dev/null 2>&1; then
    "$UI" tap text "SETTINGS" >/dev/null 2>&1 || true
    sleep 2
fi

if "$UI" exists text "CONNECT" >/dev/null 2>&1; then
    "$UI" tap text "CONNECT" >/dev/null 2>&1 || true
elif "$UI" exists text "SET UP" >/dev/null 2>&1; then
    "$UI" tap text "SET UP" >/dev/null 2>&1 || true
fi

echo "  Attente de la connexion (${LAB_CONNECT_TIMEOUT}s max)..."
deadline=$(( $(date +%s) + LAB_CONNECT_TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    # Le service publie son propre état dans sa notification permanente ; voir
    # tools/link_state.sh pour les deux sources qui ont été essayées avant et
    # qui donnaient de faux négatifs.
    if "$HERE/link_state.sh" --is-up 2>/dev/null; then
        echo "  Connecte."
        exit 0
    fi
    sleep 2
done

echo "  Pas de connexion apres ${LAB_CONNECT_TIMEOUT}s." >&2
echo "  Derniere erreur cote application :" >&2
"$UI" texts 2>/dev/null | grep -iA1 "last error" >&2 || true
exit 1
