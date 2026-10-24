"""
matthew/modal_app.py — l'application Modal de Matthew (importée seulement là où le SDK `modal` est
installé : PC, GitHub Actions, et le conteneur lui-même). Modal exige des fonctions déclarées au
niveau du module : le conteneur réimporte `matthew.modal_app` pour les retrouver. Toute la logique
est dans matthew/modal_gpu.py (testable sans Modal).
"""
from __future__ import annotations

import modal

from matthew import REPO, config
from matthew.modal_gpu import APP, MOUNT, VOLUME, run_variant

_cfg = config()
vol = modal.Volume.from_name(VOLUME, create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("curl", "zstd", "git", "build-essential", "cmake", "libcurl4-openssl-dev")
         .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
         .uv_pip_install(_cfg["modal_unsloth"], "requests")
         .env({"HF_HOME": f"{MOUNT}/hf", "PYTHONPATH": "/root", "MATTHEW_HOME": "/tmp/matthew-home"})
         .add_local_dir(str(REPO / "matthew"), "/root/matthew",
                        ignore=["**/__pycache__", "packs", "vitrine_site", "**/*.pyc"]))
app = modal.App(APP)


@app.function(image=image, gpu=_cfg["modal_gpu"], volumes={MOUNT: vol}, timeout=3 * 3600,
              single_use_containers=True)
def variant(stamp: str, v: dict) -> dict:
    vol.reload()
    return run_variant(stamp, v, commit=vol.commit)
