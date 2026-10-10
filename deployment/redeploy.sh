#!/usr/bin/env bash
#
# deployment/redeploy.sh — met Oracle a jour DEPUIS N'IMPORTE QUEL PC qui a ce
# depot, sans GitHub et sans l'autre machine.
#
#   bash deployment/redeploy.sh --dry-run        # tout verifier, ne rien changer
#   bash deployment/redeploy.sh --backup-only    # sauvegarder Oracle, rien d'autre
#   bash deployment/redeploy.sh                  # deploiement complet (demande confirmation)
#
# Options : --yes (pas de confirmation), --no-restart, --skip-tests.
# Procedure complete, et restauration : deployment/RECOVERY.md.
#
# CE QUE CE SCRIPT FAIT, DANS L'ORDRE
#   1 repo        l'arbre suivi est propre ; on deploie EXACTEMENT HEAD
#   2 verif       selftests locaux (serveur, routage, presence, contexte)
#   3 package     un git bundle de HEAD (quelques Mo) ; le modele actif, seulement
#                 si son SHA-256 correspond a son profil ET si sa licence permet
#                 de le distribuer (la liste de server/avatar_api.py)
#   4 preflight   Oracle joignable, copie git propre, avance rapide possible
#   5 sauvegarde  /opt/jarvis complet (config/, memory/, uploads/ compris) dans
#                 /var/backups/jarvis sur Oracle, root seul — les secrets ne
#                 quittent jamais le serveur
#   6 deploiement git fetch du bundle + merge --ff-only, modele, install.sh
#   7 redemarrage systemctl restart jarvis, attente de /health
#   8 selftest    server.selftest sur Oracle, sous l'utilisateur jarvis
#   9 version     copie git == HEAD local ; /opt/jarvis identique au commit ;
#                 /health ; le modele servi porte le bon SHA-256
#
# CE QU'IL NE FAIT JAMAIS
#   Pousser sur GitHub. Lire, copier ou afficher un secret. Toucher config/ ou
#   memory/ (install.sh les protege, la sauvegarde les garde). Reecrire
#   l'historique git d'Oracle : si la copie d'Oracle a diverge, il s'arrete.
set -euo pipefail

SSH_TARGET="${JARVIS_SSH:-ubuntu@jarvis-vps}"          # Tailscale SSH, pas de cle
REMOTE_REPO="${JARVIS_REMOTE_REPO:-Jarvis_replacement_new}"   # relatif au $HOME distant
BACKUPS="${JARVIS_BACKUPS:-/var/backups/jarvis}"
KEEP="${JARVIS_KEEP_BACKUPS:-10}"

DRY=0; BACKUP_ONLY=0; YES=0; RESTART=1; TESTS=1
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY=1 ;;
        --backup-only) BACKUP_ONLY=1 ;;
        --yes)         YES=1 ;;
        --no-restart)  RESTART=0 ;;
        --skip-tests)  TESTS=0 ;;
        -h|--help)     sed -n '2,33p' "$0"; exit 0 ;;
        *) echo "option inconnue : $arg" >&2; exit 2 ;;
    esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
export PYTHONIOENCODING=utf-8
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }
ok()   { printf '  ok   %s\n' "$*"; }
info() { printf '       %s\n' "$*"; }
die()  { printf '\n\033[31m  ARRET : %s\033[0m\n' "$*" >&2; exit 1; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_TARGET" "$@" 2>&1 | grep -v '^# ' || return "${PIPESTATUS[0]}"; }

# ── 1. repo ──────────────────────────────────────────────────────────────────
step 1 "depot local"
[ -z "$(git status --porcelain --untracked-files=no)" ] \
    || die "des fichiers suivis sont modifies : commite-les ou mets-les de cote (git stash)"
SHA="$(git rev-parse HEAD)"
ok "HEAD $(git log -1 --format='%h %s' | cut -c1-80) ($(b=$(git branch --show-current); echo "${b:-detache}"))"

# ── 2. verification ──────────────────────────────────────────────────────────
step 2 "verification locale"
if [ "$TESTS" = 1 ]; then
    for suite in server.selftest server.routing_selftest presence.selftest context.selftest; do
        if python -m "$suite" > "$WORK/$suite.log" 2>&1; then
            ok "$suite  $(grep -aE 'passed|/[0-9]+' "$WORK/$suite.log" | tail -1 | tr -s ' ' | cut -c1-60)"
        else
            tail -15 "$WORK/$suite.log" >&2
            die "$suite echoue : rien n'est deploye"
        fi
    done
else
    info "--skip-tests : selftests locaux NON executes"
fi

# ── 3. package ───────────────────────────────────────────────────────────────
step 3 "paquet"
git bundle create "$WORK/jarvis.bundle" HEAD >/dev/null 2>&1 || die "git bundle a echoue"
ok "bundle $(du -h "$WORK/jarvis.bundle" | cut -f1) — $(git rev-parse --short HEAD)"
# Le modele actif : jamais dans git. On l'envoie seulement s'il est verifie et
# si sa licence le permet — la meme regle que pour le telephone.
python - "$WORK/model.txt" <<'PY'
import sys
from pathlib import Path
from presence import models
from server.avatar_api import licence_allows_phone
out = Path(sys.argv[1])
manifest = models.read_json(Path("avatar/manifest.json"))
rel = ((manifest.get("model") or {}).get("file") or "").strip()
if not rel:
    out.write_text("NONE\taucun modele actif\n"); sys.exit(0)
path = Path("avatar/models") / rel
if not path.is_file():
    out.write_text(f"NONE\tfichier absent sur ce PC : {path}\n"); sys.exit(0)
profile = models.read_profile(path)
ok, why = models.check_profile(path)
if not ok:
    out.write_text(f"NONE\tprofil invalide : {why}\n"); sys.exit(0)
if not licence_allows_phone(profile.get("license")):
    out.write_text(f"NONE\tlicence « {profile.get('license')} » : pas de distribution\n"); sys.exit(0)
extra = [p for p in path.parent.iterdir() if p.is_file() and p.name.upper().startswith("LICENSE")]
files = [path] + extra
out.write_text("SHIP\t" + profile["sha256"] + "\t" + str(path.parent.as_posix()) + "\t"
               + "\t".join(p.name for p in files) + "\n")
PY
IFS=$'\t' read -r MODEL_MODE MODEL_SHA MODEL_DIR MODEL_FILES_RAW < "$WORK/model.txt" || true
if [ "$MODEL_MODE" = SHIP ]; then
    read -r -a MODEL_FILES <<< "$(printf '%s' "$MODEL_FILES_RAW" | tr '\t' ' ')"
    ok "modele $MODEL_DIR (${MODEL_FILES[*]}) sha256 ${MODEL_SHA:0:16}…"
else
    info "modele NON envoye : $MODEL_SHA"
fi

# ── 4. preflight ─────────────────────────────────────────────────────────────
step 4 "Oracle (lecture seule)"
STATE="$(remote "cd '$REMOTE_REPO' 2>/dev/null || { echo NOREPO; exit 0; }
    echo HEAD=\$(git rev-parse HEAD)
    echo DIRTY=\$(git status --porcelain | wc -l)
    echo ACTIVE=\$(systemctl is-active jarvis || true)
    echo FREE=\$(df -Pm \$HOME | awk 'NR==2{print \$4}')
    sudo -n true 2>/dev/null && echo SUDO=1 || echo SUDO=0")" || die "Oracle injoignable par '$SSH_TARGET' (Tailscale connecte ?)"
case "$STATE" in *NOREPO*) die "pas de copie git '$REMOTE_REPO' dans le \$HOME distant" ;; esac
R_HEAD="$(printf '%s\n' "$STATE" | sed -n 's/^HEAD=//p')"
R_DIRTY="$(printf '%s\n' "$STATE" | sed -n 's/^DIRTY=//p')"
R_ACTIVE="$(printf '%s\n' "$STATE" | sed -n 's/^ACTIVE=//p')"
R_FREE="$(printf '%s\n' "$STATE" | sed -n 's/^FREE=//p')"
ok "copie d'Oracle a ${R_HEAD:0:7} ; service $R_ACTIVE ; ${R_FREE} Mo libres"
[ "${R_DIRTY:-1}" = 0 ] || die "la copie git d'Oracle a $R_DIRTY modification(s) locale(s) : inspecte-les, rien n'est ecrase"
[ "${R_FREE:-0}" -gt 500 ] || die "moins de 500 Mo libres sur Oracle"
[ "$(printf '%s\n' "$STATE" | sed -n 's/^SUDO=//p')" = 1 ] || die "sudo sans mot de passe indisponible sur Oracle"
if [ "$R_HEAD" = "$SHA" ]; then
    info "Oracle est deja a ce commit : le code ne changera pas"
elif git merge-base --is-ancestor "$R_HEAD" "$SHA" 2>/dev/null; then
    ok "avance rapide ${R_HEAD:0:7} -> ${SHA:0:7} ($(git rev-list --count "$R_HEAD..$SHA") commit(s))"
else
    die "Oracle a un commit que ce PC n'a pas (${R_HEAD:0:7}) : recupere-le d'abord, rien n'est reecrit"
fi

if [ "$DRY" = 1 ]; then
    step "-" "--dry-run : rien n'a ete modifie. Ferait : sauvegarde, deploiement$([ "$RESTART" = 1 ] && echo ', redemarrage'), selftest, verification."
    exit 0
fi
if [ "$YES" = 0 ] && [ "$BACKUP_ONLY" = 0 ]; then
    printf '\n  Deployer %s sur %s%s ? [o/N] ' "${SHA:0:7}" "$SSH_TARGET" "$([ "$RESTART" = 1 ] && echo ' et redemarrer jarvis')"
    read -r answer
    case "$answer" in o|O|oui|y|yes) ;; *) die "annule" ;; esac
fi

# ── 5. sauvegarde ────────────────────────────────────────────────────────────
step 5 "sauvegarde d'Oracle"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/jarvis-$STAMP-${R_HEAD:0:7}.tgz"
remote "set -e
    sudo install -d -m 700 -o root -g root '$BACKUPS'
    sudo tar czf '$BACKUP' --exclude=.venv --exclude=__pycache__ -C /opt jarvis
    sudo chmod 600 '$BACKUP'
    echo '$R_HEAD' | sudo tee '$BACKUP.commit' >/dev/null
    # find sous sudo : le dossier est a root (700), un glob de l'utilisateur ne le lit pas.
    sudo find '$BACKUPS' -maxdepth 1 -name 'jarvis-*.tgz' -printf '%T@ %p\n' | sort -rn \
        | tail -n +$((KEEP + 1)) | cut -d' ' -f2- | while read -r old; do sudo rm -f \"\$old\" \"\$old.commit\"; done
    sudo du -h '$BACKUP' | cut -f1" > "$WORK/backup.txt" || die "sauvegarde impossible : rien n'est deploye"
ok "$BACKUP ($(tail -1 "$WORK/backup.txt")) — config/, memory/, uploads/ compris ; ${KEEP} gardees"
if [ "$BACKUP_ONLY" = 1 ]; then
    step "-" "--backup-only : termine, rien d'autre n'a change"
    exit 0
fi

# ── 6. deploiement ───────────────────────────────────────────────────────────
step 6 "deploiement"
scp -q -o BatchMode=yes "$WORK/jarvis.bundle" "$SSH_TARGET:/tmp/jarvis-$SHA.bundle" 2>&1 | grep -v '^# ' || true
remote "set -e; cd '$REMOTE_REPO'
    git fetch -q /tmp/jarvis-$SHA.bundle +HEAD:refs/deploy/incoming
    git merge --ff-only -q refs/deploy/incoming
    rm -f /tmp/jarvis-$SHA.bundle
    test \"\$(git rev-parse HEAD)\" = '$SHA'" || die "mise a jour git impossible (sauvegarde : $BACKUP)"
ok "copie git d'Oracle a ${SHA:0:7}"
if [ "$MODEL_MODE" = SHIP ]; then
    R_MODEL_SHA="$(remote "sha256sum '$REMOTE_REPO/$MODEL_DIR/${MODEL_FILES[0]}' 2>/dev/null | cut -d' ' -f1" || true)"
    if [ "$R_MODEL_SHA" = "$MODEL_SHA" ]; then
        ok "modele deja present et identique"
    else
        remote "mkdir -p '$REMOTE_REPO/$MODEL_DIR'"
        for f in "${MODEL_FILES[@]}"; do
            scp -q -o BatchMode=yes "$MODEL_DIR/$f" "$SSH_TARGET:$REMOTE_REPO/$MODEL_DIR/$f" 2>&1 | grep -v '^# ' || true
        done
        R_MODEL_SHA="$(remote "sha256sum '$REMOTE_REPO/$MODEL_DIR/${MODEL_FILES[0]}' | cut -d' ' -f1")"
        [ "$R_MODEL_SHA" = "$MODEL_SHA" ] || die "le modele copie n'a pas le bon SHA-256"
        ok "modele copie dans la copie git (pas dans /opt : install.sh l'y recopie)"
    fi
fi
remote "cd '$REMOTE_REPO' && sudo bash deployment/install.sh" > "$WORK/install.log" \
    || { tail -20 "$WORK/install.log" >&2; die "install.sh a echoue (sauvegarde : $BACKUP)"; }
# install.sh colore ses titres (\033[1m▶ ...) : compter les ▶, pas les debuts de ligne.
ok "install.sh ($(grep -c '▶' "$WORK/install.log" 2>/dev/null || true) etapes)"

# ── 7. redemarrage ───────────────────────────────────────────────────────────
step 7 "redemarrage"
if [ "$RESTART" = 1 ]; then
    remote "sudo systemctl restart jarvis
        for i in \$(seq 1 30); do curl -s -m 2 http://127.0.0.1:8000/health | grep -q '\"ok\":true' && exit 0; sleep 1; done
        exit 1" >/dev/null || die "jarvis ne repond pas sur /health apres 30 s (sauvegarde : $BACKUP)"
    ok "jarvis redemarre, /health repond"
else
    info "--no-restart : le service tourne encore sur l'ANCIEN code"
fi

# ── 8. selftest ──────────────────────────────────────────────────────────────
step 8 "selftest sur Oracle"
if remote "sudo -u jarvis sh -c 'cd /opt/jarvis && exec .venv/bin/python -m server.selftest'" > "$WORK/selftest.log"; then
    ok "$(grep -aE 'checks passed' "$WORK/selftest.log" | tail -1 | tr -s ' ')"
else
    grep -aE 'FAIL' "$WORK/selftest.log" | head -8 >&2
    die "server.selftest echoue sur Oracle (sauvegarde : $BACKUP)"
fi

# ── 9. version ───────────────────────────────────────────────────────────────
step 9 "verification de la version"
CHECK="$(remote "cd '$REMOTE_REPO'
    echo HEAD=\$(git rev-parse HEAD)
    echo DRIFT=\$(sudo rsync -rlcni --delete --exclude .git --exclude __pycache__ --exclude .venv \
        --exclude config/api_keys.json --exclude config/device_credentials.json --exclude config/certs/ \
        --exclude config/whatsapp_web/ --exclude memory/long_term.json --exclude client-android/ \
        ./ /opt/jarvis/ | grep -v '/\$' | wc -l)
    echo HEALTH=\$(curl -s -m 3 http://127.0.0.1:8000/health | grep -c '\"ok\":true')
    TOKEN=\$(sudo python3 -c 'import json;print(json.load(open(\"/opt/jarvis/config/device_credentials.json\"))[\"device_token\"])')
    B=\$(curl -s -X POST http://127.0.0.1:8000/api/device-login -H 'Content-Type: application/json' -d \"{\\\"device_token\\\":\\\"\$TOKEN\\\"}\" | python3 -c 'import sys,json;print(json.load(sys.stdin).get(\"token\",\"\"))')
    echo SERVED=\$(curl -s http://127.0.0.1:8000/api/avatar/manifest -H \"Authorization: Bearer \$B\" | python3 -c 'import sys,json;m=json.load(sys.stdin).get(\"model\");print(m[\"sha256\"] if m else \"none\")')
    echo '$(date -Iseconds) $SHA $BACKUP' >> \$HOME/jarvis-deploys.log")"
C_HEAD="$(printf '%s\n' "$CHECK" | sed -n 's/^HEAD=//p')"
C_DRIFT="$(printf '%s\n' "$CHECK" | sed -n 's/^DRIFT=//p')"
C_HEALTH="$(printf '%s\n' "$CHECK" | sed -n 's/^HEALTH=//p')"
C_SERVED="$(printf '%s\n' "$CHECK" | sed -n 's/^SERVED=//p')"
[ "$C_HEAD" = "$SHA" ] || die "la copie git d'Oracle est a ${C_HEAD:0:7}, pas ${SHA:0:7}"
ok "copie git == ${SHA:0:7}"
[ "${C_DRIFT:-1}" = 0 ] || die "/opt/jarvis differe du commit ($C_DRIFT fichier(s))"
ok "/opt/jarvis identique au commit (secrets et donnees exclus)"
[ "${C_HEALTH:-0}" = 1 ] || die "/health ne repond pas"
ok "/health"
if [ "$MODEL_MODE" = SHIP ]; then
    [ "$C_SERVED" = "$MODEL_SHA" ] || die "le telephone recevrait ${C_SERVED:0:16}, pas ${MODEL_SHA:0:16}"
    ok "modele servi aux telephones : sha256 ${C_SERVED:0:16}…"
else
    info "modele servi : $C_SERVED"
fi
step "-" "Oracle a ${SHA:0:7}. Retour arriere : deployment/RECOVERY.md, sauvegarde $BACKUP"
