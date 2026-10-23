"""
matthew/kaggle.py — entraîner Matthew dans le cloud (Kaggle, GPU gratuit), sans PC allumé.

    GitHub Actions (chaque soir, .github/workflows/matthew-cloud.yml)
        prepare   paquets du dépôt (matthew/packs) → jeu de données → plan d'entraînement
        launch    envoyé à Kaggle comme jeu de données PRIVÉ <user>/matthew-run, puis le noyau
                  PRIVÉ <user>/matthew-train lancé sur GPU (T4, fp16) ; on n'attend pas la fin
    Le PC qui sert Matthew (sa nuit, ou `python -m matthew.kaggle pull`)
        pull      noyau terminé et candidat nouveau → GGUF téléchargé → le MÊME duel que la nuit
                  locale (examen, banc scellé, cognition, archive, retour arrière : Flywheel._judge)

Ce qui part dans le cloud : seulement la donnée SYNTHÉTIQUE des paquets (demandes inventées par
des professeurs ouverts) et les déclarations d'outils. Le journal réel et les corrections ne
quittent jamais le PC : ils restent dans la boucle locale.

Accès (2026) : un jeton KGAT_… dans ~/.kaggle/access_token (ou KAGGLE_API_TOKEN), et le nom
d'utilisateur à part (KAGGLE_USERNAME, ou "kaggle_user" dans la section matthew) ; l'ancien
kaggle.json marche encore. Le compte doit être vérifié par téléphone (GPU) ; le noyau a besoin
d'Internet (Unsloth, modèle de base).

    python -m matthew.kaggle token        écrire le jeton correctement (saisie masquée, texte simple)
    python -m matthew.kaggle prepare DIR | launch [--force] | pull | status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import REPO, config, data_dir

DATASET = "matthew-run"
KERNEL = "matthew-train"
#: Ce que le noyau garde dans sa sortie (le reste — points de contrôle, GGUF 16 bits — est effacé).
KEEP = ("plan.json", "result.json", "Modelfile", "data/eval.jsonl")

WRAPPER = r'''"""Noyau Kaggle de Matthew — généré par matthew/kaggle.py. Entraîne, exporte, fait le ménage."""
import shutil, subprocess, sys
from pathlib import Path
src = next(Path("/kaggle/input").rglob("plan.json")).parent        # le jeu de données matthew-run
run = Path("/kaggle/working/run")
shutil.copytree(src, run, dirs_exist_ok=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "unsloth"], check=True)
code = subprocess.run([sys.executable, str(run / "train_matthew.py")], cwd=run).returncode
shutil.rmtree(run / "ckpt", ignore_errors=True)                       # points de contrôle : inutiles ici
for g in run.glob("gguf*/*.gguf"):                                    # seul le GGUF quantifié voyage
    if any(t in g.name.upper() for t in ("BF16", "F16", "F32")) or "mmproj" in g.name.lower():
        g.unlink()
for s in run.glob("gguf/*.safetensors"):                              # le modèle fusionné 16 bits (~8,7 Go)
    s.unlink()
print("MATTHEW_KAGGLE_DONE", code)
sys.exit(code)
'''


class KaggleError(RuntimeError):
    pass


def cli() -> list[str]:
    exe = shutil.which("kaggle")
    return [exe] if exe else [sys.executable, "-m", "kaggle"]


def token_file() -> Path:
    return Path.home() / ".kaggle" / "access_token"


def has_token() -> bool:
    """Un jeton KGAT (2026 : ~/.kaggle/access_token ou KAGGLE_API_TOKEN), ou l'ancien kaggle.json."""
    return bool(os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_KEY")
                or token_file().exists() or (Path.home() / ".kaggle" / "kaggle.json").exists())


def check_token_file() -> str | None:
    """Le piège de Windows PowerShell 5.1 : `echo … > fichier` écrit en UTF-16 (FF FE), que l'outil
    Kaggle ne lit pas. Rend une explication, ou None si le fichier est absent ou correct."""
    try:
        raw = token_file().read_bytes()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw:
        return ("~/.kaggle/access_token est en UTF-16 (écrit par `echo … >` de PowerShell) : "
                "réécris-le en texte simple, voir `python -m matthew.kaggle token`")
    if not raw.strip().startswith(b"KGAT_"):
        return "~/.kaggle/access_token ne commence pas par KGAT_ : est-ce bien le jeton de l'API Kaggle ?"
    return None


def user() -> str:
    """Le nom d'utilisateur Kaggle (il nomme <user>/matthew-run et <user>/matthew-train). Le jeton
    KGAT ne le contient pas : KAGGLE_USERNAME, ou "kaggle_user" dans la section matthew."""
    u = os.environ.get("KAGGLE_USERNAME") or config().get("kaggle_user")
    if u:
        return str(u)
    try:
        return json.loads((Path.home() / ".kaggle" / "kaggle.json").read_text(encoding="utf-8"))["username"]
    except (OSError, ValueError, KeyError):
        raise KaggleError("nom d'utilisateur Kaggle inconnu : KAGGLE_USERNAME, ou \"kaggle_user\" dans la "
                          "section matthew de config/api_keys.json")


def packs_hash(pack_dir: Path | None = None) -> str:
    from matthew.pack import PACKS
    h = hashlib.sha256()
    for f in sorted(Path(pack_dir or PACKS).glob("*")):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def prepare(out: Path, pack_dir: Path | None = None, owner: str = "matthew", clock=time.time) -> dict:
    """Le jeu de données Kaggle (out/dataset) et le noyau (out/kernel), sans rien envoyer."""
    from matthew import dataset as D
    from matthew import pack as P
    from matthew import train as T
    from matthew.journal import Journal
    out = Path(out)
    pack_dir = Path(pack_dir or P.PACKS)
    work = Path(tempfile.mkdtemp(prefix="matthew-cloud-"))
    tools = pack_dir / "tools.json"
    if not tools.exists():
        raise KaggleError("matthew/packs/tools.json manque : `python -m matthew.pack export` sur un PC")
    shutil.copy2(tools, work / "tools.json")
    imp = P.import_all(work, pack_dir)
    cfg = {**config({}), "dedup": False}
    man = D.build(Journal(work), cfg=cfg, embed=None, clock=clock)
    run = T.plan(Path(man["dir"]), cfg, root=work, clock=clock)
    ds = out / "dataset"
    shutil.rmtree(ds, ignore_errors=True)
    (ds / "data").mkdir(parents=True)
    for f in ("train.jsonl", "eval.jsonl", "manifest.json"):
        shutil.copy2(Path(man["dir"]) / f, ds / "data" / f)
    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    plan.update(train="data/train.jsonl", eval="data/eval.jsonl", cloud={"packs": packs_hash(pack_dir),
                "stamp": time.strftime("%Y%m%d-%H%M%S", time.localtime(clock())), "examples": man["train"] + man["eval"]})
    plan["command"] = "python train_matthew.py"
    (ds / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in ("train_matthew.py", "Modelfile"):
        shutil.copy2(run / f, ds / f)
    (ds / "dataset-metadata.json").write_text(json.dumps(
        {"title": DATASET, "id": f"{owner}/{DATASET}", "licenses": [{"name": "other"}]}, indent=1), encoding="utf-8")
    ker = out / "kernel"
    shutil.rmtree(ker, ignore_errors=True)
    ker.mkdir(parents=True)
    (ker / "run_kaggle.py").write_text(WRAPPER, encoding="utf-8")
    (ker / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{owner}/{KERNEL}", "title": KERNEL, "code_file": "run_kaggle.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
        "machine_shape": "NvidiaTeslaT4", "dataset_sources": [f"{owner}/{DATASET}"],
        "competition_sources": [], "kernel_sources": []}, indent=1), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return {"dataset": str(ds), "kernel": str(ker), "train": man["train"], "eval": man["eval"],
            "imported": imp["added"], "packs": plan["cloud"]["packs"], "stamp": plan["cloud"]["stamp"]}


def _run(args: list[str], runner=subprocess.run, timeout: int = 1800) -> subprocess.CompletedProcess:
    return runner(cli() + args, capture_output=True, text=True, timeout=timeout)


def last_packs(owner: str, runner=subprocess.run) -> str | None:
    """L'empreinte des paquets du dernier entraînement envoyé (plan.json du jeu de données)."""
    tmp = Path(tempfile.mkdtemp(prefix="matthew-kaggle-"))
    try:
        cp = _run(["datasets", "download", f"{owner}/{DATASET}", "-f", "plan.json", "-p", str(tmp), "--unzip"], runner)
        if cp.returncode != 0:
            return None
        found = next(tmp.rglob("plan.json*"), None)
        if found and found.suffix == ".zip":
            import zipfile
            with zipfile.ZipFile(found) as z:
                return json.loads(z.read("plan.json")).get("cloud", {}).get("packs")
        return json.loads(found.read_text(encoding="utf-8")).get("cloud", {}).get("packs") if found else None
    except (OSError, ValueError, KeyError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def launch(force: bool = False, runner=subprocess.run, owner: str | None = None, out: Path | None = None,
           pack_dir: Path | None = None) -> dict:
    owner = owner or user()
    bad = check_token_file()
    if bad:
        raise KaggleError(bad)
    if not force and last_packs(owner, runner) == packs_hash(pack_dir):
        return {"ok": True, "skipped": "aucune donnée nouvelle depuis le dernier entraînement"}
    out = Path(out or tempfile.mkdtemp(prefix="matthew-kaggle-"))
    prep = prepare(out, pack_dir, owner)
    exists = _run(["datasets", "status", f"{owner}/{DATASET}"], runner).returncode == 0
    up = (["datasets", "version", "-p", prep["dataset"], "-m", f"données {prep['stamp']}", "--dir-mode", "zip"]
          if exists else ["datasets", "create", "-p", prep["dataset"], "--dir-mode", "zip"])
    cp = _run(up, runner)
    if cp.returncode != 0:
        raise KaggleError(f"envoi du jeu de données refusé : {(cp.stderr or cp.stdout)[-300:]}")
    # Kaggle traite la nouvelle version quelques instants avant qu'un noyau puisse la lire.
    for _ in range(30):
        st = _run(["datasets", "status", f"{owner}/{DATASET}"], runner)
        if "ready" in (st.stdout or "").lower() or st.returncode != 0:
            break
        time.sleep(10)
    cp = _run(["kernels", "push", "-p", prep["kernel"]], runner)
    if cp.returncode != 0:
        raise KaggleError(f"lancement du noyau refusé : {(cp.stderr or cp.stdout)[-300:]}")
    return {"ok": True, "launched": f"{owner}/{KERNEL}", **{k: prep[k] for k in ("train", "eval", "packs", "stamp")}}


def status(owner: str | None = None, runner=subprocess.run) -> str:
    """queued | running | complete | error | cancel… | unknown"""
    cp = _run(["kernels", "status", f"{owner or user()}/{KERNEL}"], runner, timeout=120)
    m = re.search(r'status "?([A-Za-z_]+)', (cp.stdout or "") + (cp.stderr or ""))
    return m.group(1).lower() if (m and cp.returncode == 0) else "unknown"


def pull(fw=None, runner=subprocess.run, owner: str | None = None, root: Path | None = None) -> dict:
    """Si un candidat entraîné dans le cloud est prêt et nouveau : le télécharger, puis le duel."""
    root = Path(root or data_dir())
    bad = check_token_file()
    if bad:
        return {"ok": False, "reason": bad}
    st = status(owner, runner)
    if st != "complete":
        return {"ok": True, "status": st}
    tmp = Path(tempfile.mkdtemp(prefix="matthew-kaggle-out-"))
    cp = _run(["kernels", "output", f"{owner or user()}/{KERNEL}", "-p", str(tmp)], runner, timeout=6 * 3600)
    if cp.returncode != 0:
        return {"ok": False, "status": st, "reason": (cp.stderr or cp.stdout)[-300:]}
    plan_file = next(tmp.rglob("plan.json"), None)
    result = next(tmp.rglob("result.json"), None)
    if not plan_file or not result:
        return {"ok": False, "status": st, "reason": "sortie sans plan.json ou result.json (entraînement échoué ?)"}
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    stamp = (plan.get("cloud") or {}).get("stamp", "")
    state_path = root / "cloud.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if stamp and state.get("pulled") == stamp:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": True, "status": st, "skipped": f"candidat {stamp} déjà jugé"}
    run = root / "runs" / f"cloud-{stamp or int(time.time())}"
    shutil.rmtree(run, ignore_errors=True)
    shutil.copytree(plan_file.parent, run)
    shutil.rmtree(tmp, ignore_errors=True)
    from matthew.flywheel import Flywheel
    fw = fw or Flywheel(root)
    rep = fw.cloud(run)
    state.update(pulled=stamp, outcome=rep.get("outcome"), at=time.time())
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "status": st, "stamp": stamp, "outcome": rep.get("outcome")}


def write_token(token: str, path: Path | None = None) -> Path:
    """Texte simple (ASCII, sans BOM, sans fin de ligne), lisible par le seul utilisateur."""
    token = token.strip()
    if not re.fullmatch(r"KGAT_[A-Za-z0-9]{16,}", token):
        raise KaggleError("un jeton Kaggle commence par KGAT_ suivi de lettres et de chiffres")
    path = Path(path or token_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(token.encode("ascii"))
    try:
        if sys.platform == "win32":        # le dossier du profil l'est déjà ; on retire l'héritage par prudence
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:(R,W)"],
                           capture_output=True, timeout=30)
        else:
            path.chmod(0o600)
    except (OSError, subprocess.SubprocessError):
        pass
    return path


def save_token() -> int:
    import getpass
    try:
        path = write_token(getpass.getpass("Jeton Kaggle (KGAT_…, rien ne s'affiche) : "))
    except KaggleError as e:
        print(e)
        return 1
    print(f"Jeton enregistré : {path} (texte simple, accès réservé à ton compte)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.kaggle")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="construire le jeu de données et le noyau, sans rien envoyer")
    p.add_argument("out")
    p.add_argument("--owner", default="matthew")
    l = sub.add_parser("launch", help="envoyer et lancer l'entraînement sur Kaggle")
    l.add_argument("--force", action="store_true", help="même si les paquets n'ont pas changé")
    sub.add_parser("pull", help="récupérer le candidat entraîné et lui faire passer le duel")
    sub.add_parser("status", help="état du noyau d'entraînement")
    sub.add_parser("token", help="enregistrer le jeton KGAT (saisie masquée) en texte simple")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "token":
            return save_token()
        if a.cmd == "prepare":
            out = prepare(Path(a.out), owner=a.owner)
        elif a.cmd == "launch":
            out = launch(force=a.force)
        elif a.cmd == "pull":
            out = pull()
        else:
            out = {"status": status()}
    except KaggleError as e:
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
