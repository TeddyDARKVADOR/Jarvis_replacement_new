"""
pilot/recipes.py — ce qui a marché, pour la prochaine fois.

Une tâche réussie laisse sa recette : la demande, l'application, les actions
utiles (sans le texte tapé, qui peut être privé). Une nouvelle demande qui
partage assez de mots retrouve les deux recettes les plus proches, données au
modèle comme indices — « la dernière fois, msteams: puis Rechercher » — jamais
comme ordres : l'écran a le dernier mot. C'est ainsi que le pilote s'adapte à
cette machine sans qu'une application soit codée.

    store = Recipes(path); store.add(task, app, steps); store.hints(task)
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path

MAX = 200
STOP = {"dans", "avec", "pour", "une", "des", "les", "sur", "moi", "mon", "mes", "the", "and", "with", "that", "this",
        "please", "stp", "est", "faire", "fais"}


def words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]{3,}", text) if w not in STOP}


class Recipes:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def add(self, task: str, app: str, steps: list[str]) -> None:
        with self._lock:
            items = self._load()
            items.append({"task": task[:300], "app": app[:80], "steps": [s[:160] for s in steps][-20:],
                          "date": time.strftime("%Y-%m-%d")})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(items[-MAX:], ensure_ascii=False, indent=1), encoding="utf-8")

    def hints(self, task: str, n: int = 2) -> list[str]:
        want = words(task)
        if not want:
            return []
        scored = []
        for r in self._load():
            have = words(r["task"]) | words(r.get("app", ""))
            overlap = len(want & have) / max(1, len(want))
            if overlap >= 0.4:
                scored.append((overlap, r["date"], r))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [f"« {r['task']} » ({r['app'] or '?'}, {r['date']}) : " + " → ".join(r["steps"]) for _, _, r in scored[:n]]
