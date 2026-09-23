"""Des modeles glTF fabriques a la main, pour tester ce qu'on n'a pas en fichier.

POURQUOI
    Les capacites optionnelles d'un modele — des yeux, une tete, des visemes,
    un squelette, des textures — ne se testent pas sur le seul modele installe :
    il les a toutes. Un modele sans blendshapes, un autre aux noms exotiques,
    un VRM, un humanoide sans os de tete : ce sont eux qui prouvent que le moteur
    se degrade au lieu de mourir. Et le futur visage masculin n'existe pas
    encore ; ce qui se prepare pour lui se teste sur un modele qui lui ressemble.

    Aucune dependance : un GLB est un en-tete de 12 octets, un bloc JSON et un
    bloc binaire. Chaque modele ici est un triangle — ce qui compte, c'est ce
    que le fichier DECLARE (noms de formes, noeuds, extensions), exactement ce
    que `presence/inspect.py` et `body_gltf.js` lisent.
"""
from __future__ import annotations

import json
import struct

ARKIT_52 = (
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
    "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
)

OCULUS = ("sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U")

HUMANOID = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftEye", "RightEye",
            "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
            "RightShoulder", "RightArm", "RightForeArm", "RightHand",
            "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot"]

#: Le parent de chaque os, pour faire un vrai arbre.
PARENT = {"Spine": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Neck": "Spine2",
          "Head": "Neck", "LeftEye": "Head", "RightEye": "Head",
          "LeftShoulder": "Spine2", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm",
          "LeftHand": "LeftForeArm", "RightShoulder": "Spine2", "RightArm": "RightShoulder",
          "RightForeArm": "RightArm", "RightHand": "RightForeArm",
          "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg",
          "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg"}


def arkit_as(style: str) -> list[str]:
    """Les 52 noms, dans une convention d'export reelle."""
    out = []
    for name in ARKIT_52:
        side = ""
        base = name
        for word, mark in (("Left", "_L"), ("Right", "_R")):
            if name.endswith(word):
                base, side = name[: -len(word)], mark
        if style == "arkit":
            out.append(name)
        elif style == "underscore":          # browDown_L — facecap
            out.append(base + side)
        elif style == "snake":               # brow_down_l
            snake = "".join("_" + c.lower() if c.isupper() else c for c in base)
            out.append(snake + side.lower())
        elif style == "prefixed":            # Wolf3D_Head.browDownLeft — Ready Player Me
            out.append("Wolf3D_Head." + name)
        else:
            raise ValueError(style)
    return out


def glb(*, morphs: list[str] | None = None, bones: list[str] | None = None,
        animations: list[str] | None = None, texture_uri: str | None = None,
        extensions: dict | None = None, extensions_used: list[str] | None = None,
        name: str = "synthetique") -> bytes:
    """Un GLB minimal : un triangle, ses formes nommees, un arbre d'os."""
    morphs = list(morphs or [])
    bones = list(bones or [])

    binary = bytearray()

    def blob(values: list[float]) -> int:
        offset = len(binary)
        binary.extend(struct.pack(f"<{len(values)}f", *values))
        return offset

    buffer_views, accessors = [], []

    def accessor(values: list[float], count: int, kind: str, minmax: bool = False) -> int:
        offset = blob(values)
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(values) * 4})
        acc = {"bufferView": len(buffer_views) - 1, "componentType": 5126,
               "count": count, "type": kind}
        if minmax:
            xs, ys, zs = values[0::3], values[1::3], values[2::3]
            acc["min"] = [min(xs), min(ys), min(zs)]
            acc["max"] = [max(xs), max(ys), max(zs)]
        accessors.append(acc)
        return len(accessors) - 1

    # Un triangle a hauteur de visage : le cadrage calcule une vraie boite.
    positions = accessor([-0.1, 1.5, 0.0, 0.1, 1.5, 0.0, 0.0, 1.7, 0.0], 3, "VEC3", minmax=True)
    targets = []
    for i, _ in enumerate(morphs):
        delta = [0.0] * 9
        delta[(i % 3) * 3 + 2] = 0.01
        targets.append({"POSITION": accessor(delta, 3, "VEC3", minmax=True)})

    primitive = {"attributes": {"POSITION": positions}, "mode": 4}
    gltf: dict = {"asset": {"version": "2.0", "generator": "avatar/checks/fixtures.py"},
                  "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": []}
    if targets:
        primitive["targets"] = targets
    if texture_uri:
        gltf["images"] = [{"uri": texture_uri}]
        gltf["textures"] = [{"source": 0}]
        gltf["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        primitive["material"] = 0
    mesh = {"name": f"{name}_mesh", "primitives": [primitive]}
    if morphs:
        mesh["extras"] = {"targetNames": morphs}
        mesh["weights"] = [0.0] * len(morphs)
    gltf["meshes"].append(mesh)

    index = {}
    for bone in bones:
        index[bone] = len(gltf["nodes"])
        gltf["nodes"].append({"name": bone})
    for bone in bones:
        parent = PARENT.get(bone.split(":")[-1].replace("mixamorig", ""))
        parent = next((b for b in bones if b.split(":")[-1] == parent), None)
        if parent is not None:
            gltf["nodes"][index[parent]].setdefault("children", []).append(index[bone])
    roots = [index[b] for b in bones if not any(index[b] in n.get("children", [])
                                               for n in gltf["nodes"])]
    mesh_node = len(gltf["nodes"])
    gltf["nodes"].append({"name": f"{name}_face", "mesh": 0})
    gltf["scenes"][0]["nodes"] = roots + [mesh_node]

    for anim in animations or []:
        times = accessor([0.0, 1.0], 2, "SCALAR", minmax=False)
        accessors[times]["min"], accessors[times]["max"] = [0.0], [1.0]
        values = accessor([0.0] * 6, 2, "VEC3")
        gltf.setdefault("animations", []).append({
            "name": anim,
            "samplers": [{"input": times, "output": values, "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0, "target": {"node": mesh_node, "path": "translation"}}],
        })

    if extensions:
        gltf["extensions"] = extensions
    if extensions_used:
        gltf["extensionsUsed"] = extensions_used

    gltf["accessors"] = accessors
    gltf["bufferViews"] = buffer_views
    while len(binary) % 4:
        binary.append(0)
    gltf["buffers"] = [{"byteLength": len(binary)}]
    return _pack(gltf, bytes(binary))


def vrm1(*, expressions: tuple[str, ...] = ("happy", "sad", "angry", "surprised", "relaxed",
                                              "aa", "ih", "ou", "ee", "oh", "blink",
                                              "blinkLeft", "blinkRight"),
         legs: bool = True) -> bytes:
    """Un VRM 1.0 minimal : squelette humanoide declare par ROLE, expressions."""
    bones = [b for b in HUMANOID if legs or not any(k in b for k in ("UpLeg", "Leg", "Foot"))]
    data = json.loads(_unpack_json(glb(morphs=list(expressions), bones=bones, name="vrm")))
    role = {"Hips": "hips", "Spine": "spine", "Spine1": "chest", "Neck": "neck", "Head": "head",
            "LeftEye": "leftEye", "RightEye": "rightEye",
            "LeftArm": "leftUpperArm", "LeftForeArm": "leftLowerArm", "LeftHand": "leftHand",
            "RightArm": "rightUpperArm", "RightForeArm": "rightLowerArm", "RightHand": "rightHand",
            "LeftUpLeg": "leftUpperLeg", "LeftLeg": "leftLowerLeg", "LeftFoot": "leftFoot",
            "RightUpLeg": "rightUpperLeg", "RightLeg": "rightLowerLeg", "RightFoot": "rightFoot"}
    names = [n["name"] for n in data["nodes"]]
    human = {role[b]: {"node": names.index(b)} for b in bones if b in role}
    mesh_node = names.index("vrm_face")
    preset = {}
    custom = {}
    for i, name in enumerate(expressions):
        entry = {"morphTargetBinds": [{"node": mesh_node, "index": i, "weight": 1.0}]}
        (preset if name in ("happy", "angry", "sad", "relaxed", "surprised", "aa", "ih", "ou",
                            "ee", "oh", "blink", "blinkLeft", "blinkRight") else custom)[name] = entry
    data["extensionsUsed"] = ["VRMC_vrm"]
    data["extensions"] = {"VRMC_vrm": {
        "specVersion": "1.0",
        "meta": {"name": "synthetique", "authors": ["tests"], "licenseUrl": "https://vrm.dev/licenses/1.0/"},
        "humanoid": {"humanBones": human},
        "expressions": {"preset": preset, "custom": custom},
        "lookAt": {"type": "bone", "offsetFromHeadBone": [0, 0.06, 0]},
    }}
    raw = glb(morphs=list(expressions), bones=bones, name="vrm")
    return _pack(data, _unpack_bin(raw))


def _pack(gltf: dict, binary: bytes) -> bytes:
    text = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(text) % 4:
        text += b" "
    total = 12 + 8 + len(text) + 8 + len(binary)
    return (struct.pack("<III", 0x46546C67, 2, total)
            + struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def _unpack_json(raw: bytes) -> bytes:
    length = struct.unpack("<I", raw[12:16])[0]
    return raw[20:20 + length]


def _unpack_bin(raw: bytes) -> bytes:
    length = struct.unpack("<I", raw[12:16])[0]
    start = 20 + length
    size = struct.unpack("<I", raw[start:start + 4])[0]
    return raw[start + 8:start + 8 + size]


#: Les cas de robustesse, par nom. Chacun dit ce que le profil DOIT rapporter.
CASES = {
    "sans_blendshapes":  (lambda: glb(bones=HUMANOID),
                          {"arkit": 0, "eyes": True, "head": True, "limbs": "arms,head,legs,torso"}),
    "partiel":           (lambda: glb(morphs=["jawOpen", "mouthSmileLeft", "mouthSmileRight",
                                              "browInnerUp", "eyeBlinkLeft", "eyeBlinkRight"],
                                      bones=["Head", "Neck"]),
                          {"arkit": 6, "eyes": False, "head": True}),
    "noms_underscore":   (lambda: glb(morphs=arkit_as("underscore"), bones=["Head", "Neck"]),
                          {"arkit": 52, "eyes": True}),
    "noms_snake":        (lambda: glb(morphs=arkit_as("snake"), bones=["Head", "Neck"]),
                          {"arkit": 52}),
    "noms_prefixes_rpm": (lambda: glb(morphs=arkit_as("prefixed") + [f"viseme_{v}" for v in OCULUS],
                                      bones=HUMANOID),
                          {"arkit": 52, "visemes": "oculus"}),
    "sans_yeux":         (lambda: glb(morphs=[m for m in ARKIT_52 if not m.startswith("eyeLook")],
                                      bones=[b for b in HUMANOID if "Eye" not in b]),
                          {"arkit": 44, "eyes": False}),
    "sans_tete_humanoide": (lambda: glb(morphs=list(ARKIT_52),
                                        bones=[b for b in HUMANOID if b not in ("Head", "Neck",
                                                                               "LeftEye", "RightEye")]),
                            {"arkit": 52, "head": False}),
    "tete_seule_sans_os": (lambda: glb(morphs=list(ARKIT_52)),
                           {"arkit": 52, "head": True, "limbs": "head"}),
    "squelette_mixamo":  (lambda: glb(morphs=list(ARKIT_52),
                                      bones=[f"mixamorig:{b}" for b in HUMANOID]),
                          {"arkit": 52, "head": True, "limbs": "arms,head,legs,torso"}),
    "texture_manquante": (lambda: glb(morphs=list(ARKIT_52), bones=["Head", "Neck"],
                                      texture_uri="introuvable.png"),
                          {"arkit": 52}),
    "avec_animations":   (lambda: glb(morphs=list(ARKIT_52), bones=HUMANOID,
                                      animations=["idle", "wave"]),
                          {"arkit": 52, "animations": 2}),
    "vrm_1":             (lambda: vrm1(),
                          {"format": "VRM 1.0", "visemes": "vrm", "limbs": "arms,head,legs,torso"}),
    # Non conforme : la specification VRM exige des jambes. three-vrm refuse le
    # fichier en bloc ; le moteur doit le relire en glTF simple, pas le perdre.
    "vrm_buste":         (lambda: vrm1(legs=False),
                          {"format": "GLB (VRM refuse)", "head": True}),
}
