#!/usr/bin/env bash
#
# tests/notifications/test_notifications.sh — la phase 10, vérifiée sur Android.
#
# Tout ce que la suite serveur prouvait en Python est ici revérifié à l'autre
# bout du fil, dans `dumpsys notification` : le canal réellement utilisé,
# l'importance réellement appliquée, le texte réellement retenu. Une assertion
# côté serveur prouve qu'on a demandé quelque chose ; celle-ci prouve qu'Android
# l'a fait.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

SERVER="$LAB_ROOT/tools/server.sh"
CONNECT="$LAB_ROOT/tools/connect_app.sh"
NOTIFDUMP="$LAB_ROOT/tools/notifdump.py"

notifs() {
    MSYS_NO_PATHCONV=1 "$ADB" shell dumpsys notification --noredact 2>/dev/null \
        | python "$NOTIFDUMP" --package "$LAB_PACKAGE" --exclude-service
}
notif_count() { notifs | grep -c . || true; }
clear_notifs() { "$ADB" shell cmd notification post-dismiss-all >/dev/null 2>&1 || true; }

# Attend qu'une notification portant ce titre arrive. Une boucle, pas un sleep :
# le trajet serveur -> WebSocket -> NotificationManager prend un temps variable,
# et un délai fixe serait soit trop court, soit du temps perdu à chaque fois.
wait_notif() {
    local title="$1" timeout="${2:-15}"
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if notifs | grep -qF "|$title|"; then return 0; fi
        sleep 1
    done
    return 1
}

lab_init "notifications"

# ── préconditions ────────────────────────────────────────────────────────────

if ! "$SERVER" health 2>/dev/null | grep -q '"ok":true'; then
    ko "serveur en ecoute" "aucune reponse sur /health — lance ./preprod up"
    finish; exit 1
fi
ok "serveur en ecoute" "mode $("$SERVER" mode)"

LINK="$LAB_ROOT/tools/link_state.sh"

if ! "$LINK" --is-up 2>/dev/null; then
    echo "  (application non connectee — appairage)"
    "$CONNECT" >/dev/null 2>&1 || true
fi

if "$LINK" --is-up 2>/dev/null; then
    ok "application connectee au serveur" "le service publie : Connected"
else
    ko "application connectee au serveur" "etat du lien : $("$LINK" 2>/dev/null || echo inconnu)"
    finish; exit 1
fi

# ── une priorité, un canal, une importance ───────────────────────────────────
#
# 4 HIGH / 3 DEFAULT / 2 LOW. Ce sont les trois comportements que la table de
# context/policy.py demande, traduits par NotificationMapper.

check_priority() {
    local prio="$1" chan="$2" imp="$3" title="$4" text="$5"
    clear_notifs
    "$SERVER" notify "$prio" "$title" "$text" >/dev/null 2>&1 || true
    if ! wait_notif "$title"; then
        ko "$prio affichee sur Android" "aucune notification '$title' apres 15s"
        return
    fi
    local row; row="$(notifs | grep -F "|$title|" | head -1)"
    local got_chan got_imp got_text
    got_chan="$(printf '%s' "$row" | cut -d'|' -f2)"
    got_imp="$(printf '%s' "$row" | cut -d'|' -f3)"
    got_text="$(printf '%s' "$row" | cut -d'|' -f5)"

    if [ "$got_chan" = "$chan" ] && [ "$got_imp" = "$imp" ]; then
        ok "$prio -> canal et importance" "$got_chan, importance $got_imp"
    else
        ko "$prio -> canal et importance" "attendu $chan/$imp, obtenu $got_chan/$got_imp"
    fi
    assert_eq "$prio -> texte integral" "$text" "$got_text"
}

check_priority CRITICAL  jarvis_critical  4 "Fuite d'eau" "Le detecteur du garage a sonne."
check_priority IMPORTANT jarvis_important 3 "Rendez-vous" "Ca commence dans 15 minutes."
check_priority USEFUL    jarvis_general   2 "Colis"       "Livre dans la boite aux lettres."

screenshot "notification"

# ── ANODIN n'arrive jamais ───────────────────────────────────────────────────

clear_notifs
before="$(notif_count)"
http="$("$SERVER" notify TRIVIAL "Pub" "Ne doit jamais arriver." 2>/dev/null || true)"
sleep 4
after="$(notif_count)"
if printf '%s' "$http" | grep -q '"ok":false' && [ "$before" = "$after" ]; then
    ok "TRIVIAL refuse a la source" "serveur 400, rien d'affiche"
else
    ko "TRIVIAL refuse a la source" "reponse=$http, notifications $before -> $after"
fi

# ── texte long : pas de troncature arbitraire ────────────────────────────────

clear_notifs
LONG="Ceci est un texte deliberement long, destine a verifier qu'aucune troncature \
arbitraire n'est appliquee entre le serveur et l'affichage Android. Il depasse \
confortablement deux cents caracteres, ce qui est la limite qu'on aurait pu etre \
tente de poser quelque part sans le dire, et il doit arriver entier."
"$SERVER" notify IMPORTANT "Texte long" "$LONG" >/dev/null 2>&1 || true
if wait_notif "Texte long"; then
    got="$(notifs | grep -F "|Texte long|" | head -1 | cut -d'|' -f5)"
    # 600 est la limite documentee de server/notify.py (_MAX_TEXT). Ce qui est
    # interdit, c'est une coupure a 200 que personne n'a declaree.
    if [ "${#got}" -ge 200 ] && [ "${#got}" -le 600 ]; then
        ok "texte long non tronque a 200" "${#got} caracteres transmis"
    else
        ko "texte long non tronque a 200" "${#got} caracteres — coupure inattendue"
    fi
else
    ko "texte long non tronque a 200" "notification jamais arrivee"
fi

# ── déduplication : le replay ne montre rien deux fois ───────────────────────
#
# Le serveur re-propose ses notifications recentes a chaque connexion. Sans la
# memoire d'ids du client, chaque reconnexion afficherait de nouveau tout ce que
# le TTL couvre encore.

clear_notifs
"$SERVER" notify IMPORTANT "Dedup" "Cette notification ne doit apparaitre qu'une fois." >/dev/null 2>&1 || true
if wait_notif "Dedup"; then
    ok "notification de reference affichee" "1 fois"
    clear_notifs      # l'utilisateur l'ecarte : elle est lue

    # Reconnexion complète : le serveur re-proposera « Dedup » dans son pending.
    "$ADB" shell am force-stop "$LAB_PACKAGE" >/dev/null 2>&1 || true
    sleep 2
    "$ADB" shell am start -n "$LAB_PACKAGE/$LAB_ACTIVITY" >/dev/null 2>&1 || true
    sleep 12

    if notifs | grep -qF "|Dedup|"; then
        ko "aucun doublon apres reconnexion" "'Dedup' reaffichee alors qu'elle avait ete ecartee"
    else
        ok "aucun doublon apres reconnexion" "le replay serveur n'a rien reaffiche"
    fi
else
    ko "notification de reference affichee" "jamais arrivee"
fi

# ── ce qu'un emulateur ne peut pas prouver ───────────────────────────────────

physical_only "comportement HyperOS/MIUI" "gestion agressive des notifications par Xiaomi"
physical_only "son et vibration reels" "aucun haut-parleur ni vibreur sur l'AVD"
physical_only "notification ecran eteint, batterie reelle" "doze et standby bucket different d'un vrai telephone"

"$LAB_ROOT/tools/collect_logcat.sh" notifications >/dev/null 2>&1 || true
finish
