"""
presence/models.py — la bibliotheque des visages : ce qui est installe, et ce
qu'on sait de chacun.

POURQUOI UN PROFIL PAR MODELE
    `avatar/manifest.json` melangeait deux choses de nature differente :

        les PREFERENCES     la camera, les couleurs, `rig.motion` — des choix
                            sur JARVIS, valables quel que soit son visage
        la CALIBRATION      `muteMeshes`, `armRest`, les signes du regard, les
                            alias de formes, les os — des verites sur UN
                            fichier, mesurees a la main, fausses pour tout autre

    Installer un nouveau modele reecrivait le manifeste en gardant tout le
    reste : le `tongue01` mis en sourdine du modele actuel restait applique au
    suivant (et, compare par suffixe, pouvait y eteindre autre chose), son
    `armRest` aussi — et la calibration de l'ancien etait perdue le jour ou on
    voulait y revenir.

    Chaque modele a donc un profil a cote de lui (`jarvis.glb` ->
    `jarvis.model.json`), versionne, qui porte :

        identite     id, nom, version
        origine      licence, provenance, date, empreinte SHA-256, taille
        mesures      format, compression, formes ARKit, visemes, yeux, tete,
                     membres, animations — lus dans le fichier, pas declares
        calibration  ce qui a ete regle a la main pour CE fichier

    Le manifeste reste le seul fichier que lisent le moteur et le directeur :
    activer un modele y recopie sa calibration et garde les preferences. Une
    lecture, une verite — rien ne change du cote qui lit.

POURQUOI L'EMPREINTE
    Un profil decrit un fichier precis. Si le fichier change sous le meme nom
    (un nouvel export, un telechargement different), la calibration et les
    mesures ne sont plus vraies. `presence/selftest.py` recalcule l'empreinte
    du modele actif et refuse un profil qui ne correspond plus.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "avatar" / "models"
MANIFEST = BASE_DIR / "avatar" / "manifest.json"

PROFILE_SCHEMA = 1
MANIFEST_VERSION = 2

#: Ce qui appartient a UN fichier : `(section, cle)` du manifeste.
CALIBRATION_KEYS = (
    ("model", "morphAliases"),
    ("model", "muteMeshes"),
    ("model", "scale"),
    ("model", "position"),
    ("rig", "parts"),
    ("rig", "bones"),
    ("rig", "armRest"),
    ("rig", "gaze"),
)

#: Les valeurs d'un modele qu'on n'a jamais calibre — celles que le moteur
#: prendrait de toute facon en leur absence.
DEFAULT_CALIBRATION = {
    "morphAliases": {},
    "muteMeshes": [],
    "scale": 1.0,
    "position": [0, 0, 0],
    "armRest": {},
    "gaze": {},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_path(model: Path) -> Path:
    """`avatar/models/jarvis/female/jarvis.glb` -> `.../jarvis.model.json`."""
    return model.with_name(model.stem + ".model.json")


def relative(model: Path, models_dir: Path | None = None) -> str:
    """Le chemin que le manifeste ecrit : relatif a `avatar/models/`, en `/`."""
    root = (models_dir or MODELS_DIR).resolve()
    return model.resolve().relative_to(root).as_posix()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_profile(model: Path) -> dict:
    return read_json(profile_path(model))


def calibration_of(manifest: dict) -> dict:
    """La calibration telle que le manifeste la porte aujourd'hui."""
    out = {}
    for section, key in CALIBRATION_KEYS:
        value = (manifest.get(section) or {}).get(key)
        if value is not None:
            out[key] = value
    return out


def migrate(manifest: dict) -> dict:
    """Un manifeste de version 1 lu comme un manifeste de version 2.

    Rien n'est retire : les nouveaux champs ont une valeur qui redonne le
    comportement d'avant. `rig.motion` absent reste absent — il vaut "full"
    pour tous les lecteurs, et l'ecrire changerait un manifeste qu'aucun
    humain n'a touche.
    """
    out = json.loads(json.dumps(manifest))
    out.setdefault("model", {})
    out.setdefault("rig", {})
    out.setdefault("camera", {})
    out.setdefault("look", {})
    out.setdefault("gestures", {})
    out["version"] = MANIFEST_VERSION
    return out


def save_active_calibration(manifest: dict, models_dir: Path | None = None) -> Path | None:
    """Ranger la calibration du modele actif dans SON profil, avant d'en changer.

    C'est ce qui rend un changement de visage reversible : la sourdine de
    `tongue01`, la pose des bras mesuree a la main — tout ce qui n'est vrai que
    de ce fichier — part avec lui au lieu d'etre applique au suivant ou perdu.
    """
    root = models_dir or MODELS_DIR
    name = ((manifest.get("model") or {}).get("file") or "").strip()
    if not name:
        return None
    model = root / name
    if not model.is_file():
        return None
    path = profile_path(model)
    profile = read_json(path)
    if not profile:
        return None       # jamais adopte : rien a ranger, rien a ecraser
    profile["calibration"] = dict(profile.get("calibration") or {}, **calibration_of(manifest))
    write_json(path, profile)
    return path


def activate(manifest: dict, model: Path, profile: dict, models_dir: Path | None = None) -> dict:
    """Le manifeste, avec CE modele actif. Les preferences ne bougent pas."""
    out = migrate(manifest)
    calibration = dict(DEFAULT_CALIBRATION)
    calibration.update(profile.get("calibration") or {})

    out["model"]["file"] = relative(model, models_dir)
    out["model"]["profile"] = relative(profile_path(model), models_dir)
    for section, key in CALIBRATION_KEYS:
        if key in calibration:
            out[section][key] = calibration[key]
        else:
            out[section].pop(key, None)
    out["name"] = f"JARVIS — {profile.get('name') or model.stem}"

    # Le cadrage est une preference : on ne le choisit que s'il n'y en a pas.
    if not out["camera"].get("frame"):
        parts = set((profile.get("capabilities") or {}).get("limbs") or [])
        out["camera"]["frame"] = "face" if parts <= {"head"} else "portrait"
    out["camera"].setdefault("fov", 24)
    return out


def build_profile(model: Path, report: dict, *, calibration: dict, previous: dict | None = None,
                  license: str | None = None, source: str | None = None,
                  name: str | None = None, version: str | None = None,
                  models_dir: Path | None = None) -> dict:
    """Le profil d'un fichier : ce qu'on en a MESURE, et ce qu'on en sait."""
    previous = previous or {}
    fingerprint = sha256(model)
    same_file = previous.get("sha256") == fingerprint
    provenance = dict(previous.get("provenance") or {})
    if source:
        provenance["source"] = source
    provenance.setdefault("source", "inconnue — a renseigner (--source)")
    provenance["installed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    provenance["installer"] = "presence.install_model"

    return {
        "schema": PROFILE_SCHEMA,
        "id": relative(model, models_dir).rsplit(".", 1)[0],
        "name": name or previous.get("name") or model.stem,
        # Un nouveau fichier sous le meme nom est une nouvelle version, meme
        # si personne ne l'a dit.
        "version": version or (previous.get("version") if same_file else None)
                   or time.strftime("%Y.%m.%d"),
        "file": model.name,
        "sha256": fingerprint,
        "size": model.stat().st_size,
        "license": license or previous.get("license") or "inconnue — a renseigner (--license)",
        "provenance": provenance,
        "format": report.get("format", "GLB"),
        "compression": report.get("compression", []),
        "capabilities": {
            "arkit": len(report.get("arkit_matched", {})),
            "visemes": report.get("visemes", []),
            "eyes": report.get("eyes", "aucun"),
            "head": report.get("head_bone", False),
            "limbs": report.get("parts", []),
            "animations": len(report.get("animations", [])),
            "vrm": (report.get("vrm_facts") or {}).get("version", ""),
        },
        "calibration": calibration,
    }


def install_file(model: Path, report: dict, *, adopt: bool = False,
                 license: str | None = None, source: str | None = None,
                 name: str | None = None, version: str | None = None,
                 activate_now: bool = True, manifest_path: Path | None = None,
                 models_dir: Path | None = None) -> tuple[dict, dict]:
    """Profiler un fichier deja en place, et l'activer. Rend (profil, manifeste).

    D'ou vient la calibration, dans l'ordre :

      * `adopt`      le manifeste actuel decrit CE fichier (il vient d'etre
                     deplace, ou il n'a jamais eu de profil) : on la reprend
      * reinstall    le fichier a deja un profil : on garde la sienne
      * sinon        un modele neuf, sans calibration — les reglages d'un AUTRE
                     modele ne lui sont jamais appliques

    Et ce qui est detecte gagne sur ce qui etait note, SAUF un reglage manuel
    qui existe encore dans le fichier : un alias de forme qui trouve toujours
    sa cible, un os force qui est toujours la.
    """
    manifest_file = manifest_path or MANIFEST
    root = models_dir or MODELS_DIR
    manifest = migrate(read_json(manifest_file))
    previous = read_profile(model)

    if adopt:
        base = calibration_of(manifest)
    else:
        base = dict(previous.get("calibration") or {})

    detected_aliases = {a: real for a, real in report.get("arkit_matched", {}).items() if a != real}
    bones = dict(report.get("bones") or {})
    node_names = set(report.get("node_names") or [])
    for key, forced in (base.get("bones") or {}).items():
        if forced and (not node_names or forced in node_names):
            bones[key] = forced

    calibration = {k: v for k, v in base.items()
                   if k in ("muteMeshes", "armRest", "gaze", "scale", "position")}
    calibration["morphAliases"] = detected_aliases
    calibration["bones"] = {k: bones.get(k, "") for k in
                            ("head", "neck", "spine", "root", "eyeLeft", "eyeRight")}
    calibration["parts"] = report.get("parts", [])

    profile = build_profile(model, report, calibration=calibration, previous=previous,
                            license=license, source=source, name=name, version=version,
                            models_dir=root)
    write_json(profile_path(model), profile)

    if activate_now:
        current = ((manifest.get("model") or {}).get("file") or "").strip()
        if current and (root / current).resolve() != model.resolve():
            save_active_calibration(manifest, root)
        manifest = activate(manifest, model, profile, root)
        write_json(manifest_file, manifest)
    return profile, manifest


def installed(models_dir: Path | None = None) -> list[tuple[Path, dict]]:
    """Tous les modeles qui ont un profil, du plus recent au plus ancien."""
    root = models_dir or MODELS_DIR
    out = []
    for path in sorted(root.rglob("*.model.json")):
        profile = read_json(path)
        model = path.with_name(profile.get("file", ""))
        if profile and model.is_file():
            out.append((model, profile))
    return out


def find(target: str, models_dir: Path | None = None) -> Path | None:
    """Un modele, par chemin, par identifiant de profil ou par dossier."""
    root = models_dir or MODELS_DIR
    candidate = Path(target)
    for path in (candidate, root / target):
        if path.is_file() and path.suffix.lower() in (".glb", ".gltf", ".vrm"):
            return path
        if path.is_dir():
            inside = [m for m, _ in installed(path)]
            if len(inside) == 1:
                return inside[0]
    for model, profile in installed(root):
        if profile.get("id") == target or profile.get("id", "").startswith(target.rstrip("/") + "/"):
            return model
    return None


def use(model: Path, *, manifest_path: Path | None = None,
        models_dir: Path | None = None) -> tuple[dict, dict]:
    """Activer un modele deja installe. La calibration de l'actuel est rangee."""
    manifest_file = manifest_path or MANIFEST
    root = models_dir or MODELS_DIR
    profile = read_profile(model)
    if not profile:
        raise ValueError(f"{model.name} n'a pas de profil — l'installer d'abord")
    manifest = migrate(read_json(manifest_file))
    save_active_calibration(manifest, root)
    manifest = activate(manifest, model, profile, root)
    write_json(manifest_file, manifest)
    return profile, manifest


def check_profile(model: Path) -> tuple[bool, str]:
    """Le profil decrit-il encore CE fichier ? (vrai, raison)"""
    profile = read_profile(model)
    if not profile:
        return False, "aucun profil — python -m presence.install_model --adopt <fichier>"
    for key in ("schema", "id", "sha256", "license", "provenance", "capabilities", "calibration"):
        if key not in profile:
            return False, f"profil incomplet : `{key}` manque"
    if profile["sha256"] != sha256(model):
        return False, ("le fichier a change depuis son installation — mesures et calibration "
                       "ne sont plus garanties (reinstaller le modele)")
    return True, f"{profile['id']} v{profile.get('version')} · {profile['license']}"
