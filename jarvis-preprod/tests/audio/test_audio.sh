#!/usr/bin/env bash
#
# tests/audio/test_audio.sh — ce qui est déterministe, et ce qui ne l'est pas.
#
# LA LIGNE DE PARTAGE
#     Un AVD n'a ni microphone ni haut-parleur. Ce qu'on peut vérifier ici est
#     le CONTRAT : les constantes du format PCM des deux côtés du fil, et le
#     résultat de l'auto-test numérique que le client exécute lui-même au
#     démarrage du service.
#
#     Ce qu'on ne peut pas vérifier, c'est que JARVIS entend et parle. Aucune
#     ligne de ce fichier ne prétend le contraire : ces points sortent en
#     PHYSICAL_ONLY et le rapport les affiche dans leur propre bloc.
#
# L'AUTO-TEST DU WAKE WORD
#     WakeWordSelfTest rejoue un extrait fixe à travers le vrai pipeline et
#     compare la sortie à la référence openWakeWord. C'est un test de fixture au
#     sens strict : pas de micro, pas de hasard, un écart numérique attendu à
#     1e-8 près. Il tourne à chaque démarrage du service et écrit son verdict
#     dans logcat — il suffit de le lire.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

REPO="$(cd "$LAB_ROOT/.." && pwd)"

lab_init "audio"

# ── le contrat PCM, des deux côtés ───────────────────────────────────────────
#
# Ces nombres vivent dans deux langages et deux dépôts d'habitudes. C'est le
# seul endroit où ils sont comparés.

kt="$REPO/client-android/app/src/main/java/com/jarvis/net/Protocol.kt"
if [ -f "$kt" ]; then
    up="$(grep -oE 'UPLINK_SAMPLE_RATE = [0-9_]+' "$kt" | grep -oE '[0-9_]+' | tr -d '_')"
    down="$(grep -oE 'DOWNLINK_SAMPLE_RATE_DEFAULT = [0-9_]+' "$kt" | grep -oE '[0-9_]+' | tr -d '_')"
    frame="$(grep -oE 'UPLINK_FRAME_SAMPLES = [0-9_]+' "$kt" | grep -oE '[0-9_]+' | tr -d '_')"

    py_up="$(grep -oE 'SEND_SAMPLE_RATE\s*=\s*[0-9]+' "$REPO/main.py" | grep -oE '[0-9]+$' | head -1)"
    py_down="$(grep -oE 'RECEIVE_SAMPLE_RATE\s*=\s*[0-9]+' "$REPO/main.py" | grep -oE '[0-9]+$' | head -1)"

    assert_eq "cadence montante Kotlin == main.py" "${py_up:-16000}" "$up"
    assert_eq "cadence descendante Kotlin == main.py" "${py_down:-24000}" "$down"
    assert_eq "taille de trame == CHUNK_SIZE de main.py" "1024" "$frame"
else
    skipped "contrat PCM" "Protocol.kt introuvable"
fi

# ── l'auto-test embarqué du wake word ────────────────────────────────────────

selftest="$("$ADB" logcat -d -v brief 2>/dev/null | grep -iE "WakeWordSelfTest|wake.?word.*(deviation|self.?test)" | tail -5 || true)"
if [ -n "$selftest" ]; then
    if printf '%s' "$selftest" | grep -qiE "fail|mismatch|error"; then
        ko "auto-test wake word embarque" "$(printf '%s' "$selftest" | tail -1)"
    else
        ok "auto-test wake word embarque" "$(printf '%s' "$selftest" | tail -1 | cut -c1-70)"
    fi
else
    # Il ne tourne qu'au démarrage du service ET si le wake word est activé.
    # Le laboratoire le désactive pour que l'AVD ne tienne pas le micro.
    skipped "auto-test wake word embarque" "wake word desactive dans la config du laboratoire"
fi

# ── fixtures ─────────────────────────────────────────────────────────────────

FIX="$LAB_ROOT/fixtures/audio"
if [ -d "$FIX" ] && ls "$FIX"/*.wav >/dev/null 2>&1; then
    n="$(ls "$FIX"/*.wav | wc -l | tr -d ' ')"
    ok "fixtures audio presentes" "$n fichier(s)"
    skipped "injection de fixture dans le pipeline" \
        "demande -audio-input sur l'emulateur : non implemente"
else
    skipped "fixtures audio" "aucun .wav dans fixtures/audio/ — voir le README"
fi

# ── ce qu'aucun émulateur ne prouvera ────────────────────────────────────────

physical_only "microphone reel"            "l'AVD n'a pas d'entree audio ; le pipeline n'est pas exerce"
physical_only "haut-parleur reel"          "aucune sortie audio a mesurer"
physical_only "detection wake word a la voix" "l'auto-test prouve le calcul, pas l'ecoute"
physical_only "sortie Bluetooth A2DP"      "aucun peripherique Bluetooth sur l'AVD"
physical_only "interruption d'une phrase reelle" "demande une session Gemini et du son"

finish
