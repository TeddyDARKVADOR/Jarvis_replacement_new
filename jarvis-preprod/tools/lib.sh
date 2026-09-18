#!/usr/bin/env bash
#
# tools/lib.sh — ce que tous les tests partagent.
#
# Un test écrit avec ces fonctions produit trois choses sans y penser : un
# affichage lisible, une ligne JSON par vérification, et — en cas d'échec — une
# capture d'écran, un logcat et l'état ADB. Le troisième point est le plus
# important : un FAIL sans preuve oblige à rejouer le test à la main, et un test
# qui échoue une fois sur dix ne se rejoue pas à la demande.
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../config/lab.env
source "$LAB_ROOT/config/lab.env"

ADB="$LAB_ROOT/tools/adb.sh"
REPORTS="$LAB_ROOT/reports/latest"

_TEST=""
_DIR=""
_PASS=0
_FAIL=0
_SKIP=0

# ── cycle de vie ─────────────────────────────────────────────────────────────

lab_init() {
    _TEST="$1"
    _DIR="$REPORTS/$_TEST"
    mkdir -p "$_DIR"
    _PASS=0; _FAIL=0; _SKIP=0
    printf '\n  %s\n  %s\n' "$_TEST" "$(printf '─%.0s' $(seq 1 60))"
}

# Une ligne JSON par vérification, jamais un JSON global réécrit : un test tué
# en cours de route laisse quand même tout ce qu'il avait déjà prouvé.
_record() {
    local status="$1" name="$2" detail="${3:-}"
    mkdir -p "$REPORTS"
    python -c "
import json, sys, time
print(json.dumps({
    'suite': sys.argv[1], 'check': sys.argv[2],
    'status': sys.argv[3], 'detail': sys.argv[4],
    'ts': time.time(),
}))" "$_TEST" "$name" "$status" "$detail" >> "$REPORTS/results.jsonl"
}

ok()      { _PASS=$((_PASS+1)); printf '  [PASS] %-46s %s\n' "$1" "${2:-}"; _record PASS "$1" "${2:-}"; }
ko()      { _FAIL=$((_FAIL+1)); printf '  [FAIL] %-46s %s\n' "$1" "${2:-}"; _record FAIL "$1" "${2:-}"; capture_failure "$1"; }
skipped() { _SKIP=$((_SKIP+1)); printf '  [SKIP] %-46s %s\n' "$1" "${2:-}"; _record SKIP "$1" "${2:-}"; }

# PHYSICAL_ONLY est une catégorie à part et pas un SKIP ordinaire : ce sont les
# choses qu'un émulateur ne peut pas prouver, jamais celles qu'on a oublié
# d'écrire. Le rapport les affiche séparément pour qu'aucune campagne verte ne
# puisse se lire comme « tout est validé ».
physical_only() { printf '  [PHYS] %-46s %s\n' "$1" "${2:-}"; _record PHYSICAL_ONLY "$1" "${2:-}"; }

finish() {
    printf '  %s\n' "$(printf '─%.0s' $(seq 1 60))"
    printf '  %d PASS, %d FAIL, %d SKIP\n' "$_PASS" "$_FAIL" "$_SKIP"
    [ "$_FAIL" -eq 0 ]
}

# ── assertions ───────────────────────────────────────────────────────────────

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then ok "$name" "$actual"
    else ko "$name" "attendu '$expected', obtenu '$actual'"; fi
}

assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    if printf '%s' "$haystack" | grep -qF -- "$needle"; then ok "$name" "'$needle' present"
    else ko "$name" "'$needle' absent"; fi
}

assert_absent() {
    local name="$1" needle="$2" haystack="$3"
    if printf '%s' "$haystack" | grep -qF -- "$needle"; then ko "$name" "'$needle' present alors qu'il ne devrait pas"
    else ok "$name" "'$needle' absent, comme attendu"; fi
}

# ── attente ──────────────────────────────────────────────────────────────────
#
# Toute attente passe par ici. Un `sleep 5` posé au jugé est soit trop court le
# jour où la machine est chargée, soit du temps perdu à chaque exécution.
wait_for() {
    local label="$1" timeout="$2"; shift 2
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if "$@" >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    printf '  (delai depasse: %s apres %ss)\n' "$label" "$timeout" >&2
    return 1
}

# ── preuves ──────────────────────────────────────────────────────────────────

screenshot() {
    local name="${1:-screen}"
    mkdir -p "$_DIR"
    "$ADB" exec-out screencap -p > "$_DIR/$name.png" 2>/dev/null || true
    [ -s "$_DIR/$name.png" ] || rm -f "$_DIR/$name.png"
}

# Appelé automatiquement par ko(). Un échec sans capture ni logcat est un échec
# qu'il faut reproduire pour comprendre, c'est-à-dire un échec deux fois payé.
capture_failure() {
    local name="$(printf '%s' "${1:-failure}" | tr -c 'A-Za-z0-9._-' '_')"
    mkdir -p "$_DIR"
    screenshot "FAIL_$name"
    "$ADB" logcat -d -v time > "$_DIR/FAIL_$name.logcat.txt" 2>/dev/null || true
    {
        echo "# etat ADB au moment de l'echec"
        "$ADB" shell dumpsys activity activities 2>/dev/null | head -40 || true
    } > "$_DIR/FAIL_$name.adbstate.txt" 2>/dev/null || true
}
