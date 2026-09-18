#!/usr/bin/env bash
#
# tools/adb.sh — le seul adb que le laboratoire a le droit d'appeler.
#
# POURQUOI CE FICHIER EXISTE
#     Un vrai téléphone est branché sur cette machine une partie du temps. Les
#     commandes que ce laboratoire exécute sont `uninstall`, `force-stop`,
#     `clear`, `svc wifi disable`, `input keyevent` — sur l'appareil de
#     quelqu'un, ce sont des dégâts, pas un test. `adb` sans -s choisit tout
#     seul quand il n'y a qu'un appareil, et ce choix est exactement le piège :
#     il marche pendant des semaines, puis un jour l'émulateur est éteint et la
#     même commande part sur le Redmi.
#
#     Ce wrapper rend ce cas impossible. Il ne choisit jamais. Il vérifie.
#
# CE QU'IL REFUSE
#     1. Une cible qui ne ressemble pas à `emulator-<port>`. Un numéro de série
#        de téléphone physique ne peut pas passer, même explicitement.
#     2. Une cible absente de la liste des appareils.
#     3. Plusieurs appareils connectés — parce que dans ce cas l'opérateur ne
#        sait plus forcément lequel est lequel, et le doute doit s'arrêter ici.
#
#     La règle 3 se contourne avec LAB_ALLOW_OTHER_DEVICES=1, et même alors les
#     règles 1 et 2 tiennent : la cible reste un émulateur nommé. C'est une
#     concession à l'ergonomie, jamais à la sécurité.
#
# USAGE
#     tools/adb.sh shell getprop sys.boot_completed
#     tools/adb.sh install -r app-debug.apk
#     tools/adb.sh --serial          # affiche la cible validée, sans rien faire
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../config/lab.env
source "$HERE/../config/lab.env"

ADB_BIN="${LAB_SDK}/platform-tools/adb.exe"
[ -x "$ADB_BIN" ] || ADB_BIN="${LAB_SDK}/platform-tools/adb"

die() { printf '  REFUS  %s\n' "$*" >&2; exit 2; }

[ -x "$ADB_BIN" ] || die "adb introuvable sous ${LAB_SDK}/platform-tools/"

# ── règle 1 : la cible doit être un émulateur ────────────────────────────────
#
# Vérifiée avant toute interrogation d'adb : si LAB_SERIAL a été mis à un
# numéro de série physique, on s'arrête sans même démarrer le serveur adb.
if [[ ! "$LAB_SERIAL" =~ ^emulator-[0-9]{4,5}$ ]]; then
    die "LAB_SERIAL='$LAB_SERIAL' n'est pas un serial d'emulateur." \
        "Le laboratoire ne pilote que des emulator-<port>."
fi

devices="$("$ADB_BIN" devices | tail -n +2 | grep -E '\S' || true)"
online="$(printf '%s\n' "$devices" | awk '$2=="device" {print $1}' || true)"
count="$(printf '%s\n' "$online" | grep -c . || true)"

# ── règle 3 : un seul appareil ───────────────────────────────────────────────
if [ "${count:-0}" -gt 1 ] && [ "${LAB_ALLOW_OTHER_DEVICES:-0}" != "1" ]; then
    physical="$(printf '%s\n' "$online" | grep -v '^emulator-' || true)"
    {
        echo "REFUS  ${count} appareils connectes — le laboratoire s'arrete."
        printf '%s\n' "$devices" | sed 's/^/         /'
        if [ -n "$physical" ]; then
            echo "         Dont un appareil PHYSIQUE : $(printf '%s' "$physical" | tr '\n' ' ')"
            echo "         Debranche-le. Les commandes de test sont destructrices."
        fi
        echo "         (LAB_ALLOW_OTHER_DEVICES=1 leve cette regle ; la cible"
        echo "          reste $LAB_SERIAL et ne peut pas devenir un telephone.)"
    } >&2
    exit 2
fi

# ── règle 2 : la cible existe et répond ──────────────────────────────────────
if ! printf '%s\n' "$online" | grep -qx "$LAB_SERIAL"; then
    {
        echo "REFUS  $LAB_SERIAL absent ou pas encore 'device'."
        if [ -n "$devices" ]; then
            printf '%s\n' "$devices" | sed 's/^/         /'
        else
            echo "         (aucun appareil connecte)"
        fi
        echo "         Demarre-le : jarvis-preprod/emulator/start.sh"
    } >&2
    exit 2
fi

if [ "${1:-}" = "--serial" ]; then
    printf '%s\n' "$LAB_SERIAL"
    exit 0
fi

exec "$ADB_BIN" -s "$LAB_SERIAL" "$@"
