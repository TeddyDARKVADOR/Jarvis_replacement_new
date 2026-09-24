#!/usr/bin/env bash
#
# tests/avatar/test_face.sh — le visage 3D sur Android, couture par couture.
#
# Ce que la suite Node (avatar/checks/host_test.mjs) et les tests JVM
# (AvatarModelStoreTest) prouvent chacun de leur cote est ici verifie au bout
# du vrai fil : un vrai WebView, une vraie page servie depuis l'APK, un vrai
# modele telecharge depuis le serveur de labo. On lit ce que la page dit
# d'elle-meme (logcat : JarvisFace, AvatarModels) — jamais une capture d'ecran
# interpretee.
#
#   modele absent        le serveur n'a rien : le cercle reste, aucune page
#   SHA-256 incorrect    un octet change : refuse, rien d'installe
#   reprise apres echec  serveur repare : apres le delai, telechargement
#   telechargement       une fois ; au redemarrage, aucun retelechargement
#   chargement de page   onReady, modele reel, pas procedural
#   evenement avatar     joue ; perime (60 s) ignore
#   PcmLevel             un son sur /ws/phone-out atteint la bouche
#   cycle de vie         HOME : rendu suspendu ; retour : repris
#   reconnexion          l'historique rejoue n'est jamais rejoue au visage
#
# Les routes /lab/* et LAB_AVATAR_* n'existent qu'en mode `lab`. En mode
# `full` (vrai run_headless), les checks qui en dependent sont des SKIP.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

SERVER="$LAB_ROOT/tools/server.sh"
CONNECT="$LAB_ROOT/tools/connect_app.sh"
LINK="$LAB_ROOT/tools/link_state.sh"
BASE="http://${LAB_SERVER_HOST_FROM_PC:-127.0.0.1}:${LAB_SERVER_PORT:-8000}"

logs()      { MSYS_NO_PATHCONV=1 "$ADB" logcat -d -s JarvisFace:I AvatarModels:I 2>/dev/null || true; }
clear_log() { "$ADB" logcat -c >/dev/null 2>&1 || true; }
has_log()   { logs | grep -qF "$1"; }
wait_log()  { wait_for "$1" "${2:-30}" has_log "$1"; }
foreground(){ "$ADB" shell am start -n "$LAB_PACKAGE/$LAB_ACTIVITY" >/dev/null 2>&1 || true; }
background(){ "$ADB" shell input keyevent KEYCODE_HOME >/dev/null 2>&1 || true; }
forget_model() {
    MSYS_NO_PATHCONV=1 "$ADB" shell "run-as $LAB_PACKAGE rm -rf files/avatar" >/dev/null 2>&1 || true
}
restart_app() { "$ADB" shell am force-stop "$LAB_PACKAGE" >/dev/null 2>&1 || true; foreground; }
restart_server() {        # restart_server [VAR=valeur ...] — la derniere valeur l'emporte
    "$SERVER" stop >/dev/null 2>&1 || true
    env LAB_AVATAR_DIR="$FACE_DIR" "$@" "$SERVER" start >/dev/null
    wait_for "serveur" 30 sh -c "\"$SERVER\" health | grep -q '\"ok\":true'"
}
post() {                  # post /chemin '{json}'
    curl -s -X POST "$BASE$1" -H "Authorization: Bearer $("$SERVER" bearer)" \
         -H "Content-Type: application/json" -d "$2"
}
last_stats() { logs | grep -F "stats :" | tail -1 | sed 's/.*stats : //' || true; }
stat_of()    { last_stats | python -c "import json,sys; print(json.loads(sys.stdin.read() or '{}').get('$1', ''))"; }

lab_init "avatar"

if ! "$SERVER" health 2>/dev/null | grep -q '"ok":true'; then
    ko "serveur en ecoute" "aucune reponse sur /health — lance ./preprod up"
    finish; exit 1
fi
MODE="$("$SERVER" mode)"
ok "serveur en ecoute" "mode $MODE"

# Licence first. In `full` mode the server would serve the model active on
# this machine, whatever its licence: nothing here runs there.
if [ "$MODE" != "lab" ]; then
    skipped "suite visage" "mode full : servirait le modele actif, licence non verifiee"
    finish; exit 0
fi
# A model the lab may put on a device (default jarvis/male, MIT) — never the
# one active on this machine. See tools/avatar_lab_dir.py.
FACE_DIR="$(python "$LAB_ROOT/tools/avatar_lab_dir.py" "${LAB_FACE_SLOT:-jarvis/male}")"
restart_server                               # BEFORE the app opens and syncs
ok "modele de labo" "$(python -c "import json,sys; print(json.load(open(sys.argv[1]+'/manifest.json'))['model']['file'])" "$FACE_DIR")"
"$CONNECT" >/dev/null 2>&1 || true

# ── modele absent ────────────────────────────────────────────────────────────
{   # modele absent, SHA-256 incorrect, reprise : le serveur est relance a chaque fois
    EMPTY="$(mktemp -d)"
    printf '{"model": {"file": ""}}' > "$EMPTY/manifest.json"
    restart_server LAB_AVATAR_DIR="$EMPTY"
    forget_model; clear_log; restart_app
    if wait_log "Unavailable" 30 && ! has_log "page prete"; then
        ok "modele absent -> cercle 2D" "$(logs | grep -F 'sync :' | tail -1 | cut -c1-120)"
    else
        ko "modele absent -> cercle 2D" "$(logs | tail -3 | tr '\n' ' ')"
    fi

    # ── SHA-256 incorrect ────────────────────────────────────────────────────
    restart_server LAB_AVATAR_FAULT=sha
    forget_model; clear_log; restart_app
    # 46 Mo telecharges puis refuses : ~75 s sur un AVD au GPU hote, bien plus
    # en swiftshader. Ce premier « sync : » porte la raison ; les suivants ne
    # disent plus que « nouvel essai dans N s ».
    if wait_log "SHA-256 incorrecte" 300 && ! has_log "page prete"; then
        installed="$(MSYS_NO_PATHCONV=1 "$ADB" shell "run-as $LAB_PACKAGE ls files/avatar/models 2>/dev/null" | tr -d '\r' || true)"   # absent = rien d'installe
        assert_eq "SHA-256 incorrect -> refuse, rien d'installe" "" "$installed"
    else
        ko "SHA-256 incorrect -> refuse, rien d'installe" "$(logs | grep -F 'sync :' | tail -1)"
    fi

    # ── reprise apres echec ──────────────────────────────────────────────────
    restart_server
    sleep 31                                   # BACKOFF_MS[0] = 30 s
    clear_log; background; foreground          # onResume -> AvatarModels.refresh
    if wait_log "downloaded=true" 240; then
        ok "reprise apres echec" "telechargement verifie apres le delai"
    else
        ko "reprise apres echec" "$(logs | grep -F 'sync :' | tail -1)"
    fi
}

# ── telechargement : une fois ────────────────────────────────────────────────
clear_log; restart_app
if wait_log "sync :" 60 && logs | grep -F "sync :" | tail -1 | grep -qF "downloaded=false"; then
    ok "pas de retelechargement" "copie locale verifiee, reutilisee"
else
    ko "pas de retelechargement" "$(logs | grep -F 'sync :' | tail -1)"
fi

# ── chargement de la page ────────────────────────────────────────────────────
if wait_log "page prete" 60; then
    ok "page chargee (onReady)" "$(logs | grep -F 'page prete' | tail -1 | cut -c1-140)"
    # Pret ne suffit pas : un WebView en WRAP_CONTENT donne un viewport de 0 px
    # de haut, le modele se dessine dans rien et le cercle est masque quand meme.
    view="$(logs | grep -F 'page prete' | tail -1 | sed 's/.*page prete : //' \
            | python -c "import json,sys; v=json.loads(sys.stdin.read()).get('view') or [0,0]; print(f'{v[0]}x{v[1]}')" 2>/dev/null || echo '?')"
    if [[ "$view" =~ ^[1-9][0-9]*x[1-9][0-9]*$ ]]; then ok "visage visible (canvas non vide)" "$view px"
    else ko "visage visible (canvas non vide)" "canvas $view"; fi
else
    ko "page chargee (onReady)" "$(logs | grep -E 'echec|perdu' | tail -2 | tr '\n' ' ')"
    finish; exit 1
fi

# ── evenement avatar ─────────────────────────────────────────────────────────
if [ "$MODE" = "lab" ]; then
    # Les force-stop plus haut ont aussi arrete le service : sans lui, pas de
    # /ws, l'evenement part vers personne et le visage reste « OFFLINE ».
    # connect_app relance l'app et le service ; la page se recharge.
    clear_log
    "$CONNECT" >/dev/null 2>&1 || true
    # connect_app finit sur l'onglet SETTINGS ; le visage vit sur l'accueil.
    "$LAB_ROOT/tools/ui.sh" tap text "JARVIS" >/dev/null 2>&1 || true
    wait_log "page prete" 90 || true
    clear_log
    post /lab/avatar '{"directive": {"intent": "warn"}}' >/dev/null
    if wait_log '"outcome":"played"' 15; then ok "evenement avatar joue" "$(logs | grep -F 'directive :' | tail -1 | cut -c1-120)"
    else ko "evenement avatar joue" "$(logs | tail -3 | tr '\n' ' ')"; fi

    clear_log
    post /lab/avatar '{"directive": {"intent": "amuse"}, "age_s": 60}' >/dev/null
    if wait_log '"outcome":"stale"' 15; then ok "evenement perime ignore" "60 s > 25 s"
    else ko "evenement perime ignore" "$(logs | grep -F 'directive :' | tail -1)"; fi

    # ── PcmLevel ─────────────────────────────────────────────────────────────
    clear_log
    post /lab/phone-out-tone '{}' >/dev/null
    sleep 7                                    # un rapport toutes les 5 s
    peak="$(stat_of maxSpeaker)"
    if python -c "import sys; sys.exit(0 if float('${peak:-0}' or 0) > 0.05 else 1)"; then
        ok "PcmLevel atteint la bouche" "niveau max recu par la page : $peak"
    else
        ko "PcmLevel atteint la bouche" "maxSpeaker=${peak:-?} (le telephone a-t-il /ws/phone-out ?)"
    fi
else
    skipped "evenement avatar / PcmLevel" "exige /lab/avatar et /lab/phone-out-tone (mode lab)"
fi

# ── cycle de vie ─────────────────────────────────────────────────────────────
clear_log; background
if wait_log '"visible":false' 10; then
    sleep 12
    n="$(logs | grep -F 'stats :' | grep -cF '"visible":true' || true)"
    assert_eq "arriere-plan : rendu suspendu" "0" "$n"
else
    ko "arriere-plan : rendu suspendu" "aucun rapport visible:false"
fi
clear_log; foreground
if wait_log '"visible":true' 10; then ok "premier plan : rendu repris" ""
else ko "premier plan : rendu repris" "$(logs | tail -2 | tr '\n' ' ')"; fi

# ── reconnexion sans rejeu ───────────────────────────────────────────────────
airplane() { "$ADB" shell cmd connectivity airplane-mode "$1" >/dev/null 2>&1 || true; }
if [ "$MODE" = "lab" ] && [ -x "$LINK" ]; then
    played_before="$(stat_of played)"
    clear_log
    # Same cut as tests/network/test_reconnect.sh. On reconnect, /ws replays
    # its last 50 events — including the two avatar events sent above.
    airplane enable
    sleep 8
    airplane disable
    "$LINK" --wait 60 >/dev/null 2>&1 || true
    sleep 12                                   # rejeu + deux rapports
    played_after="$(stat_of played)"
    if [ "${played_before:-0}" -lt 1 ] 2>/dev/null || [ -z "$played_before" ]; then
        ko "reconnexion : aucun ancien visage rejoue" "aucun evenement joue avant la coupure : rien a prouver"
    elif [ -n "$played_after" ] && [ "$played_after" = "$played_before" ]; then
        ok "reconnexion : aucun ancien visage rejoue" "joues $played_before -> $played_after ; $(last_stats | cut -c1-120)"
    else
        ko "reconnexion : aucun ancien visage rejoue" "joues $played_before -> ${played_after:-?}"
    fi
else
    skipped "reconnexion sans rejeu" "exige tools/link_state.sh et le mode lab"
fi

physical_only "rendu GPU d'un vrai telephone" "l'AVD rend en logiciel ; fluidite et chauffe a mesurer sur l'appareil"
physical_only "lip-sync a l'oreille"          "exige une vraie voix et un vrai haut-parleur"

finish
