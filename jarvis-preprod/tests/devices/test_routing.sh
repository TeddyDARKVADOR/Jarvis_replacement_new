#!/usr/bin/env bash
#
# tests/devices/test_routing.sh — la première capacité Android, sur l'appareil.
#
# CE QUE CETTE SUITE PROUVE, ET QU'AUCUN TEST PYTHON NE PEUT PROUVER
#     server/routing_selftest.py vérifie que `targeting.py` choisit le bon
#     appareil. Il le fait avec un registre en mémoire et des appareils qui
#     n'existent pas. Ici, au bout d'un vrai WebSocket, il y a un vrai Android
#     qui ouvre — ou n'ouvre pas — une vraie application.
#
# LA PROPRIÉTÉ CENTRALE : UNE CAPACITÉ EST PROUVÉE, JAMAIS AFFIRMÉE
#     `open_app` n'est déclaré au serveur que si SYSTEM_ALERT_WINDOW est
#     accordée, parce que sans elle un lancement depuis le service est avalé par
#     Android sans erreur : startActivity revient normalement et rien ne
#     s'ouvre. Une capacité déclarée dans ces conditions ferait dire à JARVIS
#     « c'est ouvert » alors que l'écran n'a pas bougé.
#
#     Les deux tests symétriques sont le cœur de la suite : permission refusée
#     -> la capacité n'est PAS annoncée ; permission accordée -> elle l'est. Un
#     test qui ne vérifierait que le second cas laisserait passer un client qui
#     déclare tout, tout le temps.
#
# ÉTAT LAISSÉ DERRIÈRE
#     La permission reste accordée à la fin : c'est l'état dans lequel les
#     suites suivantes veulent trouver l'appareil, et le test le dit plutôt que
#     de le laisser deviner.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

SERVER="$LAB_ROOT/tools/server.sh"
CONNECT="$LAB_ROOT/tools/connect_app.sh"
LINK="$LAB_ROOT/tools/link_state.sh"

# Settings est le seul paquet dont la présence est garantie sur n'importe quelle
# image système. Son libellé dépend de la langue de l'AVD ; la résolution par
# nom de paquet d'AppLauncher rattrape le cas, ce qui est précisément ce que
# cette quatrième passe existe pour faire.
TARGET_APP="Settings"
TARGET_PKG="com.android.settings"

# La ligne du telephone du laboratoire. `connect_app.sh` lui fixe une identite
# stable (android-lab) ; on prefere malgre tout la ligne connectee, pour qu'une
# entree laissee par une campagne precedente sur un serveur deja en marche ne
# soit jamais celle qu'on interroge.
phone_row() {
    "$SERVER" devices 2>/dev/null \
        | awk -F'\t' '$2=="android" && $4=="online"{print; found=1; exit}
                      END{ if (!found) exit 1 }' \
        || "$SERVER" devices 2>/dev/null | awk -F'\t' '$2=="android"{print; exit}'
}
phone_id()   { phone_row | cut -f1; }
phone_caps() { phone_row | cut -f5; }

foreground_pkg() {
    MSYS_NO_PATHCONV=1 "$ADB" shell dumpsys activity activities 2>/dev/null \
        | grep -E 'ResumedActivity' | head -1 \
        | grep -oE '[a-zA-Z][a-zA-Z0-9_.]+/[a-zA-Z0-9_.$]+' | head -1 | cut -d/ -f1
}

wait_foreground() {
    local pkg="$1" timeout="${2:-15}"
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        [ "$(foreground_pkg)" = "$pkg" ] && return 0
        sleep 1
    done
    return 1
}

result_of() {
    python -c "
import json, sys
raw = sys.stdin.read()
try:
    print(json.loads(raw).get('result') or json.loads(raw).get('error') or raw)
except Exception:
    print(raw.strip()[:120])
"
}

overlay() { "$ADB" shell appops set "$LAB_PACKAGE" SYSTEM_ALERT_WINDOW "$1" >/dev/null 2>&1 || true; }

# Une capacité n'est envoyée qu'à l'enregistrement, donc changer la permission
# ne suffit pas : il faut que le téléphone se réannonce.
reconnect() { "$CONNECT" >/dev/null 2>&1 || true; }

# ET IL FAUT ATTENDRE QU'IL L'AIT FAIT.
#
# Le premier jet relisait le registre dès que connect_app.sh rendait la main, et
# lisait donc l'enregistrement PRÉCÉDENT — celui d'avant le changement de
# permission. Le test passait ou échouait selon la vitesse de la poignée de
# main, ce qui est la pire espèce de test : celle qui accuse le code un jour
# sur trois.
#
# La ligne doit être `online` en plus de porter la bonne capacité : un appareil
# dont l'entrée a disparu a, lui aussi, « open_app absent ».
wait_caps() {
    local want="$1" cap="$2" timeout="${3:-$LAB_CONNECT_TIMEOUT}"
    local deadline=$(( $(date +%s) + timeout ))
    local row caps
    while [ "$(date +%s)" -lt "$deadline" ]; do
        row="$(phone_row)"
        if [ -n "$row" ] && [ "$(printf '%s' "$row" | cut -f4)" = "online" ]; then
            caps="$(printf '%s' "$row" | cut -f5)"
            if [ "$want" = "present" ]; then
                if printf '%s' "$caps" | grep -q "$cap"; then return 0; fi
            else
                if ! printf '%s' "$caps" | grep -q "$cap"; then return 0; fi
            fi
        fi
        sleep 1
    done
    return 1
}

go_home() { "$ADB" shell input keyevent KEYCODE_HOME >/dev/null 2>&1 || true; sleep 1; }

lab_init "devices"

# ── préconditions ────────────────────────────────────────────────────────────

if ! "$SERVER" health 2>/dev/null | grep -q '"ok":true'; then
    ko "serveur en ecoute" "aucune reponse sur /health — lance ./preprod up"
    finish; exit 1
fi
ok "serveur en ecoute" "mode $("$SERVER" mode)"

if [ "$("$SERVER" mode)" = "full" ]; then
    skipped "routage sur appareil" "/lab/device-command n'existe qu'en mode laboratoire"
    finish; exit 0
fi

if ! "$LINK" --is-up 2>/dev/null; then
    echo "  (application non connectee — appairage)"
    "$CONNECT" >/dev/null 2>&1 || true
    sleep 3
fi

# ── 1. l'appareil se déclare ─────────────────────────────────────────────────

if [ -n "$(phone_id)" ]; then
    ok "le telephone est dans le registre" "$(phone_row | cut -f1,3 | tr '\t' ' ')"
else
    ko "le telephone est dans le registre" "aucun appareil de type android dans /status"
    finish; exit 1
fi

# ── 2. permission refusée : la capacité n'est PAS annoncée ───────────────────

overlay deny
reconnect
if wait_caps absent open_app; then
    ok "sans autorisation, open_app n'est pas declare" "capacites : $(phone_caps)"
else
    ko "sans autorisation, open_app n'est pas declare" "toujours declare apres ${LAB_CONNECT_TIMEOUT}s : $(phone_caps)"
fi

# ── 3. permission accordée : la capacité apparaît ────────────────────────────

overlay allow
reconnect
if wait_caps present open_app; then
    ok "avec autorisation, open_app est declare" "capacites : $(phone_caps)"
else
    ko "avec autorisation, open_app est declare" "absent apres ${LAB_CONNECT_TIMEOUT}s : $(phone_caps)"
fi

DEVICE_ID="$(phone_id)"

# ── 4. la commande routée ouvre vraiment l'application ───────────────────────
#
# JARVIS est renvoyé à l'arrière-plan d'abord : c'est le seul état où le test a
# du sens. Une application au premier plan a le droit d'en lancer une autre sans
# permission particulière, donc un lancement réussi depuis cet état-là ne
# prouverait rien du chemin que la voix emprunte réellement.

go_home
before="$(foreground_pkg)"
raw="$("$SERVER" device-command "$DEVICE_ID" open_app "{\"app_name\": \"$TARGET_APP\"}" 2>&1 || true)"
said="$(printf '%s' "$raw" | result_of)"

if wait_foreground "$TARGET_PKG" "$LAB_UI_TIMEOUT"; then
    ok "open_app ouvre l'application" "$TARGET_PKG au premier plan (avant : ${before:-aucun})"
else
    ko "open_app ouvre l'application" "premier plan : $(foreground_pkg) — reponse : $said"
fi

if printf '%s' "$said" | grep -qi 'est ouvert'; then
    ok "la reponse dit ce qui s'est passe" "$said"
else
    ko "la reponse dit ce qui s'est passe" "$said"
fi

# ── 5. une application inconnue est un refus, pas un lancement ───────────────

go_home
raw="$("$SERVER" device-command "$DEVICE_ID" open_app '{"app_name": "Chrysopompe"}' 2>&1 || true)"
said="$(printf '%s' "$raw" | result_of)"

if printf '%s' "$said" | grep -qi 'aucune application'; then
    ok "une application absente est dite absente" "$said"
else
    ko "une application absente est dite absente" "$said"
fi

if [ "$(foreground_pkg)" = "$TARGET_PKG" ]; then
    ko "un nom inconnu ne lance rien" "$TARGET_PKG est revenu au premier plan"
else
    ok "un nom inconnu ne lance rien" "premier plan inchange : $(foreground_pkg)"
fi

# ── 6. une action non déclarée est refusée par le téléphone ──────────────────
#
# La deuxième barrière : le serveur a déjà choisi la cible, et ceci couvre le
# cas où ce choix était faux. `computer_settings` est une capacité du PC que ce
# téléphone n'a jamais annoncée.

raw="$("$SERVER" device-command "$DEVICE_ID" computer_settings '{}' 2>&1 || true)"
said="$(printf '%s' "$raw" | result_of)"
if printf '%s' "$said" | grep -qiE 'refus|capacit'; then
    ok "une action non declaree est refusee" "$said"
else
    ko "une action non declaree est refusee" "$said"
fi

# ── ce que cette suite ne peut pas observer ──────────────────────────────────

skipped "commande adressee a un autre appareil" \
    "DeviceChannel.request() adresse toujours la commande au canal qu'il ouvre ; forger un mauvais destinataire demanderait un parametre de test dans une classe de production"

echo "  (SYSTEM_ALERT_WINDOW laissee sur 'allow')"

finish
