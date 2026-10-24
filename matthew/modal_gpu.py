"""
matthew/modal_gpu.py — entraîner ET juger Matthew sur un GPU Modal, sans PC allumé la nuit.

    GitHub Actions (chaque soir, .github/workflows/matthew-modal.yml)
        launch    paquets du dépôt → jeu de données (le même que Kaggle : kaggle.prepare) envoyé
                  au volume Modal, puis une variante par carte (L4), lancées détachées.
    Sur Modal, chaque variante (`variant`) :
        entraîne (train_matthew.py), sert le GGUF ET le champion dans un Ollama du conteneur, et
        joue le MÊME duel que le PC (flywheel.duel : examen apparié, banc scellé) → verdict.json.
    Le PC, quand il est allumé (tâche « Matthew modal », rattrapée à l'ouverture de session)
        pull      lit les verdicts ; ne télécharge QUE les gagnants (~3 Go) ; Flywheel.cloud
                  garde le verdict s'il a été joué contre le champion d'ici, puis promeut, archive,
                  vérifie la copie servie (retour arrière) et repasse les spécialités.
        seed      une fois : envoie le GGUF du champion actuel au volume (le duel en a besoin).

Ce qui part chez Modal : la donnée SYNTHÉTIQUE des paquets et les déclarations d'outils, comme
pour Kaggle. Le journal réel et les corrections restent sur le PC (entraînement local de jour).

Budget : chaque variante écrit sa durée dans le volume (usage/<mois>/) ; `launch` refuse au-delà
de modal_usd_month (estimé à modal_usd_hour). Le crédit offert ne se reporte pas : une soirée sans
donnée neuve explore d'autres réglages (kaggle.variants_for), dans le plafond.

Accès : `pip install modal` puis `modal token new` (PC) ; secrets MODAL_TOKEN_ID / MODAL_TOKEN_SECRET
(GitHub Actions).

    python -m matthew.modal_gpu launch [--force] | pull | seed | status | install
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import REPO, config, data_dir

APP = "matthew-lab"
VOLUME = "matthew-lab"
MOUNT = "/vol"
#: Ce qui revient d'une variante : de quoi la servir et la juger, rien de plus (pas les points de contrôle).
KEEP = ("plan.json", "result.json", "Modelfile", "verdict.json", "train.log", "data/eval.jsonl")


class ModalError(RuntimeError):
    pass


# ── Le volume : une interface, deux réalisations (Modal, ou un dossier pour les essais) ──────────

class DirStore:
    """Le volume vu comme un dossier : le conteneur Modal (monté sur /vol) et le selftest."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _p(self, path: str) -> Path:
        return self.root / path.strip("/")

    def listdir(self, path: str) -> list[str]:
        p = self._p(path)
        return sorted(x.name for x in p.iterdir()) if p.is_dir() else []

    def read(self, path: str) -> bytes | None:
        try:
            return self._p(path).read_bytes()
        except OSError:
            return None

    def get(self, path: str, dest: Path) -> None:
        shutil.copy2(self._p(path), dest)

    def put_bytes(self, path: str, data: bytes) -> None:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, local: Path, path: str) -> None:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, p)

    def put_dir(self, local: Path, path: str) -> None:
        shutil.copytree(local, self._p(path), dirs_exist_ok=True)

    def remove(self, path: str) -> None:
        p = self._p(path)
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


class ModalStore:
    """Le volume Modal depuis le PC ou GitHub Actions (SDK modal, jeton dans ~/.modal.toml ou l'env)."""

    def __init__(self, vol=None):
        if vol is None:
            import modal
            vol = modal.Volume.from_name(VOLUME, create_if_missing=True)
        self.vol = vol

    def listdir(self, path: str) -> list[str]:
        try:
            return sorted(Path(e.path).name for e in self.vol.listdir(path))
        except Exception:                       # dossier absent : FileNotFoundError ou erreur gRPC
            return []

    def read(self, path: str) -> bytes | None:
        try:
            return b"".join(self.vol.read_file(path))
        except Exception:
            return None

    def get(self, path: str, dest: Path) -> None:
        with open(dest, "wb") as f:
            for chunk in self.vol.read_file(path):
                f.write(chunk)

    def put_bytes(self, path: str, data: bytes) -> None:
        import io
        with self.vol.batch_upload(force=True) as b:
            b.put_file(io.BytesIO(data), path)

    def put_file(self, local: Path, path: str) -> None:
        with self.vol.batch_upload(force=True) as b:
            b.put_file(str(local), path)

    def put_dir(self, local: Path, path: str) -> None:
        with self.vol.batch_upload(force=True) as b:
            b.put_directory(str(local), path)

    def remove(self, path: str) -> None:
        try:
            self.vol.remove_file(path, recursive=True)
        except Exception:
            pass


def _json(store, path: str, default=None):
    raw = store.read(path)
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def _put_json(store, path: str, data) -> None:
    store.put_bytes(path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))


# ── Budget ───────────────────────────────────────────────────────────────────────────────────────

def month(clock=time.time) -> str:
    return time.strftime("%Y-%m", time.gmtime(clock()))


def spent(store, cfg: dict, clock=time.time) -> dict:
    """Les secondes de GPU du mois (une fiche par variante : jamais deux écrivains sur un fichier)."""
    m = month(clock)
    secs = sum((_json(store, f"usage/{m}/{f}", {}) or {}).get("seconds", 0) for f in store.listdir(f"usage/{m}"))
    usd = secs / 3600 * float(cfg["modal_usd_hour"])
    return {"month": m, "gpu_hours": round(secs / 3600, 2), "usd": round(usd, 2),
            "cap_usd": float(cfg["modal_usd_month"]), "left_usd": round(float(cfg["modal_usd_month"]) - usd, 2)}


# ── Lancer (GitHub Actions ou PC) ───────────────────────────────────────────────────────────────

def running(store, clock=time.time, stale_h: float = 4.0) -> list[str]:
    """Les variantes lancées sans verdict ni échec depuis moins de `stale_h` heures."""
    out = []
    for stamp in store.listdir("runs"):
        st = _json(store, f"runs/{stamp}/launch.json", {}) or {}
        if clock() - st.get("at", 0) > stale_h * 3600:
            continue
        for label in st.get("variants", []):
            names = store.listdir(f"runs/{stamp}/{label}")
            if "verdict.json" not in names and "error.json" not in names:
                out.append(f"{stamp}/{label}")
    return out


def launch(force: bool = False, store=None, spawn=None, pack_dir: Path | None = None, clock=time.time,
           cfg: dict | None = None) -> dict:
    """Préparer le jeu de données, l'envoyer au volume, lancer les variantes (sans attendre).
    `spawn(stamp, variant)` : par défaut les fonctions Modal détachées ; un faux dans le selftest."""
    from matthew import kaggle as K
    cfg = cfg or config()
    store = store or ModalStore()
    busy = running(store, clock)
    if busy and not force:
        return {"ok": True, "skipped": f"variantes encore en cours : {', '.join(busy)}"}
    money = spent(store, cfg, clock)
    if money["left_usd"] <= 0:
        return {"ok": True, "skipped": f"plafond du mois atteint ({money['usd']} $ sur {money['cap_usd']} $)", "budget": money}
    packs = K.packs_hash(pack_dir)
    state = _json(store, "state.json", {}) or {}
    new_data = state.get("packs") != packs
    n = max(1, int(cfg["modal_variants"]))
    variants = K.variants_for(new_data)[:n]
    work = Path(tempfile.mkdtemp(prefix="matthew-modal-"))
    try:
        prep = K.prepare(work, pack_dir, owner="modal", clock=clock, variants=variants)
        stamp = prep["stamp"]
        ds = Path(prep["dataset"])
        (ds / "dataset-metadata.json").unlink(missing_ok=True)
        from matthew.pack import PACKS
        shutil.copy2(Path(pack_dir or PACKS) / "tools.json", ds / "tools.json")
        store.put_dir(ds, f"inbox/{stamp}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # Lancer d'abord, noter ensuite : un lancement refusé (2026-10-24 : décorateur Modal) ne doit pas
    # laisser des variantes « en cours » qui bloquent les soirs suivants.
    (spawn or _modal_spawn)(stamp, variants)
    _put_json(store, f"runs/{stamp}/launch.json", {"at": clock(), "variants": [v["label"] for v in variants],
                                                   "packs": packs, "new_data": new_data})
    _put_json(store, "state.json", {**state, "packs": packs, "last_launch": stamp})
    return {"ok": True, "stamp": stamp, "new_data": new_data, "variants": [v["label"] for v in variants],
            "train": prep["train"], "eval": prep["eval"], "budget": money,
            "champion": (_json(store, "champion/current.json", {}) or {}).get("since")}


def _modal_spawn(stamp: str, variants: list[dict]) -> None:
    from matthew import modal_app as MA            # le SDK modal n'est requis qu'ici
    with MA.app.run(detach=True):
        for v in variants:
            MA.variant.spawn(stamp, v)


# ── Sur Modal : une variante = entraînement + duel ──────────────────────────────────────────────

def run_variant(stamp: str, v: dict, vol_root: Path = Path(MOUNT), work: Path = Path("/tmp/matthew"),
                runner=subprocess.run, serve=None, client_factory=None, clock=time.time, commit=lambda: None,
                cfg: dict | None = None, cases: list[dict] | None = None) -> dict:
    """Le corps de la fonction Modal (testable hors de Modal : `serve` et `client_factory` simulés)."""
    from matthew import bench as B
    from matthew import evaluate as E
    from matthew import train as T
    from matthew.flywheel import SERVE_EXTRA, duel
    from matthew.client import Client
    store = DirStore(vol_root)
    cfg = cfg or config({})
    label, t0 = v["label"], clock()
    d = work / label
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(vol_root / "inbox" / stamp, d)
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    plan.update(hyper={**plan["hyper"], **v.get("hyper", {})}, variant=label)
    (d / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    dest = f"runs/{stamp}/{label}"
    gpu = cfg["modal_gpu"]

    def fail(why: str, log: str = "") -> dict:
        _put_json(store, f"{dest}/error.json", {"why": why, "log": log[-2000:], "gpu": gpu})
        _usage(store, stamp, label, clock() - t0, clock)
        commit()
        return {"ok": False, "label": label, "why": why}

    with open(d / "train.log", "w", encoding="utf-8") as log:
        cp = runner([sys.executable, "train_matthew.py"], cwd=d, stdout=log, stderr=subprocess.STDOUT, text=True)
    if cp.returncode != 0 or T.finish(d) is None:
        return fail(f"entraînement : code {cp.returncode}" if cp.returncode else "aucun GGUF quantifié",
                    (d / "train.log").read_text(encoding="utf-8", errors="replace"))
    # Servir candidat et champion dans l'Ollama du conteneur, puis le jury du PC, à l'identique.
    (serve or _serve_ollama)()
    res = T.ollama_create(d, "matthew-candidate", runner=runner)
    if not res["ok"]:
        return fail(f"ollama create : {res['reason']}")
    champ = _json(store, "champion/current.json", None)
    if champ:
        res = T.ollama_create(vol_root / champ["dir"], "matthew", runner=runner)
        if not res["ok"]:
            return fail(f"champion : {res['reason']}")
    make = client_factory or (lambda m: Client(cfg["endpoint"], m, purpose="matthew", extra=SERVE_EXTRA))
    decls = json.loads((d / "tools.json").read_text(encoding="utf-8"))["tools"]
    v_ = duel(make("matthew-candidate"), make("matthew") if champ else None,
              E.load(d / "data" / "eval.jsonl"), B.load() if cases is None else cases, decls, cfg)
    q = T.quantized_gguf(d)
    verdict = {**v_, "champion_since": (champ or {}).get("since"), "gpu": gpu, "label": label,
               "hyper": plan["hyper"], "seconds": round(clock() - t0), "stamp": stamp,
               "gguf": q.relative_to(d).as_posix() if v_["ok"] else None}
    (d / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in KEEP:
        if (d / f).exists():
            store.put_file(d / f, f"{dest}/{f}")
    if verdict["gguf"]:                                 # seul un gagnant garde son GGUF (~3 Go)
        store.put_file(q, f"{dest}/{verdict['gguf']}")
    _usage(store, stamp, label, clock() - t0, clock)
    commit()
    return {"ok": True, "label": label, "promote": verdict["ok"], "why": verdict["why"]}


def _usage(store, stamp: str, label: str, seconds: float, clock=time.time) -> None:
    _put_json(store, f"usage/{month(clock)}/{stamp}-{label}.json", {"seconds": round(seconds)})


def _serve_ollama() -> None:
    import urllib.request
    env = {**os.environ, "OLLAMA_MODELS": "/tmp/ollama-models"}
    subprocess.Popen(["ollama", "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
            return
        except OSError:
            time.sleep(1)
    raise ModalError("Ollama ne démarre pas dans le conteneur")


# ── Le PC : rapatrier les gagnants, synchroniser le champion ────────────────────────────────────

def pull(fw=None, store=None, root: Path | None = None) -> dict:
    """Pour chaque lancement pas encore lu : les verdicts ; le meilleur gagnant (plus grand écart
    au champion) est téléchargé et passe par Flywheel.cloud, qui garde le verdict s'il a été joué
    contre le champion d'ici. Les perdants ne coûtent aucun téléchargement."""
    root = Path(root or data_dir())
    store = store or ModalStore()
    state_path = root / "modal.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    seen = set(state.get("pulled", []))
    report = {}
    for stamp in store.listdir("runs"):
        if stamp in seen:
            continue
        launched = _json(store, f"runs/{stamp}/launch.json", {}) or {}
        labels = launched.get("variants", [])
        verdicts = {l: _json(store, f"runs/{stamp}/{l}/verdict.json") for l in labels}
        errors = {l: _json(store, f"runs/{stamp}/{l}/error.json") for l in labels}
        pending = [l for l in labels if not verdicts[l] and not errors[l]]
        if pending and time.time() - launched.get("at", 0) < 4 * 3600:
            report[stamp] = {"status": "en cours", "pending": pending}
            continue
        winners = sorted((l for l, v in verdicts.items() if v and v.get("ok")),
                         key=lambda l: -(verdicts[l]["candidate"].get("composite") or 0))
        outcome = {"verdicts": {l: (v or {}).get("why") for l, v in verdicts.items()},
                   "errors": {l: e["why"] for l, e in errors.items() if e}}
        if winners:
            label = winners[0]
            run = root / "runs" / f"modal-{stamp}-{label}"
            _download(store, f"runs/{stamp}/{label}", run, verdicts[label]["gguf"])
            from matthew.flywheel import Flywheel
            fw = fw or Flywheel(root)
            outcome["outcome"] = fw.cloud(run).get("outcome")
            outcome["label"] = label
        else:
            outcome["outcome"] = "aucun candidat ne bat le champion"
        report[stamp] = outcome
        seen.add(stamp)
    state.update(pulled=sorted(seen), at=time.time(), last=report)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    report["champion"] = sync_champion(store, root)
    # Les gagnants qui ne sont pas devenus champion (duel rejoué ici et perdu, ou battus par un
    # meilleur) ne gardent pas leurs ~3 Go sur le volume.
    keep = (_json(store, "champion/current.json", {}) or {}).get("dir")
    for stamp, out in report.items():
        if isinstance(out, dict) and out.get("label") and f"runs/{stamp}/{out['label']}" != keep:
            _prune(store, keep=keep or "", old=f"runs/{stamp}/{out['label']}")
    return report


def _download(store, remote: str, local: Path, gguf: str) -> None:
    """Les fichiers connus d'une variante (KEEP) et son GGUF, sur le disque des données (D:)."""
    shutil.rmtree(local, ignore_errors=True)
    for f in (*KEEP, gguf):
        if store.read(f"{remote}/{f}") is None and f != gguf:
            continue
        (local / f).parent.mkdir(parents=True, exist_ok=True)
        store.get(f"{remote}/{f}", local / f)


def sync_champion(store, root: Path | None = None) -> dict:
    """Le volume doit servir le champion d'ici (promotion, retour arrière ou entraînement local de
    jour l'ont peut-être changé). Gagnant venu de Modal : son GGUF y est déjà. Sinon on l'envoie."""
    from matthew import train as T
    root = Path(root or data_dir())
    try:
        champ = json.loads((root / "champion.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "reason": "pas de champion ici"}
    since = champ.get("since")
    cur = _json(store, "champion/current.json", {}) or {}
    if cur.get("since") == since:
        return {"ok": True, "since": since, "action": "déjà à jour"}
    run = Path(champ.get("run") or "")
    old = cur.get("dir")
    name = run.name.split("-")
    if run.name.startswith("modal-") and len(name) >= 4:          # modal-<jour>-<heure>-<variante>
        remote = f"runs/{name[1]}-{name[2]}/{'-'.join(name[3:])}"
        if _json(store, f"{remote}/verdict.json"):
            _put_json(store, "champion/current.json", {"since": since, "dir": remote, "at": time.time()})
            _prune(store, keep=remote, old=old)
            return {"ok": True, "since": since, "action": "déjà sur le volume", "dir": remote}
    q = T.quantized_gguf(run) if run.is_dir() else None
    if q is None:
        return {"ok": False, "reason": f"GGUF du champion introuvable dans {run}"}
    tmp = Path(tempfile.mkdtemp(prefix="matthew-champion-"))
    try:
        mf = (run / "Modelfile").read_text(encoding="utf-8")
        import re
        (tmp / "Modelfile").write_text(re.sub(r"(?m)^FROM \S+", f"FROM ./{q.name}", mf), encoding="utf-8")
        store.put_file(tmp / "Modelfile", f"champion/{since}/Modelfile")
        store.put_file(q, f"champion/{since}/{q.name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _put_json(store, "champion/current.json", {"since": since, "dir": f"champion/{since}", "at": time.time()})
    _prune(store, keep=f"champion/{since}", old=old)
    return {"ok": True, "since": since, "action": "envoyé", "mb": round(q.stat().st_size / 1e6)}


def _prune(store, keep: str, old: str | None) -> None:
    """Un seul GGUF de champion sur le volume : l'ancien part (gagnant d'hier ou copie envoyée)."""
    if not old or old == keep:
        return
    if old.startswith("champion/"):
        store.remove(old)
    else:
        v = _json(store, f"{old}/verdict.json", {}) or {}
        if v.get("gguf"):
            store.remove(f"{old}/{v['gguf']}")


def status(store=None, cfg: dict | None = None) -> dict:
    store = store or ModalStore()
    cfg = cfg or config()
    return {"budget": spent(store, cfg), "running": running(store),
            "champion": (_json(store, "champion/current.json", {}) or {}).get("since"),
            "last_launch": (_json(store, "state.json", {}) or {}).get("last_launch")}


TASK = "Matthew modal"


def install(runner=subprocess.run, dry: bool = False) -> dict:
    """À l'ouverture de session (le PC n'est plus allumé la nuit) : rapatrier et juger."""
    from matthew import sched
    d = data_dir()
    return sched.at_login(TASK, ["-m", "matthew.modal_gpu", "pull"], d / "modal.log", d, runner=runner,
                          dry=dry, stem="modal")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.modal_gpu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("launch", help="envoyer les données et lancer les variantes sur Modal")
    l.add_argument("--force", action="store_true", help="même si des variantes tournent encore")
    sub.add_parser("pull", help="lire les verdicts, rapatrier et promouvoir le meilleur gagnant")
    sub.add_parser("seed", help="envoyer le champion actuel au volume (une fois, ~3 Go)")
    sub.add_parser("status", help="budget du mois, variantes en cours, champion du volume")
    sub.add_parser("install", help="pose la tâche « Matthew modal » (à l'ouverture de session)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "launch":
            out = launch(force=a.force)
        elif a.cmd == "pull":
            out = pull()
        elif a.cmd == "seed":
            out = sync_champion(ModalStore())
        elif a.cmd == "install":
            out = install()
        else:
            out = status()
    except (ModalError, ImportError) as e:
        print(json.dumps({"ok": False, "reason": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
