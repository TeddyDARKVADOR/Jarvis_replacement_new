"""
Auto-vérification du JARVIS portable — « il refuse d'échouer au démarrage ».

Deux garde-fous :
  check()      les modules critiques s'importent-ils ? (sinon : quoi manque)
  integrity()  les fichiers de app/ correspondent-ils au manifeste de hash posé
               par sync_to_key.ps1 ? (détecte corruption sur clé capricieuse / altération)
  repair()     une dépendance manque → efface le marqueur .deps-ok pour que le
               lanceur réinstalle au prochain démarrage.

    python portable/selfcheck.py check
    python portable/selfcheck.py integrity
    python portable/selfcheck.py repair

Lu aussi par l'outil « portable » (action check). Ne lève jamais.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

CRITICAL = ["PyQt6.QtWidgets", "google.genai", "numpy", "sounddevice",
            "faster_whisper", "cv2", "fastapi", "cryptography", "yaml"]


def _app() -> Path:
    return Path(__file__).resolve().parents[1]


def check() -> tuple[bool, list[str]]:
    """(tout_ok, modules_manquants) — sans importer réellement (rapide, sûr)."""
    missing = []
    for mod in CRITICAL:
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except Exception:
            missing.append(mod)
    return (not missing, missing)


def _hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_path() -> Path:
    return _app() / "portable" / "integrity.json"


def build_manifest() -> int:
    """Calcule les hash de tous les .py de app/ et les écrit dans integrity.json.
    Appelé par sync_to_key.ps1 après la copie. Renvoie le nombre de fichiers."""
    app = _app()
    manifest = {}
    for p in app.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            manifest[str(p.relative_to(app)).replace("\\", "/")] = _hash(p)
        except Exception:
            continue
    _manifest_path().write_text(json.dumps(manifest, indent=0), encoding="utf-8")
    return len(manifest)


def integrity() -> dict:
    """Compare app/ au manifeste. {ok, changed[], missing[]} ; ok=True si pas de manifeste."""
    mp = _manifest_path()
    if not mp.exists():
        return {"ok": True, "note": "pas de manifeste d'intégrité", "changed": [], "missing": []}
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": True, "note": "manifeste illisible", "changed": [], "missing": []}
    app = _app()
    changed, missing = [], []
    for rel, want in manifest.items():
        f = app / rel
        if not f.exists():
            missing.append(rel)
        else:
            try:
                if _hash(f) != want:
                    changed.append(rel)
            except Exception:
                changed.append(rel)
    return {"ok": not (changed or missing), "changed": changed[:50], "missing": missing[:50],
            "changed_count": len(changed), "missing_count": len(missing)}


def repair() -> str:
    ok, missing = check()
    if ok:
        return "Rien à réparer : toutes les dépendances critiques sont présentes."
    marker = _app().parent / "runtime" / "python" / ".deps-ok"
    try:
        if marker.exists():
            marker.unlink()
    except Exception:
        pass
    return (f"Dépendances manquantes ({', '.join(missing)}). J'ai réamorcé la réinstallation : "
            "relance Lancer-JARVIS.bat (internet requis) et elles seront réinstallées sur la clé.")


def summary() -> str:
    ok, missing = check()
    intg = integrity()
    parts = []
    parts.append("dépendances : toutes présentes" if ok else f"dépendances MANQUANTES : {', '.join(missing)}")
    if intg.get("note"):
        parts.append(f"intégrité : {intg['note']}")
    elif intg["ok"]:
        parts.append("intégrité de la clé : intacte")
    else:
        parts.append(f"intégrité : {intg['changed_count']} modifié(s), {intg['missing_count']} manquant(s)")
    return " ; ".join(parts)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "check"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if cmd == "integrity":
        print(json.dumps(integrity(), ensure_ascii=False))
    elif cmd == "repair":
        print(repair())
    elif cmd == "manifest":
        print(f"{build_manifest()} fichiers hachés dans {_manifest_path().name}")
    else:
        ok, missing = check()
        print("OK" if ok else "MANQUE: " + ", ".join(missing))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
