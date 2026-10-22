"""
matthew/pack.py — emporter la donnée vérifiée d'un PC à l'autre.

memory/matthew/ est ignoré par Git (le journal réel ne quitte jamais la machine). Mais la
donnée SYNTHÉTIQUE — demandes inventées par un professeur ouvert, vérifiées par un second
modèle — peut voyager : une session faite sur un portable sans GPU (matthew/session.py) doit
nourrir l'entraînement du PC à la RTX 3060. Le paquet passe par le dépôt :

    python -m matthew.pack export     memory/matthew/synthetic → matthew/packs/<jour>-<poste>.jsonl.gz
    python -m matthew.pack import     matthew/packs/*.jsonl.gz → memory/matthew/synthetic (sans doublon)
    python -m matthew.pack status

Ce qui sort : seulement les exemples synthétiques vérifiés (status ok, synthetic, licence
ouverte du professeur ET du vérificateur), passés à scrub() sans effet ; jamais un épisode
du journal réel. La boucle de nuit importe les paquets avant de construire ses données.
"""
from __future__ import annotations

import argparse
import gzip
import json
import platform
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import OPEN_LICENSES, REPO, data_dir

PACKS = REPO / "matthew" / "packs"
SCHEMA = "matthew.pack/1"


class PackError(ValueError):
    pass


def _records(root: Path):
    path = Path(root) / "synthetic" / "records.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            yield json.loads(line)
        except ValueError:
            pass


def exportable(r: dict) -> bool:
    return (r.get("status") == "ok" and r.get("synthetic") is True and bool(r.get("messages"))
            and str(r.get("license", "")).lower() in OPEN_LICENSES)


def check(r: dict) -> None:
    """Lève PackError si l'exemple ne peut pas voyager."""
    from matthew.journal import scrub
    if not exportable(r):
        raise PackError(f"exemple non exportable ({r.get('episode')})")
    if "gemini" in str(r.get("teacher", "")).lower() or "gemini" in str(r.get("verifier", "")).lower():
        raise PackError("professeur Gemini refusé")
    for m in r["messages"]:
        text = m.get("content") or ""
        if scrub(text) != text:
            raise PackError(f"secret ou coordonnée dans {r.get('episode')}")


def export(root: Path | None = None, out_dir: Path | None = None, clock=time.time, host: str | None = None) -> dict:
    """Écrit les exemples pas encore emballés (identifiants déjà présents dans un paquet : sautés)."""
    root, out_dir = Path(root or data_dir()), Path(out_dir or PACKS)
    seen = {r.get("episode") for r in imported(out_dir)}
    rows, held = [], 0
    for r in _records(root):
        if exportable(r) and r.get("episode") not in seen:
            try:
                check(r)
            except PackError:              # une adresse ou un IBAN, même inventés : le dépôt est public
                held += 1
                continue
            rows.append(r)
    out_dir.mkdir(parents=True, exist_ok=True)
    decls = root / "tools.json"
    if decls.exists():                  # les déclarations d'outils (tirées du code) : le cloud en a besoin
        (out_dir / "tools.json").write_bytes(decls.read_bytes())
    if not rows:
        return {"written": 0, "held": held}
    name = f"{time.strftime('%Y%m%d-%H%M', time.localtime(clock()))}-{(host or platform.node() or 'pc').lower()[:20]}.jsonl.gz"
    head = {"schema": SCHEMA, "n": len(rows), "at": clock()}
    with gzip.open(out_dir / name, "wt", encoding="utf-8") as f:
        f.write(json.dumps(head) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"written": len(rows), "file": name, "held": held}


def imported(pack_dir: Path | None = None):
    for p in sorted(Path(pack_dir or PACKS).glob("*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            head = json.loads(f.readline() or "{}")
            if head.get("schema") != SCHEMA:
                continue
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    pass


def import_all(root: Path | None = None, pack_dir: Path | None = None) -> dict:
    """Ajoute à synthetic/records.jsonl les exemples des paquets absents (par identifiant)."""
    root = Path(root or data_dir())
    have = {r.get("episode") for r in _records(root)}
    path = root / "synthetic" / "records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    added = refused = 0
    with path.open("a", encoding="utf-8") as f:
        for r in imported(pack_dir):
            if r.get("episode") in have:
                continue
            try:
                check(r)
            except PackError:
                refused += 1
                continue
            f.write(json.dumps({**r, "pack": True}, ensure_ascii=False) + "\n")
            have.add(r.get("episode"))
            added += 1
    return {"added": added, "refused": refused}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.pack")
    ap.add_argument("cmd", choices=("export", "import", "status"))
    a = ap.parse_args(argv)
    if a.cmd == "export":
        out = export()
    elif a.cmd == "import":
        out = import_all()
    else:
        packs = sorted(p.name for p in PACKS.glob("*.jsonl.gz")) if PACKS.exists() else []
        out = {"packs": packs, "examples": sum(1 for _ in imported())}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
