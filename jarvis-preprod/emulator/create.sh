#!/usr/bin/env bash
#
# emulator/create.sh — crée l'AVD du laboratoire. Idempotent : --force recrée.
#
# DEUX CHOIX QUI ONT UNE RAISON
#
#   L'AVD vit sur D:, pas sous ~/.android/avd. Un AVD pèse 8 à 10 Go une fois
#   rodé et C: n'avait que 8 Go libres au moment de la mise en place. Un disque
#   plein pendant un boot d'émulateur ne produit pas une erreur claire, il
#   produit un AVD corrompu qu'il faut recréer. ANDROID_AVD_HOME évite ça.
#
#   L'image est android-36 x86_64. minSdk est 26 et targetSdk 37 ; une API 36
#   fait tourner une application qui cible 37 sans difficulté — targetSdk est
#   une déclaration d'intention, pas une exigence de plancher. L'inverse serait
#   faux : une image plus ancienne que minSdk refuserait l'installation.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../config/lab.env"

export JAVA_HOME="${LAB_JDK//\//\\}"
export ANDROID_HOME="${LAB_SDK//\//\\}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_AVD_HOME="${LAB_AVD_HOME//\//\\}"
export MSYS_NO_PATHCONV=1

IMAGE="${LAB_IMAGE:-system-images;android-36;google_apis_playstore;x86_64}"
AVDMANAGER="$LAB_SDK/cmdline-tools/23.0/bin/avdmanager.bat"

if [ ! -x "$AVDMANAGER" ] && [ ! -f "$AVDMANAGER" ]; then
    echo "avdmanager introuvable : $AVDMANAGER" >&2
    echo "Installe-le :  sdkmanager \"cmdline-tools;23.0\"" >&2
    echo "(Une version ancienne ne lit que le XML v3 et rapporte" >&2
    echo " 'Valid system image paths are: null' meme quand l'image est la.)" >&2
    exit 1
fi

mkdir -p "$LAB_AVD_HOME"

if [ -d "$LAB_AVD_HOME/$LAB_AVD.avd" ] && [ "${1:-}" != "--force" ]; then
    echo "  AVD '$LAB_AVD' existe deja. --force pour le recreer."
    exit 0
fi

echo "  Creation de l'AVD '$LAB_AVD' ($IMAGE)"
echo "no" | "$AVDMANAGER" create avd -n "$LAB_AVD" -k "$IMAGE" -d "${LAB_DEVICE:-pixel_6}" --force

CFG="$LAB_AVD_HOME/$LAB_AVD.avd/config.ini"
python - "$CFG" <<'PY'
import io, sys
path = sys.argv[1]
cfg = {}
for line in io.open(path, encoding="utf-8"):
    if "=" in line:
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
# 4 Go de RAM et 6 Go de data : au-dessous, l'application s'installe mais
# l'emulateur se met a tuer des processus en arriere-plan, ce qui produit des
# echecs de test qui ressemblent a des bugs du service de premier plan.
cfg.update({
    "hw.ramSize": "4096",
    "vm.heapSize": "512",
    "disk.dataPartition.size": "6144",
    "hw.keyboard": "yes",
    "hw.audioInput": "yes",
    "hw.audioOutput": "yes",
    "showDeviceFrame": "no",
})
io.open(path, "w", encoding="utf-8").write(
    "\n".join(f"{k}={v}" for k, v in sorted(cfg.items())) + "\n")
print(f"  config.ini ajuste : RAM 4096 Mo, data 6144 Mo")
PY

echo "  Pret. Demarrage :  emulator/start.sh"
