"""
presence/install_model.py — put a real character in, in one command.

    python -m presence.install_model --readyplayerme 64bfa15f0e72c63d7c3934a6
    python -m presence.install_model https://example.com/aven.glb
    python -m presence.install_model C:/Downloads/james.vrm
    python -m presence.install_model --demo
    python -m presence.install_model --list

WHY A COMMAND AND NOT A PARAGRAPH IN A README
    Installing a model is four steps that are each easy to get wrong: fetch the
    file with the right query string, put it where the renderer looks, work out
    what its blendshapes are called, and write that into the manifest without
    clobbering the parts a human tuned. Done by hand, the usual outcome is a
    model that loads and never smiles, because the morph targets were named
    `browDown_L` and nothing said so.

    So it is one command, and the last two steps are `presence.inspect`, which
    reads the file itself rather than trusting anyone's description of it.

READY PLAYER ME, AND WHY IT IS THE DEFAULT SUGGESTION
    It is the only source that is free, needs no account to *download*, and
    hands back a full humanoid with the complete ARKit set and Oculus visemes
    in one glTF — which is precisely the combination this project needs and
    that most marketplace assets only have half of. The query string matters:
    without `morphTargets=ARKit`, the same avatar arrives with eight blendshapes
    instead of fifty-two.

    Make an avatar at readyplayer.me, copy the id out of the .glb URL it gives
    you, and pass it here.

WHAT THIS DELIBERATELY DOES NOT DO
    It does not download from a marketplace you have to be logged into, and it
    does not scrape. A paid asset — Aven, James, Inori, a MetaHuman export —
    gets downloaded by you, in a browser, and then installed from a local path
    with the third form above. The manifest work is identical either way.
"""
from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from presence import inspect as inspector  # noqa: E402

MODELS_DIR = BASE_DIR / "avatar" / "models"

#: What to ask Ready Player Me for. Every one of these changes what arrives.
#:
#:   morphTargets=ARKit,Oculus Visemes  52 facial shapes + 15 mouth shapes.
#:                                      Without it: 8 shapes and a dead face.
#:   textureAtlas=1024                  one texture instead of a dozen — fewer
#:                                      requests, and it matters on a phone.
#:   pose=A                             A-pose, which is what Mixamo retargets
#:                                      onto. T-pose needs a conversion step.
#:   lod=0                              full detail; 1 and 2 halve the polygons
#:                                      and are the right choice on a phone.
RPM_QUERY = (
    "morphTargets=ARKit,Oculus%20Visemes"
    "&textureAtlas=1024"
    "&pose=A"
    "&lod=0"
)

#: Sources worth knowing about, printed by `--list`. Kept here rather than in a
#: README because this is where someone stands when they need it.
SUGGESTIONS = [
    ("Ready Player Me", "readyplayer.me",
     "gratuit, corps entier, 52 ARKit + visemes Oculus, GLB direct",
     "python -m presence.install_model --readyplayerme <id>"),
    ("VRoid Studio", "vroid.com/en/studio",
     "gratuit, cree un VRM complet, style anime, blendshapes VRM",
     "exporter en .vrm puis installer depuis le chemin local"),
    ("Mixamo", "mixamo.com",
     "gratuit, corps riggés et surtout la banque d'animations pour les gestes",
     "voir avatar/gestures/README.md"),
    ("Fab (Aven, Inori)", "fab.com",
     "payant, 128 a 165 blendshapes dont 52 ARKit, tres expressif",
     "telecharger en GLB puis installer depuis le chemin local"),
    ("ArtStation (James, Casual Man)", "artstation.com/marketplace",
     "payant, rig facial dedie, 52 ARKit, lip-sync prepare",
     "telecharger en GLB puis installer depuis le chemin local"),
    ("MetaHuman", "metahuman.unrealengine.com",
     "gratuit mais Unreal — exporter en FBX puis convertir en GLB",
     "passe par Blender ou FBX2glTF"),
]

ALLOWED_SUFFIXES = {".glb", ".gltf", ".vrm"}

#: The model `--demo` installs.
#:
#: A photoscanned human head carrying the complete ARKit set, under the three
#: compressions real assets use (KTX2 textures, meshopt geometry). It is a
#: three.js sample, so it is small, freely fetchable and stable — and it proves
#: the whole chain end to end without an account anywhere.
#:
#: What it is NOT is a finished character: head only, and a deliberately flat
#: texture. It is the fixture this system is tested against, not the JARVIS
#: anyone should ship. `--list` says where the real ones are.
DEMO_URL = "https://threejs.org/examples/models/gltf/facecap.glb"


def _human(size: int) -> str:
    return f"{size / 1_048_576:.1f} Mo" if size > 1_048_576 else f"{size // 1024} Ko"


def fetch(url: str, target: Path) -> Path:
    """Download to a temporary name, then move. A half-written .glb left behind
    by an interrupted download is a file that looks installed and is not."""
    print(f"  telechargement  {url}")
    partial = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "JARVIS/presence"})
        with urllib.request.urlopen(request, timeout=120) as response, \
                partial.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"  echec HTTP {exc.code} : {exc.reason}")
    except (urllib.error.URLError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"  reseau indisponible : {exc}")

    # Un GLB commence par "glTF". Un .gltf est du JSON. Tout le reste est une
    # page d'erreur HTML renvoyee avec un code 200, ce que font plusieurs
    # marches quand l'asset demande n'existe pas.
    head = partial.open("rb").read(4)
    if target.suffix.lower() in (".glb", ".vrm") and head != b"glTF":
        partial.unlink(missing_ok=True)
        raise SystemExit(
            "  le fichier recu n'est pas un GLB (souvent une page d'erreur "
            "renvoyee en 200). Verifier l'identifiant ou l'URL."
        )

    partial.replace(target)
    print(f"  ecrit           avatar/models/{target.name}  ({_human(target.stat().st_size)})")
    return target


def install(source: str, *, rpm: bool = False, name: str | None = None) -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if rpm:
        avatar_id = source.strip().rstrip("/").split("/")[-1].split(".")[0].split("?")[0]
        if not avatar_id:
            raise SystemExit("  identifiant Ready Player Me vide")
        url = f"https://models.readyplayer.me/{avatar_id}.glb?{RPM_QUERY}"
        target = MODELS_DIR / f"{name or avatar_id}.glb"
        path = fetch(url, target)

    elif source.startswith(("http://", "https://")):
        stem = source.split("?")[0].rstrip("/").split("/")[-1] or "model.glb"
        suffix = Path(stem).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise SystemExit(f"  extension non geree : {suffix or 'aucune'} "
                             f"(attendu {', '.join(sorted(ALLOWED_SUFFIXES))})")
        path = fetch(source, MODELS_DIR / (name or stem))

    else:
        local = Path(source).expanduser()
        if not local.is_file():
            raise SystemExit(f"  fichier introuvable : {local}")
        if local.suffix.lower() not in ALLOWED_SUFFIXES:
            raise SystemExit(f"  extension non geree : {local.suffix}")
        path = MODELS_DIR / (name or local.name)
        if local.resolve() != path.resolve():
            shutil.copy2(local, path)
        print(f"  copie           avatar/models/{path.name}  ({_human(path.stat().st_size)})")

    # ── ce qui est reellement dedans, et le manifeste qui en decoule ────────
    try:
        data = inspector.report(path)
    except Exception as exc:
        raise SystemExit(f"  fichier illisible comme glTF : {exc}")

    inspector._print(data)

    matched = len(data["arkit_matched"])
    vrm = data.get("vrm_facts") or {}
    if vrm:
        # Un VRM sans nom ARKit n'est pas un modele casse : c'est un modele
        # qui parle une autre langue, et body_vrm.js la traduit. Crier
        # "aucun blendshape" enverrait chercher un probleme inexistant.
        if vrm["native_arkit"]:
            print(f"  VRM portant {len(vrm['native_arkit'])} expressions ARKit"
                  " natives — pleine fidelite." + chr(10))
        else:
            print(f"  VRM : {len(vrm['expressions'])} expressions propres, alimentees")
            print("  depuis les 52 formes ARKit par avatar/js/body_vrm.js.")
            print("  Le visage marchera, en plus stylise et moins nuance." + chr(10))
    elif matched == 0:
        print("  AUCUN blendshape ARKit reconnu.")
        print("  Le corps bougera la tete mais le visage restera fige.")
        print("  -> relire la liste des morphs ci-dessus : si l'un d'eux est un")
        print("     ARKit sous un autre nom, l'ajouter a model.morphAliases.\n")
    elif matched < 30:
        print(f"  seulement {matched}/52 blendshapes ARKit — les expressions seront")
        print("  approximatives. Un modele avec le jeu complet vaut mieux.\n")

    inspector.write_manifest(path, data)

    frame = "face" if data["parts"] == ["head"] else "bust"
    manifest = json.loads(inspector.MANIFEST.read_text(encoding="utf-8"))
    manifest.setdefault("camera", {})["frame"] = frame
    manifest["camera"].setdefault("fov", 24)
    inspector.MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"  cadrage         {frame}")
    print("\n  Verifier maintenant :")
    print("      python -m presence.selftest")
    print("      avatar/lab.html          (charger, inspecter, tester au curseur)")
    print()
    return 0


def print_sources() -> int:
    print("\n  Ou trouver un personnage exploitable\n")
    for label, site, what, how in SUGGESTIONS:
        print(f"    {label}")
        print(f"      {site}")
        print(f"      {what}")
        print(f"      {how}\n")
    print("  Ce qu'il faut chercher, quel que soit le site :")
    print("      humanoid · rigged · PBR · 52 ARKit blendshapes · visemes ·")
    print("      GLB/GLTF/VRM · compatible Mixamo\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "--list":
        return print_sources()

    name = None
    if "--name" in args:
        index = args.index("--name")
        name = args[index + 1] if index + 1 < len(args) else None
        del args[index:index + 2]

    if args[0] == "--demo":
        return install(DEMO_URL, name="facecap.glb")

    if args[0] in ("--readyplayerme", "--rpm"):
        if len(args) < 2:
            raise SystemExit("  usage : --readyplayerme <identifiant>")
        return install(args[1], rpm=True, name=name)

    return install(args[0], name=name)


if __name__ == "__main__":
    raise SystemExit(main())
