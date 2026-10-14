"""
nightshift/state.py — l'état local du veilleur, dans memory/nightshift.json.

    {"cache":    {"report": {…}, "etag": "…", "fetched_at": 1760400000.0, "url": "…"},
     "briefing": {"announced": "<run_id>", "stale_warned": "<run_id>"}}

Ignoré par Git (.gitignore), comme memory/reminders.json : les selftests qui
surveillent memory/ ne le voient pas. Écriture atomique (fichier temporaire
puis renommage) sous verrou : le préchargement du briefing et l'onglet NUIT
peuvent écrire en même temps. Rien ici ne lève : un état illisible est un
état vide.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "memory" / "nightshift.json"
_lock = threading.Lock()


def load(path: Path | str | None = None) -> dict:
    try:
        data = json.loads(Path(path or PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def update(section: str, values: dict, path: Path | str | None = None) -> dict:
    """Fusionne `values` dans data[section] et réécrit le fichier. Renvoie l'état écrit."""
    p = Path(path or PATH)
    with _lock:
        data = load(p)
        current = data.get(section) if isinstance(data.get(section), dict) else {}
        current.update(values)
        data[section] = current
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".nightshift-", suffix=".tmp", dir=str(p.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, p)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except Exception:
            pass
        return data
