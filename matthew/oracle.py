"""
matthew/oracle.py — rapatrier le journal de Matthew collecté sur le serveur Oracle.

Quand on parle à Matthew depuis le téléphone, la conversation passe par Oracle : c'est là
que le journal est écrit (memory/matthew/journal/*.jsonl, sous /opt/jarvis). Sans ce module,
le PC d'entraînement ne voyait jamais ces épisodes — la meilleure donnée qui existe.

Le PC tire le journal par Tailscale SSH (chiffré, entre tes machines seulement : jamais par
GitHub ni par un service tiers), en lecture seule côté serveur. Chaque fichier arrive sous
journal/oracle-<jour>.jsonl et remplace la copie précédente : rejouer ne duplique rien.

    python -m matthew.oracle pull
Réglage "oracle_ssh" (section matthew) : "ubuntu@jarvis-vps" par défaut, None le coupe.
La boucle de nuit le fait d'elle-même (étape « oracle »).
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import config, data_dir

REMOTE_DIR = "/opt/jarvis/memory/matthew"
PREFIX = "oracle-"


def pull(target: str | None = None, root: Path | None = None, runner=subprocess.run) -> dict:
    target = target if target is not None else config().get("oracle_ssh")
    if not target:
        return {"ok": True, "skipped": "oracle_ssh non réglé"}
    root = Path(root or data_dir())
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target,
           f"sudo -n test -d {REMOTE_DIR}/journal && sudo -n tar czf - -C {REMOTE_DIR} journal || true"]
    try:
        cp = runner(cmd, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "reason": f"Oracle injoignable : {e}"}
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace") if isinstance(cp.stderr, bytes) else str(cp.stderr or "")
        return {"ok": False, "reason": (err.strip().splitlines() or ["ssh a échoué"])[-1][:200]}
    if not cp.stdout:
        return {"ok": True, "files": 0, "note": "aucun journal sur Oracle pour l'instant"}
    dest = root / "journal"
    dest.mkdir(parents=True, exist_ok=True)
    n = lines = 0
    with tarfile.open(fileobj=io.BytesIO(cp.stdout), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = Path(m.name).name
            # Seulement des fichiers de journal à plat : pas de chemin, pas de lien, pas d'autre type.
            if not m.isfile() or not name.endswith(".jsonl") or "/" in name or name.startswith("."):
                continue
            data = tar.extractfile(m).read()
            out = dest / (name if name.startswith(PREFIX) else PREFIX + name)
            out.write_bytes(data)
            n += 1
            lines += data.count(b"\n")
    return {"ok": True, "files": n, "records": lines, "into": str(dest)}


def main(argv=None) -> int:
    out = pull()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
