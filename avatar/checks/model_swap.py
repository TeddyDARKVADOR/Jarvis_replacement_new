"""Changer de visage sans toucher au cerveau — et sans rien perdre.

    python avatar/checks/model_swap.py

LE TEST DE QUALITE DE L'ARCHITECTURE
    Le visage actuel est feminin ; le suivant sera masculin. Si l'architecture
    est juste, ce changement est une INSTALLATION, pas un chantier : aucun
    fichier de `presence/` ni du moteur n'a a changer, et JARVIS decide
    exactement la meme chose avant et apres.

    Ce controle le prouve dans un repertoire temporaire — jamais sur le vrai
    manifeste :

      1. un modele « feminin » en place, avec la calibration reelle du
         manifeste (sourdine de `tongue01`, pose des bras mesuree)
      2. on installe un modele « masculin » synthetique, exporte dans une
         AUTRE convention de noms (`browDown_L`), avec d'autres os et des
         visemes Oculus
      3. on compare, pour les seize intentions sur tous les etats machine, les
         Performances produites avant et apres : elles doivent etre IDENTIQUES
      4. la calibration du premier n'a pas fui sur le second, elle a ete
         rangee dans son profil — et `--use` la rend a l'identique
      5. les preferences (camera, couleurs, `rig.motion`) n'ont pas bouge
      6. un fichier modifie sous le meme nom est refuse par son empreinte
      7. un manifeste de version 1 est migre sans rien perdre

    Il est aussi appele par `presence/selftest.py`, qui n'a besoin ni de
    navigateur ni de reseau.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
from presence import catalog as catalog_mod  # noqa: E402
from presence import inspect as inspector  # noqa: E402
from presence import install_model  # noqa: E402
from presence import models  # noqa: E402
from presence.director import Director, parse  # noqa: E402
from presence.model import Intent  # noqa: E402

STATES = ["ACTIVE", "LISTENING", "THINKING", "SPEAKING", "WAKING", "ERROR", "CONFIRM", "SLEEPING"]


class _Sandbox:
    """Les quatre constantes de chemin, redirigees le temps du test."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.models = root / "models"
        self.manifest = root / "manifest.json"
        self.saved = {}

    def __enter__(self):
        self.models.mkdir(parents=True)
        targets = [(models, "MODELS_DIR", self.models), (models, "MANIFEST", self.manifest),
                   (inspector, "MANIFEST", self.manifest), (install_model, "MODELS_DIR", self.models),
                   (catalog_mod, "MANIFEST", self.manifest)]
        for module, name, value in targets:
            self.saved[(module, name)] = getattr(module, name)
            setattr(module, name, value)
        return self

    def __exit__(self, *exc):
        for (module, name), value in self.saved.items():
            setattr(module, name, value)
        catalog_mod.catalogue(force=True)


def brain() -> list[dict]:
    """Ce que JARVIS decide, pour tout ce qu'il peut decider. Le modele n'y a
    aucune place — c'est exactement ce qu'on verifie."""
    cat = catalog_mod.catalogue(force=True)
    out = []
    for state in STATES:
        out.append(Director(cat).resolve(state, now=0.0).as_json())
        for intent in Intent:
            director = Director(cat)
            director.set_intent(parse(json.dumps({"intent": intent.value})), now=0.0)
            out.append(director.resolve(state, now=0.1).as_json())
    return out


def _quiet(fn, *args, **kwargs):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def run() -> tuple[bool, str]:
    real = json.loads((BASE / "avatar" / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp, _Sandbox(Path(tmp)) as box:
        # ── 1. le visage actuel, avec sa vraie calibration ──────────────────
        female = box.models / "jarvis" / "female" / "jarvis.glb"
        female.parent.mkdir(parents=True)
        female.write_bytes(fixtures.glb(
            morphs=list(fixtures.ARKIT_52) + [f"viseme_{v}" for v in fixtures.OCULUS],
            bones=fixtures.HUMANOID, name="female"))
        manifest = copy.deepcopy(real)
        manifest.pop("version", None)               # un manifeste d'avant les profils
        manifest["model"]["file"] = "jarvis/female/jarvis.glb"
        manifest["model"].pop("profile", None)
        models.write_json(box.manifest, manifest)
        preferences = {k: copy.deepcopy(manifest.get(k)) for k in ("camera", "look", "gestures")}
        motion = manifest["rig"].get("motion")
        calibration = {"muteMeshes": manifest["model"].get("muteMeshes"),
                       "armRest": manifest["rig"].get("armRest")}

        _quiet(install_model.install, "jarvis/female/jarvis.glb", adopt=True,
               license="a renseigner", provenance="adopte par le test")
        before = brain()
        after_adopt = models.read_json(box.manifest)
        assert after_adopt["version"] == models.MANIFEST_VERSION, "manifeste non migre"
        assert after_adopt["model"]["muteMeshes"] == calibration["muteMeshes"], (
            "l'adoption a perdu la sourdine du modele en place")

        # ── 2. le visage masculin, dans une autre convention ────────────────
        source = box.root / "male_export.glb"
        source.write_bytes(fixtures.glb(
            morphs=fixtures.arkit_as("underscore") + [f"viseme_{v}" for v in fixtures.OCULUS],
            bones=[f"mixamorig:{b}" for b in fixtures.HUMANOID], name="male"))
        _quiet(install_model.install, str(source), slot="jarvis/male", name="male.glb",
               license="CC0", provenance="export de test", label="JARVIS Male")
        after = brain()
        manifest = models.read_json(box.manifest)

        # ── 3. le cerveau n'a rien vu ────────────────────────────────────────
        diffs = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert not diffs and len(before) == len(after), (
            f"{len(diffs)} decisions changent avec le visage — le cerveau connait le modele "
            f"(premiere : {before[diffs[0]]} -> {after[diffs[0]]})" if diffs else "tailles")

        # ── 4. rien n'a fui, rien n'est perdu ────────────────────────────────
        assert manifest["model"]["file"] == "jarvis/male/male.glb", manifest["model"]["file"]
        assert len(manifest["model"]["morphAliases"]) >= 36, (
            f"convention `_L` non traduite : {len(manifest['model']['morphAliases'])} alias")
        assert not manifest["model"].get("muteMeshes"), (
            "la sourdine du visage feminin s'applique au masculin")
        assert not manifest["rig"].get("armRest"), "la pose des bras du feminin a fui"
        assert manifest["rig"]["bones"]["head"] == "mixamorig:Head", manifest["rig"]["bones"]

        female_profile = models.read_profile(female)
        assert female_profile["calibration"].get("muteMeshes") == calibration["muteMeshes"], (
            "la calibration du visage feminin n'a pas ete rangee dans son profil")
        male = box.models / "jarvis" / "male" / "male.glb"
        male_profile = models.read_profile(male)
        assert male_profile["license"] == "CC0" and male_profile["name"] == "JARVIS Male"
        assert male_profile["capabilities"]["visemes"], "visemes non mesures"

        # ── 5. les preferences n'ont pas bouge ───────────────────────────────
        for key, value in preferences.items():
            assert manifest.get(key) == value, f"preference `{key}` modifiee par l'installation"
        assert manifest["rig"].get("motion") == motion, "rig.motion modifie par l'installation"

        # Un reglage fait a la main sur le masculin, puis aller-retour.
        manifest["model"]["muteMeshes"] = ["hair"]
        models.write_json(box.manifest, manifest)
        _quiet(install_model.use, "jarvis/female")
        back = models.read_json(box.manifest)
        assert back["model"]["muteMeshes"] == calibration["muteMeshes"], "retour : sourdine perdue"
        assert back["rig"]["armRest"] == calibration["armRest"], "retour : pose des bras perdue"
        assert models.read_profile(male)["calibration"]["muteMeshes"] == ["hair"], (
            "le reglage manuel du masculin a ete perdu en changeant de visage")
        _quiet(install_model.use, "jarvis/male")
        assert models.read_json(box.manifest)["model"]["muteMeshes"] == ["hair"]
        assert brain() == before, "le cerveau a change apres deux allers-retours"

        # ── 6. l'empreinte ───────────────────────────────────────────────────
        ok, _ = models.check_profile(male)
        assert ok, "profil refuse alors que le fichier n'a pas change"
        male.write_bytes(male.read_bytes() + b"\0\0\0\0")
        ok, why = models.check_profile(male)
        assert not ok and "change" in why, "un fichier modifie passe pour le meme"

    return True, (f"feminin -> masculin -> feminin -> masculin : {len(before)} decisions "
                  f"identiques ; calibration rangee et rendue ; preferences intactes ; "
                  f"empreinte verifiee")


if __name__ == "__main__":
    try:
        ok, detail = run()
    except AssertionError as exc:
        ok, detail = False, str(exc)
    print(f"\n  [{'OK  ' if ok else 'FAUX'}] {detail}\n")
    raise SystemExit(0 if ok else 1)
