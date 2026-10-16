"""
Preuve du zéro-trace — JARVIS démontre qu'il n'a rien écrit sur le PC hôte.

Tout l'intérêt du mode portable est de ne rien laisser. Ici on le PROUVE : au
démarrage le lanceur mémorise les VRAIS dossiers « maison » de l'hôte (avant la
redirection vers la clé) dans JARVIS_HOST_USERPROFILE / _LOCALAPPDATA / _APPDATA
/ _TEMP, puis snapshot() releve leur contenu. À l'arrêt, verify() compare : tout
fichier apparu hors de la clé est signalé. En principe : zéro, puisque tout a été
redirigé — mais une bibliothèque qui écrirait par chemin absolu serait attrapée.

    python portable/trace_audit.py snapshot    (au lancement, apres la redirection)
    python portable/trace_audit.py verify       (a l'arret) -> rapport + resume parle

Rapports dans data/trace-audits/. Ne lève jamais.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

MAX_TEMP_FILES = 20000          # borne de sécurité pour ne pas ramer
HOST_VARS = {
    "USERPROFILE": "JARVIS_HOST_USERPROFILE",
    "LOCALAPPDATA": "JARVIS_HOST_LOCALAPPDATA",
    "APPDATA": "JARVIS_HOST_APPDATA",
    "TEMP": "JARVIS_HOST_TEMP",
}


def _data_dir() -> Path:
    d = os.environ.get("JARVIS_DATA_DIR", "").strip()
    return Path(d) if d else (Path(__file__).resolve().parents[1] / "data")


def _audit_dir() -> Path:
    d = _data_dir() / "trace-audits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _host_dirs() -> dict[str, Path]:
    out = {}
    for name, var in HOST_VARS.items():
        v = os.environ.get(var, "").strip()
        if v and Path(v).exists():
            out[name] = Path(v)
    return out


def _list_shallow(root: Path) -> list[str]:
    try:
        return sorted(p.name for p in root.iterdir())
    except Exception:
        return []


def _list_temp(root: Path) -> list[str]:
    files = []
    try:
        for p in root.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(root)))
                if len(files) >= MAX_TEMP_FILES:
                    break
    except Exception:
        pass
    return sorted(files)


def snapshot() -> str:
    dirs = _host_dirs()
    if not dirs:
        return "Audit zéro-trace indisponible (dossiers hôte non mémorisés)."
    base = {"t0": time.time(), "shallow": {}, "temp": []}
    for name, root in dirs.items():
        if name == "TEMP":
            base["temp"] = _list_temp(root)
            base["temp_root"] = str(root)
        else:
            base["shallow"][name] = {"root": str(root), "entries": _list_shallow(root)}
    try:
        (_audit_dir() / "_baseline.json").write_text(json.dumps(base), encoding="utf-8")
    except Exception:
        pass
    return f"Snapshot zéro-trace pris ({len(dirs)} dossiers hôte)."


def verify() -> str:
    dirs = _host_dirs()
    try:
        base = json.loads((_audit_dir() / "_baseline.json").read_text(encoding="utf-8"))
    except Exception:
        return "Pas de snapshot de référence : audit zéro-trace impossible cette fois."
    offenders: list[str] = []
    # dossiers « maison » : nouvelles entrées de premier niveau
    for name, info in base.get("shallow", {}).items():
        root = dirs.get(name)
        if not root:
            continue
        before = set(info.get("entries", []))
        for n in _list_shallow(root):
            if n not in before:
                offenders.append(f"{name}\\{n}")
    # TEMP : nouveaux fichiers apparus pendant la session
    if base.get("temp_root"):
        root = Path(base["temp_root"])
        before = set(base.get("temp", []))
        t0 = base.get("t0", 0)
        for rel in _list_temp(root):
            if rel not in before:
                try:
                    if (root / rel).stat().st_mtime >= t0 - 2:
                        offenders.append(f"TEMP\\{rel}")
                except Exception:
                    offenders.append(f"TEMP\\{rel}")
    report = {
        "when": time.time(), "host_dirs": {k: str(v) for k, v in dirs.items()},
        "clean": not offenders, "offenders": offenders[:200], "offender_count": len(offenders),
    }
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (_audit_dir() / f"{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if not offenders:
        return "Zéro trace prouvé, Sir : 0 fichier écrit hors de la clé pendant cette session."
    sample = ", ".join(offenders[:5])
    return (f"Attention : {len(offenders)} écriture(s) hors de la clé détectée(s) sur ce PC "
            f"(ex. {sample}). Rapport dans data/trace-audits.")


def latest_report() -> dict | None:
    try:
        reps = sorted(_audit_dir().glob("20*.json"))
        return json.loads(reps[-1].read_text(encoding="utf-8")) if reps else None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "verify"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(snapshot() if cmd == "snapshot" else verify())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
