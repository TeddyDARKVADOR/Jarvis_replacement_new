"""
matthew/kaggle.py — entraîner Matthew dans le cloud (Kaggle, GPU gratuit), sans PC allumé.

    GitHub Actions (chaque soir, .github/workflows/matthew-cloud.yml)
        prepare   paquets du dépôt (matthew/packs) → jeu de données → plan d'entraînement
        launch    envoyé à Kaggle comme jeu de données PRIVÉ <user>/matthew-run, puis le noyau
                  PRIVÉ <user>/matthew-train lancé sur GPU (T4, fp16) ; on n'attend pas la fin.
                  Chaque nuit, même sans donnée neuve (le quota non utilisé est perdu) ; seul un
                  tournoi encore en cours fait attendre.
    Sur Kaggle, un TOURNOI (WRAPPER) : les variantes de VARIANTS, une par GPU en parallèle (2 × T4),
        tant que le budget de la nuit (kaggle_hours) le permet ; les kaggle_keep meilleures sur la
        perte d'évaluation gardent leur GGUF, tournament.json raconte tout.
    Le PC qui sert Matthew (sa nuit, la tâche « Matthew cloud » rattrapée à son réveil, ou
    `python -m matthew.kaggle pull`)
        pull      tournoi terminé et nouveau → GGUF téléchargés → pour chacun, du meilleur au suivant,
                  le MÊME duel que la nuit locale (examen, banc scellé, cognition, archive, retour
                  arrière : Flywheel._judge)

Ce qui part dans le cloud : seulement la donnée SYNTHÉTIQUE des paquets (demandes inventées par
des professeurs ouverts) et les déclarations d'outils. Le journal réel et les corrections ne
quittent jamais le PC : ils restent dans la boucle locale.

Accès (2026) : un jeton KGAT_… dans ~/.kaggle/access_token (ou KAGGLE_API_TOKEN), et le nom
d'utilisateur à part (KAGGLE_USERNAME, ou "kaggle_user" dans la section matthew) ; l'ancien
kaggle.json marche encore. Le compte doit être vérifié par téléphone (GPU) ; le noyau a besoin
d'Internet (Unsloth, modèle de base).

    python -m matthew.kaggle token        écrire le jeton correctement (saisie masquée, texte simple)
    python -m matthew.kaggle prepare DIR | launch [--force] | pull | status | install
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
#: Le tournoi de chaque nuit : des entraînements DIFFÉRENTS sur les mêmes données, pour que le quota
#: gratuit (~30 h de GPU par semaine) explore au lieu de refaire le même. Chaque variante ne change
#: que quelques réglages de train.HYPER ; « base » (les réglages du PC) ouvre toujours sur des
#: données neuves, pour qu'un candidat comparable au local existe. Arrêt précoce partout : une
#: époque de plus ne coûte que du temps, la meilleure est gardée.
VARIANTS = [
    {"label": "base", "hyper": {}},
    {"label": "ep1", "hyper": {"epochs": 1}},                                   # 3e cycle : la 1re était la meilleure
    {"label": "lr1e-4", "hyper": {"lr": 1e-4, "epochs": 3}},                    # plus lent, plus prudent
    {"label": "r32", "hyper": {"lora_r": 32, "lora_alpha": 32}},                # plus de capacité
    {"label": "r8", "hyper": {"lora_r": 8, "lora_alpha": 8}},                   # moins : moins de surapprentissage
    {"label": "lr3e-4", "hyper": {"lr": 3e-4, "epochs": 1}},                    # vite et fort
    {"label": "drop05", "hyper": {"lora_dropout": 0.05}},                       # régularisé
    {"label": "seed42", "hyper": {"seed": 42}},                                 # même recette, autre tirage
]
#: Kaggle coupe une session à 12 h : le budget d'une nuit reste en dessous.
MAX_BUDGET_H = 11.0


def variants_for(new_data: bool, day: int | None = None) -> list[dict]:
    """L'ordre de la nuit. Données neuves : « base » d'abord, puis les autres ; mêmes données :
    les autres seulement, en tournant d'une nuit à l'autre pour ne pas refaire les mêmes."""
    day = int(time.time() // 86400) if day is None else day
    rest = VARIANTS[1:]
    k = day % len(rest)
    rest = rest[k:] + rest[:k]
    return ([VARIANTS[0]] if new_data else []) + rest

WRAPPER = r'''"""Noyau Kaggle de Matthew — généré par matthew/kaggle.py. Un TOURNOI : plusieurs entraînements
aux réglages différents (plan.json → variants), un par GPU en parallèle, tant que le budget de la nuit
le permet ; les meilleurs sur la perte d'évaluation voyagent jusqu'au PC, qui les fait passer au duel."""
import json, os, shutil, subprocess, sys, threading, time
from pathlib import Path
T0 = time.time()
src = next(Path("/kaggle/input").rglob("plan.json")).parent        # le jeu de données matthew-run
out = Path("/kaggle/working/run")
work = Path("/tmp/matthew")                                         # hors de la sortie : 20 Go au plus
shutil.copytree(src, out, dirs_exist_ok=True)
(out / "data" / "train.jsonl").unlink(missing_ok=True)              # déjà dans le jeu de données
work.mkdir(parents=True, exist_ok=True)
PLAN = json.loads((src / "plan.json").read_text(encoding="utf-8"))
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "unsloth"], check=True)
# Le modèle de base une seule fois, avant que deux entraînements le téléchargent en même temps.
from huggingface_hub import snapshot_download
snapshot_download(PLAN["base_model"])
smi = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout
gpus = list(range(max(1, sum(1 for l in smi.splitlines() if l.startswith("GPU")))))
budget = float(PLAN.get("budget_s") or 4 * 3600)
queue = list(PLAN.get("variants") or [{"label": "base", "hyper": {}}])
print("MATTHEW_TOURNAMENT", json.dumps({"gpus": len(gpus), "budget_s": budget,
      "variants": [v["label"] for v in queue]}), flush=True)

def pump(proc, label, log):
    for line in proc.stdout:
        log.write(line)
        print(f"[{label}] {line}", end="", flush=True)

def start(v, gpu):
    d = work / v["label"]
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(src, d)
    p = dict(PLAN, hyper={**PLAN["hyper"], **v["hyper"]}, variant=v["label"])
    (d / "plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu), "PYTHONUNBUFFERED": "1",
           "MATTHEW_EXPORT_LOCK": str(work / "export.lock")}
    proc = subprocess.Popen([sys.executable, str(d / "train_matthew.py")], cwd=d, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    log = open(d / "train.log", "w", encoding="utf-8")
    th = threading.Thread(target=pump, args=(proc, v["label"], log), daemon=True)
    th.start()
    print(f"MATTHEW_VARIANT_START {v['label']} gpu={gpu} {json.dumps(v['hyper'])}", flush=True)
    return {"v": v, "gpu": gpu, "proc": proc, "dir": d, "t": time.time(), "log": log, "th": th}

def collect(r):
    r["th"].join(timeout=30)
    r["log"].close()
    d, v = r["dir"], r["v"]
    res = {}
    try:
        res = json.loads((d / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    q = [g for sub in ("gguf_gguf", "gguf") for g in sorted((d / sub).glob("*.gguf"))
         if not any(t in g.name.upper() for t in ("BF16", "F16", "F32")) and "mmproj" not in g.name.lower()]
    ok = r["proc"].returncode == 0 and bool(q) and bool(res)
    dest = out / "variants" / v["label"]
    if ok:
        (dest / "gguf_gguf").mkdir(parents=True, exist_ok=True)
        (dest / "data").mkdir(exist_ok=True)
        shutil.move(str(q[0]), dest / "gguf_gguf" / q[0].name)
        for f in ("plan.json", "result.json", "Modelfile"):
            shutil.copy2(d / f, dest / f)
        shutil.copy2(d / "data" / "eval.jsonl", dest / "data" / "eval.jsonl")
        if (d / "lora").is_dir():
            shutil.copytree(d / "lora", dest / "lora", dirs_exist_ok=True)
    tail = (d / "train.log").read_text(encoding="utf-8", errors="replace")[-1500:] if (d / "train.log").exists() else ""
    shutil.rmtree(d, ignore_errors=True)
    took = time.time() - r["t"]
    print(f"MATTHEW_VARIANT_DONE {v['label']} ok={ok} {took:.0f}s loss={res.get('best_eval_loss')}", flush=True)
    return {"label": v["label"], "hyper": v["hyper"], "ok": ok, "code": r["proc"].returncode, "seconds": round(took),
            "gpu": r["gpu"], "best_eval_loss": res.get("best_eval_loss"), "train_loss": res.get("train_loss"),
            **({} if ok else {"error": tail[-600:]})}

running, done, free = [], [], list(gpus)
while queue or running:
    while queue and free:
        left = budget - (time.time() - T0)
        longest = max([x["seconds"] for x in done if x["ok"]] or [0])
        # Première vague : un entraînement par GPU, sans mesure encore. Ensuite, seulement si le
        # plus long déjà vu tient dans le temps qui reste.
        if left <= 0 or (longest and left < 1.1 * longest):
            if not running:
                queue.clear()
            break
        running.append(start(queue.pop(0), free.pop(0)))
    time.sleep(20)
    for r in [r for r in running if r["proc"].poll() is not None]:
        running.remove(r)
        free.append(r["gpu"])
        done.append(collect(r))
# Les meilleurs (perte d'évaluation) gardent leur GGUF : un par duel, ~3 Go chacun.
ranked = sorted([x for x in done if x["ok"]], key=lambda x: (x["best_eval_loss"] is None, x["best_eval_loss"] or 0))
keep = {x["label"] for x in ranked[:int(PLAN.get("keep_gguf", 2))]}
for x in done:
    x["kept"] = x["label"] in keep
    if x["ok"] and not x["kept"]:
        shutil.rmtree(out / "variants" / x["label"] / "gguf_gguf", ignore_errors=True)
(out / "tournament.json").write_text(json.dumps({"gpus": len(gpus), "budget_s": budget,
    "elapsed_s": round(time.time() - T0), "variants": done}, ensure_ascii=False, indent=1), encoding="utf-8")
print("MATTHEW_KAGGLE_DONE", json.dumps([(x["label"], x["ok"], x["best_eval_loss"], x["kept"]) for x in done]))
sys.exit(0 if keep else 1)
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
    # Un secret GitHub collé avec son retour à la ligne donnait « nom\n/matthew-train ».
    u = str(os.environ.get("KAGGLE_USERNAME") or config().get("kaggle_user") or "").strip()
    if u:
        return u
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


def prepare(out: Path, pack_dir: Path | None = None, owner: str = "matthew", clock=time.time,
            variants: list[dict] | None = None) -> dict:
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
    plan["variants"] = variants if variants is not None else variants_for(True)
    plan["budget_s"] = round(min(float(cfg.get("kaggle_hours") or 4), MAX_BUDGET_H) * 3600)
    plan["keep_gguf"] = int(cfg.get("kaggle_keep") or 2)
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
            "imported": imp["added"], "packs": plan["cloud"]["packs"], "stamp": plan["cloud"]["stamp"],
            "variants": [v["label"] for v in plan["variants"]], "budget_h": round(plan["budget_s"] / 3600, 1)}


def _run(args: list[str], runner=subprocess.run, timeout: int = 1800) -> subprocess.CompletedProcess:
    return runner(cli() + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


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


def attached(owner: str, runner=subprocess.run) -> list[str] | None:
    """Les jeux de données vraiment rattachés au noyau (None : métadonnées illisibles)."""
    tmp = Path(tempfile.mkdtemp(prefix="matthew-kaggle-"))
    try:
        cp = _run(["kernels", "pull", f"{owner}/{KERNEL}", "-p", str(tmp), "-m"], runner, timeout=120)
        meta = tmp / "kernel-metadata.json"
        if cp.returncode != 0 or not meta.exists():
            return None
        return list(json.loads(meta.read_text(encoding="utf-8")).get("dataset_sources") or [])
    except (OSError, ValueError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def launch(force: bool = False, runner=subprocess.run, owner: str | None = None, out: Path | None = None,
           pack_dir: Path | None = None, sleep=time.sleep) -> dict:
    owner = owner or user()
    bad = check_token_file()
    if bad:
        raise KaggleError(bad)
    # Le quota gratuit non utilisé est perdu : chaque nuit s'entraîne, sur des données neuves
    # ou en explorant d'autres réglages. Seul un tournoi encore en cours fait attendre.
    st = status(owner, runner)
    if not force and st in ("queued", "running"):
        return {"ok": True, "skipped": f"un tournoi est encore en cours ({st})"}
    new_data = last_packs(owner, runner) != packs_hash(pack_dir)
    out = Path(out or tempfile.mkdtemp(prefix="matthew-kaggle-"))
    prep = prepare(out, pack_dir, owner, variants=variants_for(new_data))
    exists = _run(["datasets", "status", f"{owner}/{DATASET}"], runner).returncode == 0
    up = (["datasets", "version", "-p", prep["dataset"], "-m", f"données {prep['stamp']}", "--dir-mode", "zip"]
          if exists else ["datasets", "create", "-p", prep["dataset"], "--dir-mode", "zip"])
    cp = _run(up, runner)
    if cp.returncode != 0:
        raise KaggleError(f"envoi du jeu de données refusé : {(cp.stderr or cp.stdout)[-300:]}")
    # Kaggle traite la nouvelle version quelques instants avant qu'un noyau puisse la lire. Un jeu
    # de données tout juste créé n'existe pas encore pour `status` : pousser le noyau à ce moment
    # lui retire la source sans rien dire (2026-10-23 : StopIteration sur plan.json).
    for _ in range(30):
        st = _run(["datasets", "status", f"{owner}/{DATASET}"], runner)
        if "ready" in (st.stdout or "").lower():
            break
        sleep(10)
    else:
        raise KaggleError("le jeu de données n'est pas prêt après 5 min : noyau non lancé")
    cp = _run(["kernels", "push", "-p", prep["kernel"]], runner)
    if cp.returncode != 0:
        raise KaggleError(f"lancement du noyau refusé : {(cp.stderr or cp.stdout)[-300:]}")
    src = attached(owner, runner)
    if src is not None and f"{owner}/{DATASET}" not in src:
        raise KaggleError(f"noyau poussé sans son jeu de données (sources : {src}) : relancer")
    return {"ok": True, "launched": f"{owner}/{KERNEL}", "new_data": new_data,
            **{k: prep[k] for k in ("train", "eval", "packs", "stamp", "variants", "budget_h")}}


def status(owner: str | None = None, runner=subprocess.run) -> str:
    """queued | running | complete | error | cancel… | unknown"""
    cp = _run(["kernels", "status", f"{owner or user()}/{KERNEL}"], runner, timeout=120)
    m = re.search(r'status "?(?:KernelWorkerStatus\.)?([A-Za-z_]+)', (cp.stdout or "") + (cp.stderr or ""))
    return m.group(1).lower() if (m and cp.returncode == 0) else "unknown"


def pull(fw=None, runner=subprocess.run, owner: str | None = None, root: Path | None = None) -> dict:
    """Si le tournoi du cloud est fini et nouveau : télécharger ses meilleurs candidats, puis le duel
    de chacun, du meilleur sur la perte d'évaluation au suivant (le second doit battre le champion
    que le premier vient peut-être de devenir)."""
    root = Path(root or data_dir())
    bad = check_token_file()
    if bad:
        return {"ok": False, "reason": bad}
    st = status(owner, runner)
    if st != "complete":
        return {"ok": True, "status": st}
    # Sur le disque des données (D:), pas dans le dossier temporaire de C: : ~3 Go par candidat.
    tmp = root / "cloud-download"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        cp = _run(["kernels", "output", f"{owner or user()}/{KERNEL}", "-p", str(tmp)], runner, timeout=6 * 3600)
        if cp.returncode != 0:
            return {"ok": False, "status": st, "reason": (cp.stderr or cp.stdout)[-300:]}
        plans = sorted(tmp.rglob("plan.json"), key=lambda f: len(f.parts))
        if not plans:
            return {"ok": False, "status": st, "reason": "sortie sans plan.json (entraînement échoué ?)"}
        top = plans[0].parent
        plan = json.loads(plans[0].read_text(encoding="utf-8"))
        stamp = (plan.get("cloud") or {}).get("stamp", "")
        state_path = root / "cloud.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if stamp and state.get("pulled") == stamp:
            return {"ok": True, "status": st, "skipped": f"candidat {stamp} déjà jugé"}
        tour = _read_json(top / "tournament.json")
        if tour:
            ranked = sorted((v for v in tour.get("variants", []) if v.get("ok") and v.get("kept")),
                            key=lambda v: (v.get("best_eval_loss") is None, v.get("best_eval_loss") or 0))
            dirs = [(v["label"], top / "variants" / v["label"]) for v in ranked]
        elif (top / "result.json").exists():                        # noyau d'avant le tournoi : un seul
            dirs = [("", top)]
        else:
            return {"ok": False, "status": st, "reason": "aucun entraînement réussi dans le tournoi"}
        from matthew.flywheel import Flywheel
        fw = fw or Flywheel(root)
        outcomes = {}
        for label, d in dirs:
            run = root / "runs" / ("-".join(x for x in ("cloud", stamp or str(int(time.time())), label) if x))
            shutil.rmtree(run, ignore_errors=True)
            shutil.copytree(d, run)
            outcomes[label or "base"] = fw.cloud(run).get("outcome")
        state.update(pulled=stamp, outcome="nouveau champion" if "nouveau champion" in outcomes.values()
                     else next(iter(outcomes.values()), None), outcomes=outcomes, at=time.time(),
                     tournament=[{k: v.get(k) for k in ("label", "ok", "best_eval_loss", "seconds", "kept")}
                                 for v in (tour or {}).get("variants", [])])
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "status": st, "stamp": stamp, "outcome": state["outcome"], "outcomes": outcomes}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


#: Rattrapage : si le PC dormait à l'heure de la nuit, le duel se fait à son réveil.
TASK = "Matthew cloud"


def install(hour: str = "05:00", runner=subprocess.run, dry: bool = False) -> dict:
    """Tâche quotidienne qui ne fait QUE récupérer et juger le tournoi du cloud (pas d'entraînement
    local) : avec rattrapage — PC éteint ou en veille à l'heure dite, elle part dès son retour."""
    from matthew import sched
    d = data_dir()
    return sched.daily(TASK, hour, ["-m", "matthew.kaggle", "pull"], d / "cloud.log", d, runner=runner,
                       dry=dry, stem="cloud", catch_up=True)


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
    l.add_argument("--force", action="store_true", help="même si un tournoi tourne encore")
    sub.add_parser("pull", help="récupérer le candidat entraîné et lui faire passer le duel")
    sub.add_parser("status", help="état du noyau d'entraînement")
    sub.add_parser("token", help="enregistrer le jeton KGAT (saisie masquée) en texte simple")
    sub.add_parser("install", help="pose la tâche « Matthew cloud » (chaque jour 05 h, rattrapée au réveil du PC)")
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
        elif a.cmd == "install":
            out = install()
        else:
            out = {"status": status()}
    except KaggleError as e:
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
