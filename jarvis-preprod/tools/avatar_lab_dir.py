"""
tools/avatar_lab_dir.py — an avatar/ for the lab, with a model whose licence
allows it.

    python tools/avatar_lab_dir.py [slot]      prints the directory it built

WHY
    The model active on this machine may carry a licence nobody has settled
    (the female face's profile says « inconnue — à renseigner »). The lab
    must not copy such a file onto a device, even an emulator. So the face
    suite runs against a separate avatar/ directory in which a model with a
    KNOWN, permissive licence is active — by default `jarvis/male`, Microsoft
    Rocketbox, MIT with attribution (its LICENSE.md travels with it).

NO SECOND MECHANISM
    The manifest is produced by presence.models.activate(), the function
    `install_model --use` calls, from the repository's own manifest: the
    lab's manifest is what the desktop would read if that model were active.
    Files are hard-linked when possible (no 46 MB copy), copied otherwise.

It refuses a slot whose profile does not declare a licence the lab may use.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from presence import models  # noqa: E402
from server.avatar_api import licence_allows_phone  # noqa: E402  (the one list)


def build(slot: str = "jarvis/male", dest: Path | None = None) -> Path:
    source = models.find(slot)
    if source is None:
        raise SystemExit(f"modele introuvable : {slot}")
    profile = models.read_profile(source)
    licence = str(profile.get("license") or "")
    if not licence_allows_phone(licence):
        raise SystemExit(f"{slot} : licence « {licence} » — refuse pour le labo")
    ok, why = models.check_profile(source)
    if not ok:
        raise SystemExit(f"{slot} : {why}")

    dest = Path(dest or tempfile.mkdtemp(prefix="jarvis-avatar-lab-"))
    rel = Path(models.relative(source))
    target_dir = dest / "models" / rel.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    for f in source.parent.iterdir():
        if f.is_file():
            out = target_dir / f.name
            if out.exists():
                out.unlink()
            try:
                os.link(f, out)
            except OSError:
                shutil.copy2(f, out)

    manifest = models.read_json(REPO / "avatar" / "manifest.json")
    active = models.activate(manifest, dest / "models" / rel, profile, models_dir=dest / "models")
    models.write_json(dest / "manifest.json", active)
    return dest


if __name__ == "__main__":
    print(build(*(sys.argv[1:2] or ["jarvis/male"])))
