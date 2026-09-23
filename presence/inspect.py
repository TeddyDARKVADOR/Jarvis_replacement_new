"""
presence/inspect.py — what is actually inside a model, before trusting a word of it.

    python -m presence.inspect test/Michelle.glb
    python -m presence.inspect jarvis/female/jarvis.glb --write

WHY THIS EXISTS
    `avatar/manifest.json` tells `catalog.py` what JARVIS may ask for: which
    limbs the body has, which blendshapes its face carries. Written by hand,
    that file is wrong within a week — someone types `["head","torso","arms",
    "legs"]` because it sounds complete, JARVIS is offered `wave`, the model has
    no arms, and the symptom is a gesture that silently does nothing.

    So the manifest is *derived*. This reads the glTF itself, reports what is
    genuinely there, and with `--write` writes the answer back. The only fields
    a human still edits are the ones taste decides: camera, colours, scale.

WHY IT PARSES GLB BY HAND INSTEAD OF USING A LIBRARY
    A GLB is a 12-byte header and a JSON chunk. Everything this needs — mesh
    names, `extras.targetNames`, animation names, node names, skin joints — is
    in that JSON. Pulling in a glTF library to read a dictionary would put a
    dependency on the one package in this project that must import nothing
    (`presence/` is checked for exactly that by the selftest), and it would
    still not answer the only hard question here, which is naming.

THE NAMING PROBLEM, WHICH IS THE WHOLE JOB
    No two exporters agree. Ready Player Me writes `browInnerUp` on a mesh
    called `Wolf3D_Head`. A Blender export writes `brow_inner_up`. An older FBX
    round trip writes `Brow_Inner_Up`. A VRM writes `Fcl_BRW_Surprised` and
    means something else entirely. The matcher below is the same three-pass
    rule `avatar/js/body_gltf.js` applies at runtime — exact, then normalised,
    then the manifest's own aliases — so what this tool reports is what the
    renderer will actually find, not what it ought to find.
"""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from presence.model import RigPart  # noqa: E402
from presence.vocabulary import ARKIT_52  # noqa: E402

MANIFEST = BASE_DIR / "avatar" / "manifest.json"

_JSON_CHUNK = 0x4E4F534A

#: Bone names that prove a limb exists. Normalised — lowercase, separators
#: stripped — so `mixamorig:LeftArm`, `LeftArm` and `left_arm` are one entry.
#:
#: The 3ds Max Biped convention (`Bip01_Head`, `Bip001 L UpperArm`) is here
#: because it is everywhere in game assets and was nowhere in this table: the
#: Microsoft Rocketbox male was reported as having no head — while the
#: renderer, whose table already knew `bip01head`, found it. An inspector that
#: promises less than the renderer does is lying the other way round.
_PART_EVIDENCE: dict[RigPart, tuple[str, ...]] = {
    RigPart.HEAD: ("head", "neck", "bip01head", "bip001head", "bip01neck", "bip001neck"),
    RigPart.TORSO: ("spine", "spine1", "spine2", "chest", "upperchest", "hips",
                    "bip01spine", "bip01spine1", "bip01spine2", "bip01pelvis",
                    "bip001spine", "bip001spine1", "bip001spine2", "bip001pelvis"),
    RigPart.ARMS: ("leftarm", "rightarm", "leftforearm", "rightforearm",
                   "lefthand", "righthand", "leftupperarm", "rightupperarm",
                   "bip01lupperarm", "bip01rupperarm", "bip01lforearm", "bip01rforearm",
                   "bip001lupperarm", "bip001rupperarm", "bip001lforearm", "bip001rforearm"),
    RigPart.LEGS: ("leftupleg", "rightupleg", "leftleg", "rightleg",
                   "leftfoot", "rightfoot", "leftupperleg", "rightupperleg",
                   "bip01lthigh", "bip01rthigh", "bip01lcalf", "bip01rcalf",
                   "bip001lthigh", "bip001rthigh", "bip001lcalf", "bip001rcalf"),
}

#: Where each bone the engine drives is found, by normalised name, first match
#: wins. Mirrored in `BONE_HINTS` of `avatar/js/body_gltf.js`; the selftest
#: requires every entry here to exist there. The JS table may hold MORE:
#: three.js strips `:` and `.` from node names, so `mixamorig:Head` reaches
#: the renderer as `mixamorigHead`, a spelling this file never sees.
BONE_HINTS: dict[str, tuple[str, ...]] = {
    "head": ("head", "bip01head", "bip001head", "headjoint"),
    "neck": ("neck", "bip01neck", "bip001neck"),
    "spine": ("spine2", "spine1", "spine", "chest", "upperchest",
              "bip01spine2", "bip01spine1", "bip01spine",
              "bip001spine2", "bip001spine1", "bip001spine"),
    "root": ("hips", "root", "armature", "bip01pelvis", "bip001pelvis"),
    "eyeLeft": ("lefteye", "eyeleft", "eyel", "bip01leye", "bip001leye"),
    "eyeRight": ("righteye", "eyeright", "eyer", "bip01reye", "bip001reye"),
}


#: A trailing side marker, in the two spellings the world actually uses.
#: `browDown_L` and `browDownLeft` are the same shape, and a matcher that does
#: not know this rejects 36 of facecap.glb's 52 blendshapes — a real, textured,
#: fully ARKit-rigged human head, discarded over an underscore.
_SIDE_SEPARATED = re.compile(r"[._\s-]([lr])$")
_SIDE_WORD = re.compile(r"(left|right)$")


def normalise(name: str) -> str:
    """The one spelling of a name, whatever the exporter called it.

    Three things are removed, in this order, and the order matters:

      1. the exporter's prefix   `Wolf3D_Head.browInnerUp` -> `browInnerUp`
                                 `mixamorig:Head`          -> `Head`
      2. the side marker         `_L` / `.R` / `Left` / `Right`, put back at
                                 the end in one spelling so both conventions
                                 land on the same key
      3. every separator         `brow_inner_up` -> `browinnerup`

    Doing (3) before (2) is the bug that costs 36 blendshapes: once the
    underscore in `browDown_L` is gone, the `l` is just the last letter of a
    word and there is nothing left to recognise.
    """
    # Le point est ambigu : il separe un prefixe d'export
    # (`Wolf3D_Head.browInnerUp`) ET un marqueur de cote (`mouthSmile.L`,
    # convention Blender). Couper aveuglement au dernier point reduit la
    # seconde forme a "L" et perd la forme entiere. On recolle donc le
    # dernier segment quand il n'est qu'un cote.
    segments = [s for s in re.split(r"[.:]", str(name).strip()) if s]
    if len(segments) > 1 and segments[-1].lower() in ("l", "r"):
        tail = f"{segments[-2]}_{segments[-1]}".lower()
    else:
        tail = (segments[-1] if segments else "").lower()

    side = ""
    match = _SIDE_SEPARATED.search(tail)
    if match:
        side = "left" if match.group(1) == "l" else "right"
        tail = tail[:match.start()]
    else:
        match = _SIDE_WORD.search(tail)
        if match:
            side = match.group(1)
            tail = tail[:match.start()]

    return re.sub(r"[^a-z0-9]", "", tail) + side


def read_gltf(path: Path) -> dict:
    """The JSON of a .glb, .gltf or .vrm. A VRM *is* a GLB with extensions."""
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        # .gltf — plain JSON, no container.
        return json.loads(raw.decode("utf-8"))

    _, _, total = struct.unpack("<III", raw[:12])
    offset = 12
    while offset < min(total, len(raw)):
        length, kind = struct.unpack("<II", raw[offset:offset + 8])
        body = raw[offset + 8:offset + 8 + length]
        if kind == _JSON_CHUNK:
            return json.loads(body.decode("utf-8"))
        offset += 8 + length
    raise ValueError(f"{path.name} : aucun bloc JSON dans le GLB")


def morph_names(gltf: dict) -> dict[str, list[str]]:
    """Every morph target, per mesh, in index order.

    glTF has no official place for these names, which is the root of the whole
    problem: the near-universal convention is `mesh.extras.targetNames`, and
    some exporters put it on the primitive instead. Both are read.
    """
    found: dict[str, list[str]] = {}
    for index, mesh in enumerate(gltf.get("meshes", [])):
        name = mesh.get("name") or f"mesh{index}"
        names = (mesh.get("extras") or {}).get("targetNames")
        if not names:
            for primitive in mesh.get("primitives", []):
                names = (primitive.get("extras") or {}).get("targetNames")
                if names:
                    break
        if not names:
            # Des cibles existent mais ne sont pas nommees : on les compte,
            # elles sont inutilisables telles quelles.
            count = max((len(p.get("targets", [])) for p in mesh.get("primitives", [])),
                        default=0)
            if count:
                found[name] = [f"<sans nom {i}>" for i in range(count)]
            continue
        found[name] = list(names)
    return found


def match_arkit(all_names: list[str], aliases: dict[str, str] | None = None) -> dict[str, str]:
    """ARKit name -> the name this model actually uses. Three passes, cheapest first."""
    by_normalised: dict[str, str] = {}
    for name in all_names:
        by_normalised.setdefault(normalise(name), name)

    resolved: dict[str, str] = {}
    for arkit in ARKIT_52:
        hit = by_normalised.get(normalise(arkit))
        if hit:
            resolved[arkit] = hit
    for arkit, declared in (aliases or {}).items():
        hit = by_normalised.get(normalise(declared))
        if hit and arkit in ARKIT_52:
            resolved[arkit] = hit
    return resolved


def detect_parts(gltf: dict) -> set[RigPart]:
    names = {normalise(node.get("name", "")) for node in gltf.get("nodes", [])}
    parts = {RigPart.HEAD}     # tout modele a au moins de quoi tourner la tete
    for part, evidence in _PART_EVIDENCE.items():
        if any(hint in names for hint in evidence):
            parts.add(part)
    return parts


def find_bones(gltf: dict) -> dict[str, str]:
    """The four nodes `gestures.js` drives, by their real names in this file."""
    hints = BONE_HINTS
    by_normalised: dict[str, str] = {}
    for node in gltf.get("nodes", []):
        name = node.get("name")
        if name:
            by_normalised.setdefault(normalise(name), name)

    found: dict[str, str] = {}
    for key, candidates in hints.items():
        for candidate in candidates:
            if candidate in by_normalised:
                found[key] = by_normalised[candidate]
                break
    return found


def is_vrm(gltf: dict) -> str:
    extensions = gltf.get("extensions", {})
    if "VRMC_vrm" in extensions:
        return "VRM 1.0"
    if "VRM" in extensions:
        return "VRM 0.x"
    return ""


#: VRM human bone names that prove a limb. The spec fixes these, which is the
#: one place in this whole file where naming is not a problem: a VRM declares
#: its skeleton by ROLE, so `leftUpperArm` means the same thing in every file.
_VRM_EVIDENCE: dict[RigPart, tuple[str, ...]] = {
    RigPart.HEAD: ("head", "neck"),
    RigPart.TORSO: ("spine", "chest", "upperChest", "hips"),
    RigPart.ARMS: ("leftUpperArm", "rightUpperArm", "leftHand", "rightHand"),
    RigPart.LEGS: ("leftUpperLeg", "rightUpperLeg", "leftFoot", "rightFoot"),
}


def vrm_facts(gltf: dict) -> dict:
    """What a VRM declares about itself, in its own vocabulary.

    WHY THIS IS NOT OPTIONAL
        Judged as a plain glTF, a VRM looks broken: zero ARKit blendshapes, no
        `Head` bone, no arms. Every one of those conclusions is wrong. A VRM
        names its morph targets `Fcl_BRW_Angry`, names its bones by ROLE in an
        extension rather than by string, and expresses a face through a dozen
        named expressions instead of fifty-two dials.

        Reporting the glTF reading of a VRM would tell the user their model is
        useless and tell `catalog.py` that JARVIS has no arms — so he would
        never be offered `wave` on a model that has two.

    `avatar/js/body_vrm.js` does the runtime half of this, and reads the same
    two structures.
    """
    extensions = gltf.get("extensions", {})
    version = is_vrm(gltf)
    if not version:
        return {}

    bones: set[str] = set()
    expressions: list[str] = []

    if version == "VRM 1.0":
        core = extensions.get("VRMC_vrm", {})
        bones = set((core.get("humanoid", {}).get("humanBones") or {}).keys())
        presets = (core.get("expressions", {}).get("preset") or {})
        custom = (core.get("expressions", {}).get("custom") or {})
        expressions = sorted(presets.keys()) + sorted(custom.keys())
    else:
        core = extensions.get("VRM", {})
        for entry in (core.get("humanoid", {}).get("humanBones") or []):
            name = entry.get("bone")
            if name:
                bones.add(name)
        for group in (core.get("blendShapeMaster", {}).get("blendShapeGroups") or []):
            name = group.get("presetName") or group.get("name")
            if name and name.lower() != "unknown":
                expressions.append(name)

    parts = {RigPart.HEAD}
    for part, evidence in _VRM_EVIDENCE.items():
        if any(bone in bones for bone in evidence):
            parts.add(part)

    # Un VRM 1.0 peut porter le jeu ARKit en expressions personnalisees. Quand
    # c'est le cas, body_vrm.js prend le chemin haute-fidelite et tout le reste
    # de ce rapport devient secondaire.
    native_arkit = [name for name in ARKIT_52 if name in expressions]

    return {
        "version": version,
        "bones": sorted(bones),
        "expressions": expressions,
        "native_arkit": native_arkit,
        "parts": sorted(p.value for p in parts),
    }


#: Les visemes Oculus, tels que Ready Player Me et la plupart des exports
#: "game ready" les nomment. Detectes mais pas encore joues : `lipsync.js`
#: pilote les formes ARKit, ce qui marche partout. Un modele qui porte ces
#: quinze-la peut faire mieux — c'est une vraie amelioration a brancher, pas
#: une case a cocher pour faire joli, et le rapport le dit plutot que de le
#: laisser deviner.
OCULUS_VISEMES = (
    "viseme_sil", "viseme_PP", "viseme_FF", "viseme_TH", "viseme_DD",
    "viseme_kk", "viseme_CH", "viseme_SS", "viseme_nn", "viseme_RR",
    "viseme_aa", "viseme_E", "viseme_I", "viseme_O", "viseme_U",
)


def scorecard(data: dict) -> list[tuple[str, bool, str]]:
    """Ce modele fait-il l'affaire comme corps definitif de JARVIS ?

    POURQUOI UN BARÈME ET PAS UN RAPPORT DE PLUS
        Le rapport dit ce qu'il y a dedans. Il ne dit pas si c'est SUFFISANT, et
        c'est la seule question au moment de choisir. Un mesh superbe sans rig
        facial donne un JARVIS avec un corps et pas de visage ; un modele avec
        52 blendshapes mais sans jambes ne pourra jamais saluer. Ces deux
        verdicts sont evidents ici et invisibles dans une liste de morphs.

    Chaque ligne est (critere, satisfait, ce qu'on a trouve). Rien n'est
    bloquant : un « non » coute une capacite, jamais le chargement.
    """
    parts = set(data["parts"])
    vrm = data.get("vrm_facts") or {}
    morphs = [n for names in data["meshes"].values() for n in names]
    normalised = {normalise(n) for n in morphs}

    if vrm:
        expressions = len(vrm["expressions"])
        native = len(vrm["native_arkit"])
        face_ok = native >= 40 or expressions >= 12
        face_note = (f"{native}/52 ARKit natifs" if native
                     else f"{expressions} expressions VRM, traduites")
    else:
        matched = len(data["arkit_matched"])
        face_ok = matched >= 40
        face_note = f"{matched}/52 blendshapes ARKit"

    visemes = [v for v in OCULUS_VISEMES if normalise(v) in normalised]
    if vrm:
        vrm_visemes = [v for v in ("aa", "ih", "ou", "ee", "oh")
                       if v in vrm["expressions"]]
        visemes = visemes or vrm_visemes

    eyes = bool(data["bones"].get("eyeLeft") or data["bones"].get("eyeRight"))
    if not eyes:
        eyes = any(normalise(f"eyeLook{d}{s}") in normalised
                   for d in ("Up", "Down", "In", "Out") for s in ("Left", "Right"))

    return [
        ("corps entier",
         {"arms", "legs", "torso"} <= parts,
         ", ".join(sorted(parts)) or "rien"),
        ("rig facial",
         face_ok,
         face_note),
        ("visemes",
         bool(visemes),
         f"{len(visemes)} trouves" if visemes
         else "aucun — lip-sync approxime depuis les formes ARKit"),
        ("yeux pilotables",
         eyes,
         "os ou blendshapes de regard" if eyes else "le regard ne bougera que la tete"),
        ("animations embarquees",
         bool(data["animations"]),
         f"{len(data['animations'])} clip(s)" if data["animations"]
         else "aucune — installer un idle Mixamo, voir avatar/gestures/README.md"),
    ]


def print_scorecard(data: dict) -> bool:
    """Affiche le bareme. Rend True si le modele coche tout.

    Le verdict est nuance a dessein : « utilisable » n'est pas « definitif », et
    un modele a qui il manque seulement une animation d'attente est a deux
    minutes d'etre parfait — le dire evite de repartir en chercher un autre.
    """
    rows = scorecard(data)
    print("  Ce modele comme corps definitif de JARVIS\n")
    width = max(len(name) for name, _, _ in rows)
    for name, ok, note in rows:
        print(f"    [{'x' if ok else ' '}] {name.ljust(width)}   {note}")

    missing = [name for name, ok, _ in rows if not ok]
    print()
    if not missing:
        print("  Tout y est.\n")
        return True
    if missing == ["animations embarquees"]:
        print("  Utilisable tout de suite. Il ne lui manque qu'une animation")
        print("  d'attente — deux etapes, zero code : avatar/gestures/README.md\n")
        return False
    print(f"  Il manque : {', '.join(missing)}.")
    print("  Pour un corps definitif, chercher : humanoid · rigged · PBR ·")
    print("  52 ARKit blendshapes · visemes · GLB/GLTF/VRM · compatible Mixamo")
    print("  (`python -m presence.install_model --list`)\n")
    return False


def report(path: Path, aliases: dict[str, str] | None = None) -> dict:
    gltf = read_gltf(path)
    meshes = morph_names(gltf)
    every_morph = [n for names in meshes.values() for n in names]
    matched = match_arkit(every_morph, aliases)
    parts = detect_parts(gltf)
    bones = find_bones(gltf)
    animations = [a.get("name") or f"clip{i}"
                  for i, a in enumerate(gltf.get("animations", []))]

    vrm = vrm_facts(gltf)
    if vrm:
        # Le VRM sait mieux que la devinette par nom d'os : ses membres sont
        # declares, pas devines.
        parts = {RigPart(value) for value in vrm["parts"]}

    # Ce que le profil du modele porte — mesure ici, jamais declare. Voir
    # `presence/models.py`. Les memes questions que `avatar/js/profile.js`
    # pose au corps charge, pour que l'installation et le rendu disent la meme
    # chose du meme fichier.
    normalised = {normalise(n) for n in every_morph}
    visemes = [v for v in OCULUS_VISEMES if normalise(v) in normalised]
    if vrm and not visemes:
        visemes = [v for v in ("aa", "ih", "ou", "ee", "oh") if v in vrm["expressions"]]
    eyes_by = ("formes" if any(k.startswith("eyeLook") for k in matched)
               else "os" if (bones.get("eyeLeft") or bones.get("eyeRight"))
               else "VRMLookAt" if vrm else "aucun")
    used = gltf.get("extensionsUsed", [])
    compression = [label for ext, label in _COMPRESSION.items() if ext in used]
    fmt = (vrm["version"] if vrm else "glTF" if path.suffix.lower() == ".gltf" else "GLB")

    return {
        "file": path.name,
        "size_kb": path.stat().st_size // 1024,
        "vrm": is_vrm(gltf),
        "vrm_facts": vrm,
        "format": fmt,
        "compression": compression,
        "meshes": meshes,
        "morph_total": len(every_morph),
        "arkit_matched": matched,
        "arkit_missing": [n for n in ARKIT_52 if n not in matched],
        "unmatched_morphs": [n for n in every_morph
                             if n not in set(matched.values())],
        "visemes": visemes,
        "eyes": eyes_by,
        "head_bone": bool(bones.get("head") or bones.get("neck")),
        "parts": sorted(p.value for p in parts),
        "bones": bones,
        "node_names": [n.get("name") for n in gltf.get("nodes", []) if n.get("name")],
        "animations": animations,
        "nodes": len(gltf.get("nodes", [])),
    }


#: Les extensions de compression, telles que les nomme le profil.
_COMPRESSION = {
    "KHR_draco_mesh_compression": "DRACO",
    "EXT_meshopt_compression": "meshopt",
    "KHR_texture_basisu": "KTX2",
    "KHR_mesh_quantization": "quantization",
}


def _print(data: dict) -> None:
    print(f"\n  {data['file']}  —  {data['size_kb']} Ko"
          + (f"  [{data['vrm']}]" if data["vrm"] else ""))
    print(f"  {data['nodes']} noeuds, {len(data['meshes'])} maillage(s)\n")

    for mesh, names in data["meshes"].items():
        print(f"    {mesh}: {len(names)} morph target(s)")

    vrm = data.get("vrm_facts") or {}
    if vrm:
        native = vrm["native_arkit"]
        print(f"\n  {vrm['version']} : {len(vrm['expressions'])} expressions declarees")
        print(f"    {', '.join(vrm['expressions'][:12])}"
              + (" ..." if len(vrm["expressions"]) > 12 else ""))
        if native:
            print(f"    dont {len(native)} noms ARKit natifs — pleine fidelite")
        else:
            print("    aucun nom ARKit : body_vrm.js reduit les 52 formes vers")
            print("    ces expressions. Plus stylise, moins nuance, mais vivant.")
        print(f"    os humanoides declares : {len(vrm['bones'])}")

    matched = data["arkit_matched"]
    print(f"\n  ARKit reconnus directement : {len(matched)}/52")
    if matched:
        sample = list(matched.items())[:4]
        for arkit, real in sample:
            mark = "" if arkit == real else f"  (le modele l'appelle {real!r})"
            print(f"    {arkit}{mark}")
        if len(matched) > 4:
            print(f"    ... et {len(matched) - 4} autres")
    if data["arkit_missing"]:
        missing = data["arkit_missing"]
        print(f"  manquants ({len(missing)}) : {', '.join(missing[:8])}"
              + (" ..." if len(missing) > 8 else ""))
    if data["unmatched_morphs"]:
        extra = data["unmatched_morphs"]
        print(f"\n  morphs non ARKit ({len(extra)}) : {', '.join(extra[:10])}"
              + (" ..." if len(extra) > 10 else ""))
        print("    -> a mapper dans model.morphAliases si l'un d'eux est un ARKit deguise")

    print(f"\n  membres detectes : {', '.join(data['parts'])}")
    print("  os :")
    for key in ("root", "spine", "neck", "head", "eyeLeft", "eyeRight"):
        print(f"    {key:<9} {data['bones'].get(key, '— absent —')}")
    print()
    print_scorecard(data)


    print(f"\n  animations ({len(data['animations'])}) : "
          + (", ".join(data["animations"][:10]) or "aucune")
          + (" ..." if len(data["animations"]) > 10 else ""))
    print()


def write_manifest(path: Path, data: dict) -> None:
    """Fold the findings into `avatar/manifest.json`, through the model's profile.

    This used to overwrite the aliases and the bones in place and keep every
    other field — including the calibration of whatever model was there
    before, which then applied to this one. It now goes through
    `presence.models.install_file`: the file gets its own profile, the
    previous model's calibration is put away in ITS profile, and the
    preferences (camera, colours, `rig.motion`) are left alone.

    When the file is the one the manifest already describes, its current
    calibration is adopted rather than reset — that is what `--write` on the
    installed model has always meant.
    """
    from presence import models

    manifest = models.read_json(MANIFEST)
    current = ((manifest.get("model") or {}).get("file") or "").strip()
    root = BASE_DIR / "avatar" / "models"
    adopt = bool(current) and (root / current).resolve() == path.resolve()
    models.install_file(path, data, adopt=adopt)
    print(f"  avatar/manifest.json mis a jour depuis {path.name} "
          f"(profil {models.profile_path(path).name})\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in args
    args = [a for a in args if not a.startswith("--")]

    root = BASE_DIR / "avatar" / "models"
    if not args:
        # Recursif : les modeles vivent dans leurs dossiers (`jarvis/female/`).
        models = sorted(p for p in root.rglob("*")
                        if p.suffix.lower() in (".glb", ".gltf", ".vrm"))
        if not models:
            print("\n  aucun modele dans avatar/models/ — voir avatar/models/README.md\n")
            return 1
        args = [str(p) for p in models]

    for name in args:
        path = Path(name)
        if not path.is_absolute():
            # Relatif a la racine du depot, ou a `avatar/models/` — les deux
            # formes que la documentation et le manifeste emploient.
            path = BASE_DIR / path if (BASE_DIR / path).is_file() else root / path
        if not path.is_file():
            print(f"  introuvable : {path}")
            return 1
        data = report(path)
        _print(data)
        if write:
            write_manifest(path, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
