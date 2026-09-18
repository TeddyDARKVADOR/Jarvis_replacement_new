#!/usr/bin/env bash
#
# tests/smoke/test_launch.sh — l'application s'installe, démarre, et tient debout.
#
# C'est le test qui doit passer avant qu'aucun autre ait du sens. S'il échoue,
# tous les suivants échoueront aussi et pour la même raison : inutile de lire
# leurs rapports.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../tools/lib.sh"

APK="${LAB_APK:-$LAB_ROOT/../client-android/app/build/outputs/apk/debug/app-debug.apk}"
UI="$LAB_ROOT/tools/ui.sh"

lab_init "smoke"

# ── 1. l'émulateur répond ────────────────────────────────────────────────────

if serial="$("$ADB" --serial 2>/dev/null)"; then
    ok "cible ADB validee" "$serial"
else
    ko "cible ADB validee" "tools/adb.sh refuse la cible — voir son message"
    finish; exit 1
fi

boot="$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')"
assert_eq "Android a fini de demarrer" "1" "$boot"

sdk="$("$ADB" shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r\n')"
if [ "${sdk:-0}" -ge 26 ]; then
    ok "API du device compatible minSdk 26" "API $sdk"
else
    ko "API du device compatible minSdk 26" "API $sdk < 26"
fi

# ── 2. installation ──────────────────────────────────────────────────────────

if [ ! -f "$APK" ]; then
    ko "APK presente" "$APK introuvable — lance d'abord: ./preprod build"
    finish; exit 1
fi
ok "APK presente" "$(du -m "$APK" | cut -f1) Mo"

# Désinstaller d'abord : une APK signée d'une autre clé échoue en upgrade avec
# INSTALL_FAILED_UPDATE_INCOMPATIBLE, ce qui n'a rien à voir avec le code testé.
"$ADB" uninstall "$LAB_PACKAGE" >/dev/null 2>&1 || true

install_out="$("$ADB" install -r -g "$APK" 2>&1 || true)"
if printf '%s' "$install_out" | grep -q "Success"; then
    # -g accorde les permissions declarees d'office : sans lui, POST_NOTIFICATIONS
    # reste refusee et chaque notification serait comptee en 'dropped' sans que
    # rien n'explique pourquoi.
    ok "installation" "Success (permissions accordees par -g)"
else
    ko "installation" "$(printf '%s' "$install_out" | tail -2 | tr '\n' ' ')"
    finish; exit 1
fi

pkg="$("$ADB" shell pm list packages "$LAB_PACKAGE" 2>/dev/null | tr -d '\r\n')"
assert_contains "package present" "$LAB_PACKAGE" "$pkg"

# ── 3. lancement ─────────────────────────────────────────────────────────────

"$ADB" shell am start -n "$LAB_PACKAGE/$LAB_ACTIVITY" >/dev/null 2>&1 || true

if wait_for "activite au premier plan" "$LAB_UI_TIMEOUT" \
        bash -c "'$ADB' shell dumpsys activity activities 2>/dev/null | grep -q '$LAB_PACKAGE'"; then
    ok "Activity lancee" "$LAB_ACTIVITY"
else
    ko "Activity lancee" "jamais apparue dans dumpsys activity"
fi

# Le processus doit être vivant, pas seulement lancé : une application qui
# plante au démarrage apparaît brièvement dans dumpsys puis disparaît.
sleep 3
if "$ADB" shell pidof "$LAB_PACKAGE" >/dev/null 2>&1; then
    ok "processus vivant 3 s apres le lancement" "pid $("$ADB" shell pidof "$LAB_PACKAGE" | tr -d '\r\n')"
else
    ko "processus vivant 3 s apres le lancement" "le processus a disparu — crash au demarrage"
fi

# ── 4. l'UI répond ───────────────────────────────────────────────────────────

if texts="$("$UI" texts 2>/dev/null)" && [ -n "$texts" ]; then
    ok "arbre UI lisible" "$(printf '%s' "$texts" | grep -c . ) libelles visibles"
else
    ko "arbre UI lisible" "uiautomator n'a rien renvoye"
fi

# ── 5. aucun crash dans le journal ───────────────────────────────────────────

crashes="$("$ADB" logcat -d -v brief 2>/dev/null \
    | grep -E "FATAL EXCEPTION|AndroidRuntime.*$LAB_PACKAGE" || true)"
assert_absent "aucune FATAL EXCEPTION" "FATAL EXCEPTION" "$crashes"

# ── 6. preuves ───────────────────────────────────────────────────────────────

screenshot "screen"
"$LAB_ROOT/tools/collect_logcat.sh" smoke >/dev/null 2>&1 || true
ok "preuves collectees" "screen.png + logcat.txt"

finish
