"""
journal.py — what Matthew actually did, written by the tools that did it.

The HUD never shows text the model wrote about its progress. Every line in
the Studio panel is an event appended HERE by the code that performed the
action: the sandbox wrote a file, the runner started a command and it exited
with code 1, the checkpoint module copied 14 files. The model's own words only
appear as the *content* of a "plan" event, labelled as such.

One JSON object per line in <root>/.studio/<project>/journal.jsonl — readable
with any editor, append-only, survives a crash mid-write (a torn last line is
skipped on load). A bounded copy lives in memory for the HUD.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

KINDS = {"phase", "plan", "task", "file", "command", "test", "error", "checkpoint",
         "restore", "summary", "info", "blocked", "confirm", "preview"}
MEMORY_EVENTS = 400
TERMINAL_LINES = 600
MAX_LINE = 400


class Journal:
    def __init__(self, meta_dir: Path) -> None:
        self._path = Path(meta_dir) / "journal.jsonl"
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=MEMORY_EVENTS)
        self._terminal: deque[dict] = deque(maxlen=TERMINAL_LINES)
        self._seq = 0
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in deque(fh, maxlen=MEMORY_EVENTS):
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, dict):
                        self._events.append(ev)
                        self._seq = max(self._seq, int(ev.get("seq", 0) or 0))
        except OSError:
            pass

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def add(self, kind: str, msg: str, /, **data) -> dict:
        if kind not in KINDS:
            kind = "info"
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": round(time.time(), 3), "kind": kind,
                  "msg": str(msg)[:600]}
            if data:
                ev["data"] = data
            self._events.append(ev)
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
        return ev

    def terminal(self, line: str, stream: str = "out", /) -> None:
        """One line of a running command's output (memory only; the command's
        full output is also returned to the caller and summarised in events)."""
        with self._lock:
            self._seq += 1
            self._terminal.append({"seq": self._seq, "ts": round(time.time(), 3),
                                   "s": stream, "t": str(line)[:MAX_LINE]})

    def recent(self, limit: int = 150) -> list[dict]:
        with self._lock:
            return list(self._events)[-limit:]

    def terminal_tail(self, limit: int = 250) -> list[dict]:
        with self._lock:
            return list(self._terminal)[-limit:]

    def files_touched(self) -> list[dict]:
        """Every file event still in memory, newest state per path."""
        seen: dict[str, dict] = {}
        with self._lock:
            for ev in self._events:
                if ev.get("kind") == "file":
                    d = ev.get("data") or {}
                    if d.get("path"):
                        seen[d["path"]] = {"path": d["path"], "op": d.get("op", "write"),
                                           "ts": ev["ts"], "lines": d.get("lines")}
        return sorted(seen.values(), key=lambda x: -x["ts"])
