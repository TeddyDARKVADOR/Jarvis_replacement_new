"""
presence/install_model.py — put a real character in, in one command.

    python -m presence.install_model --readyplayerme 64bfa15f0e72c63d7c3934a6
    python -m presence.install_model https://example.com/aven.glb
    python -m presence.install_model C:/Downloads/james.vrm --slot jarvis/male \
        --license "CC-BY 4.0" --source "https://..." --name "JARVIS Male"
    python -m presence.install_model --demo
    python -m presence.install_model --list          ou en trouver un
    python -m presence.install_model --installed     ce qui est deja la
    python -m presence.install_model --use jarvis/female
    python -m presence.install_model --adopt jarvis/female/jarvis.glb

CHANGER DE VISAGE, SANS TOUCHER AU CERVEAU
    Chaque modele vit dans son dossier (`--slot jarvis/male`) avec un profil a
    cote de lui — empreinte, licence, provenance, capacites mesurees et
    calibration. Installer ou `--use` active un modele : sa calibration entre
    dans le manifeste, celle de l'ancien est rangee dans SON profil, et les
    preferences (camera, couleurs, `rig.motion`) ne bougent pas. Rien dans
    `presence/` ni dans le moteur n'a a changer : voir `presence/models.py`.

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
from presence import models  # noqa: E402

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


def install(source: str, *, rpm: bool = False, name: str | None = None,
            slot: str | None = None, license: str | None = None,
            provenance: str | None = None, label: str | None = None,
            version: str | None = None, activate: bool = True,
            adopt: bool = False) -> int:
    """Mettre un fichier dans `avatar/models/`, le mesurer, le profiler, l'activer.

    `adopt` : le fichier est deja en place et le manifeste actuel le decrit —
    on lui ecrit son profil avec la calibration qu'il a deja, sans rien copier.
    """
    target_dir = MODELS_DIR / slot if slot else MODELS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if adopt:
        path = models.find(source) or Path(source)
        if not path.is_absolute() and not path.is_file():
            path = MODELS_DIR / source
        if not path.is_file():
            raise SystemExit(f"  fichier introuvable : {source}")
        print(f"  adopte          avatar/models/{models.relative(path)}")

    elif rpm:
        avatar_id = source.strip().rstrip("/").split("/")[-1].split(".")[0].split("?")[0]
        if not avatar_id:
            raise SystemExit("  identifiant Ready Player Me vide")
        url = f"https://models.readyplayer.me/{avatar_id}.glb?{RPM_QUERY}"
        path = fetch(url, target_dir / f"{name or avatar_id}.glb")
        provenance = provenance or f"Ready Player Me {avatar_id} ({url})"

    elif source.startswith(("http://", "https://")):
        stem = source.split("?")[0].rstrip("/").split("/")[-1] or "model.glb"
        suffix = Path(stem).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise SystemExit(f"  extension non geree : {suffix or 'aucune'} "
                             f"(attendu {', '.join(sorted(ALLOWED_SUFFIXES))})")

        # L'erreur la plus courante, et la plus silencieuse : coller l'URL d'un
        # avatar Ready Player Me telle quelle. Sans `morphTargets=ARKit`, le
        # meme avatar arrive avec huit blendshapes au lieu de cinquante-deux —
        # il se charge, il s'affiche, et son visage ne bouge pas.
        if "readyplayer.me" in source and "morphTargets" not in source:
            joiner = "&" if "?" in source else "?"
            source = f"{source}{joiner}{RPM_QUERY}"
            print("  complete       morphTargets=ARKit ajoute a l'URL "
                  "(sans lui : 8 blendshapes au lieu de 52)")

        path = fetch(source, target_dir / (name or stem))
        provenance = provenance or source

    else:
        local = Path(source).expanduser()
        if not local.is_file():
            raise SystemExit(f"  fichier introuvable : {local}")
        if local.suffix.lower() not in ALLOWED_SUFFIXES:
            raise SystemExit(f"  extension non geree : {local.suffix}")
        path = target_dir / (name or local.name)
        if local.resolve() != path.resolve():
            shutil.copy2(local, path)
        print(f"  copie           avatar/models/{models.relative(path)}  ({_human(path.stat().st_size)})")
        provenance = provenance or f"fichier local {local.name}"

    # ── ce qui est reellement dedans ────────────────────────────────────────
    # Les alias de forme notes pour CE fichier (reinstallation, adoption)
    # participent a la lecture : un alias manuel qui trouve encore sa cible
    # ne doit pas disparaitre parce qu'on a relu le fichier.
    previous = models.read_profile(path)
    manifest = models.read_json(inspector.MANIFEST)
    if adopt:
        aliases = (manifest.get("model") or {}).get("morphAliases")
    else:
        aliases = (previous.get("calibration") or {}).get("morphAliases")
    try:
        data = inspector.report(path, aliases)
    except Exception as exc:
        raise SystemExit(f"  fichier illisible comme glTF : {exc}")

    inspector._print(data)

    matched = len(data["arkit_matched"])
    vrm = data.get("vrm_facts") or {}
    if vrm:
        # Un VRM sans nom ARKit n'est pas un modele casse : c'est un modele
        # qui parle une autre langue, et body_vrm.js la traduit.
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

    profile, manifest = models.install_file(
        path, data, adopt=adopt, license=license, source=provenance,
        name=label, version=version, activate_now=activate)

    print(f"  profil          avatar/models/{models.relative(models.profile_path(path))}")
    print(f"                  {profile['id']} v{profile['version']} · licence : {profile['license']}")
    print(f"                  empreinte {profile['sha256'][:16]}…")
    if activate:
        print(f"  actif           {manifest['model']['file']}  "
              f"(cadrage {manifest['camera'].get('frame')}, motion "
              f"{manifest['rig'].get('motion', 'full')})")
    else:
        print("  installe sans l'activer — `--use` pour le porter")
    print("\n  Verifier maintenant :")
    print("      python -m presence.selftest")
    print("      avatar/lab.html          (charger, inspecter, tester au curseur)")
    print()
    return 0


def list_installed() -> int:
    manifest = models.read_json(inspector.MANIFEST)
    active = ((manifest.get("model") or {}).get("file") or "").strip()
    rows = models.installed()
    if not rows:
        print("\n  aucun modele profile — `--adopt <fichier>` pour un modele deja en place\n")
        return 0
    print()
    for model, profile in rows:
        mark = "*" if models.relative(model) == active else " "
        caps = profile.get("capabilities", {})
        ok, why = models.check_profile(model)
        print(f"  {mark} {profile['id']:<28} v{profile.get('version', '?'):<10} "
              f"{caps.get('arkit', 0)}/52 ARKit · visemes {len(caps.get('visemes', []))} · "
              f"{'/'.join(caps.get('limbs', []))} · {profile.get('license')}")
        if not ok:
            print(f"      ! {why}")
    print("\n  * = actif\n")
    return 0


def use(target: str) -> int:
    model = models.find(target)
    if model is None:
        raise SystemExit(f"  aucun modele installe ne correspond a {target!r} — voir --installed")
    ok, why = models.check_profile(model)
    if not ok:
        print(f"  attention : {why}")
    profile, manifest = models.use(model)
    print(f"  actif           {manifest['model']['file']}  ({profile['id']} v{profile.get('version')})")
    print("  calibration du modele precedent rangee dans son profil.\n")
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
    if args[0] == "--installed":
        return list_installed()

    options: dict[str, str | None] = {}
    for flag in ("--name", "--slot", "--license", "--source", "--label", "--version"):
        if flag in args:
            index = args.index(flag)
            options[flag] = args[index + 1] if index + 1 < len(args) else None
            del args[index:index + 2]
    activate = "--no-activate" not in args
    args = [a for a in args if a != "--no-activate"]
    common = dict(name=options.get("--name"), slot=options.get("--slot"),
                  license=options.get("--license"), provenance=options.get("--source"),
                  label=options.get("--label"), version=options.get("--version"),
                  activate=activate)

    if args[0] == "--use":
        if len(args) < 2:
            raise SystemExit("  usage : --use <dossier, identifiant ou fichier>")
        return use(args[1])

    if args[0] == "--adopt":
        if len(args) < 2:
            raise SystemExit("  usage : --adopt <fichier deja dans avatar/models/>")
        return install(args[1], adopt=True, **common)

    if args[0] == "--demo":
        common["name"] = common["name"] or "facecap.glb"
        common["slot"] = common["slot"] or "test"
        common["provenance"] = common["provenance"] or DEMO_URL
        return install(DEMO_URL, **common)

    if args[0] in ("--readyplayerme", "--rpm"):
        if len(args) < 2:
            raise SystemExit("  usage : --readyplayerme <identifiant>")
        return install(args[1], rpm=True, **common)

    return install(args[0], **common)


if __name__ == "__main__":
    raise SystemExit(main())
