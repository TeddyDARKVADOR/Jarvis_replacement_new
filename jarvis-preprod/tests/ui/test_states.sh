#!/usr/bin/env bash
#
# tests/ui/test_states.sh — l'interface reste celle de JARVIS.
#
# La vérification la plus utile ici n'est pas qu'un bouton existe : c'est que
# l'écran d'accueil n'a pas redérivé vers un tableau de bord technique. C'est
# arrivé une fois dans l'histoire de ce client, la phase 9 l'a corrigé, et rien
# ne l'empêche de revenir sans un test qui le surveille.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

UI="$LAB_ROOT/tools/ui.sh"
LINK="$LAB_ROOT/tools/link_state.sh"

lab_init "ui"

# Appairer ici, pas compter sur la suite d'avant : lancee seule (ou apres
# `smoke`, qui reinstalle l'app), cette suite trouvait le service arrete et
# echouait sur l'etat du lien. connect_app relance l'activite ; il finit sur
# SETTINGS, la navigation ci-dessous repart de JARVIS.
"$LAB_ROOT/tools/connect_app.sh" >/dev/null 2>&1 || true
"$ADB" shell am start -n "$LAB_PACKAGE/$LAB_ACTIVITY" >/dev/null 2>&1 || true
sleep 3
# connect_app passe par SETTINGS -> CONNECT, et rate parfois (dialogue System UI,
# bouton deja dans l'autre etat). Le bouton de l'accueil est l'action de l'app
# elle-meme : s'en servir si le lien n'est pas monte.
if ! "$LINK" --is-up >/dev/null 2>&1; then
    "$UI" tap text "JARVIS" >/dev/null 2>&1 || true
    "$UI" tap text "WAKE JARVIS" >/dev/null 2>&1 || true
    "$LINK" --wait 60 >/dev/null 2>&1 || true
fi

# ── navigation ───────────────────────────────────────────────────────────────

for tab in JARVIS HISTORY SETTINGS; do
    if "$UI" tap text "$tab" >/dev/null 2>&1; then
        sleep 2
        if "$UI" exists text "$tab" >/dev/null 2>&1; then
            ok "onglet $tab accessible" "affiche"
            screenshot "tab_$tab"
        else
            ko "onglet $tab accessible" "l'onglet disparait apres le tap"
        fi
    else
        ko "onglet $tab accessible" "introuvable dans l'arbre UI"
    fi
done

# ── l'accueil parle le langage JARVIS ────────────────────────────────────────

"$UI" tap text "JARVIS" >/dev/null 2>&1 || true
sleep 2
home="$("$UI" texts 2>/dev/null || true)"
screenshot "home"

# Des libellés de transport sur l'écran principal signifieraient que le mode
# technique a repris le dessus. Recherche mot entier : "RX" ne doit pas
# déclencher sur un mot qui le contient.
leaked=""
for term in "TX" "RX" "WebSocket" "bytesSent" "framesDropped" "socket"; do
    if printf '%s\n' "$home" | grep -qiwF "$term"; then leaked="$leaked $term"; fi
done
if [ -z "$leaked" ]; then
    ok "accueil sans jargon de transport" "ni TX, ni RX, ni WebSocket"
else
    ko "accueil sans jargon de transport" "presents :$leaked"
fi

# ── le mode développeur, lui, DOIT être technique ────────────────────────────

"$UI" tap text "SETTINGS" >/dev/null 2>&1 || true
sleep 2
if "$UI" tap text "Developer  ›" >/dev/null 2>&1 || "$UI" tap text "Developer" >/dev/null 2>&1; then
    sleep 3
    dev="$("$UI" texts 2>/dev/null || true)"
    screenshot "developer"
    assert_contains "le mode developpeur montre les compteurs" "Notifications" "$dev"
    # Les quatre compteurs de la phase 10.
    for label in "Shown" "Duplicate" "Dropped"; do
        assert_contains "compteur '$label' present" "$label" "$dev"
    done
else
    ko "mode developpeur accessible" "lien Developer introuvable"
fi

# ── états du lien ────────────────────────────────────────────────────────────

state="$("$LINK" 2>/dev/null || echo inconnu)"
case "$state" in
    Connected|Reconnecting*|Disconnected|Connecting*)
        ok "etat du lien lisible et connu" "$state" ;;
    *)
        ko "etat du lien lisible et connu" "valeur inattendue: '$state'" ;;
esac

# LISTENING / THINKING / SPEAKING viennent de la session Gemini. Sans clé, le
# serveur du laboratoire n'ouvre aucune session : ces états ne peuvent pas être
# atteints, et prétendre les tester serait faux.
if [ "$("$LAB_ROOT/tools/server.sh" mode)" = "full" ]; then
    skipped "etats LISTENING/THINKING/SPEAKING" "a implementer avec le serveur complet"
else
    # Le labo n'a pas de session Gemini, mais /lab/state envoie le MEME message
    # que server/headless_ui.py : ce que l'ecran fait d'un etat est testable,
    # ce que Gemini fait pour y arriver ne l'est pas (et n'est pas teste ici).
    # La section d'avant laisse l'app sur Developer, une sous-page sans barre
    # d'onglets (qui affiche AUSSI le mot d'etat) : revenir a l'accueil, et le
    # verifier, sinon on lirait l'etat sur le mauvais ecran.
    "$ADB" shell input keyevent KEYCODE_BACK >/dev/null 2>&1 || true
    "$ADB" shell input keyevent KEYCODE_BACK >/dev/null 2>&1 || true
    "$ADB" shell am start -n "$LAB_PACKAGE/$LAB_ACTIVITY" >/dev/null 2>&1 || true
    "$UI" tap text "JARVIS" >/dev/null 2>&1 || true
    if ! wait_for "accueil" 10 sh -c "\"$UI\" texts | grep -qE '^(MIC|MUTE|WAKE JARVIS)$'"; then
        ko "accueil atteint avant les etats" "$("$UI" texts 2>/dev/null | tr '\n' '|' | cut -c1-120)"
    fi
    bearer="$("$LAB_ROOT/tools/server.sh" bearer)"
    for pair in "LISTENING:I'm listening." "THINKING:Thinking" "SPEAKING:"; do
        st="${pair%%:*}"; caption="${pair#*:}"
        curl -s -X POST "http://${LAB_SERVER_HOST_FROM_PC}:${LAB_SERVER_PORT}/lab/state" \
             -H "Authorization: Bearer $bearer" -H "Content-Type: application/json" \
             -d "{\"state\":\"$st\"}" >/dev/null
        # La pastille change tout de suite, la legende arrive en fondu (420 ms) :
        # attendre les deux ensemble, pas lire l'ecran au milieu de la transition.
        shows() { t="$("$UI" texts 2>/dev/null)"; printf '%s\n' "$t" | grep -qx "$1" \
                  && { [ -z "$2" ] || printf '%s' "$t" | grep -qF "$2"; }; }
        if wait_for "$st" 10 shows "$st" "$caption"; then
            ok "etat $st affiche" "pastille${caption:+ + « $caption »}"
            screenshot "state_$st"
        else
            ko "etat $st affiche" "attendu $st${caption:+ + « $caption »} : $("$UI" texts 2>/dev/null | tr '\n' '|' | cut -c1-120)"
        fi
    done
fi

"$LAB_ROOT/tools/collect_logcat.sh" ui >/dev/null 2>&1 || true
finish
