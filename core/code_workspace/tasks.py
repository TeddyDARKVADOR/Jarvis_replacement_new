"""
tasks.py — the living task list.

States: todo (À faire), doing (En cours), done (Terminé), blocked (Bloqué).
Only the engine and the tool move a task, and only after the operation it
describes was confirmed by a tool result — a task is never "done" because the
model said so. Persisted to <root>/.studio/<project>/tasks.json.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

STATES = ("todo", "doing", "done", "blocked")


class TaskList:
    def __init__(self, meta_dir: Path, journal=None) -> None:
        self._path = Path(meta_dir) / "tasks.json"
        self._journal = journal
        self._lock = threading.Lock()
        self._tasks: list[dict] = []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._tasks = [t for t in data if isinstance(t, dict) and "id" in t]
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._tasks, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except OSError:
            pass

    def replace(self, titles: list[dict]) -> None:
        """A new plan: [{"title", "files"}…]. Previous tasks are discarded."""
        with self._lock:
            self._tasks = []
            for i, t in enumerate(titles, 1):
                self._tasks.append({"id": i, "title": str(t.get("title", f"Tâche {i}"))[:160],
                                    "files": [str(f) for f in (t.get("files") or [])][:30],
                                    "status": "todo", "detail": ""})
            self._save()

    def add(self, title: str, files: list[str] | None = None) -> int:
        with self._lock:
            tid = max((t["id"] for t in self._tasks), default=0) + 1
            self._tasks.append({"id": tid, "title": str(title)[:160], "files": list(files or [])[:30],
                                "status": "todo", "detail": ""})
            self._save()
            return tid

    def set(self, tid: int, status: str, detail: str = "") -> None:
        if status not in STATES:
            return
        with self._lock:
            for t in self._tasks:
                if t["id"] == tid:
                    if t["status"] == status and t["detail"] == detail:
                        return
                    t["status"], t["detail"] = status, str(detail)[:300]
                    title = t["title"]
                    break
            else:
                return
            self._save()
        if self._journal is not None:
            self._journal.add("task", f"{title} → {status}", id=tid, status=status, detail=detail)

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._tasks]

    def counts(self) -> dict:
        c = {s: 0 for s in STATES}
        for t in self.all():
            c[t["status"]] = c.get(t["status"], 0) + 1
        return c
