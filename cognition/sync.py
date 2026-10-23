"""
cognition/sync.py — une seule mémoire pour Matthew, sur tous vos PC.

Chaque PC garde sa base (memory/cognition.db) et partage ses changements par
un dossier synchronisé, OneDrive par défaut (OneDrive\\MatthewAssistantMemory) :

    <dossier>/<ce-pc>.jsonl      ce que CE PC a appris, oublié, validé…
    <dossier>/<autre-pc>.jsonl   lu et appliqué ici

Pourquoi des journaux et pas la base elle-même : un fichier SQLite copié par
OneDrive pendant qu'un PC l'écrit se corrompt, et deux PC qui écrivent le même
fichier produisent des copies « en conflit ». Ici chaque fichier n'a qu'un seul
auteur : OneDrive n'a jamais rien à fusionner, et les PC n'ont pas besoin
d'être allumés en même temps.

    capture   des déclencheurs SQLite (schéma v7) écrivent chaque changement
              d'un souvenir dans sync_outbox — tous les chemins d'écriture,
              y compris la validation ou le rejet depuis ESPRIT.
    export    l'outbox est ajoutée au journal de ce PC, ligne par ligne.
    import    les journaux des autres sont lus depuis l'endroit où on s'était
              arrêté ; une ligne incomplète (OneDrive en train de copier) est
              laissée pour la prochaine fois.
    conflit   le même souvenir modifié sur deux PC : la version la plus
              récente (updated_at) gagne. Un oubli gagne toujours : un
              souvenir effacé ici l'est partout, et un oubli par sujet est
              rejoué tel quel ailleurs.

Ce qui reste propre à chaque PC : le journal d'événements, l'état affectif,
les objectifs, les vecteurs (recalculés localement).

Réglage : "memory_sync_dir" dans config/api_keys.json. Absent : OneDrive s'il
existe. Vide ("") : pas de partage.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
INTERVAL_S = 60.0


def default_folder(cfg: dict) -> Path | None:
    """Le dossier partagé d'après la configuration, ou None (désactivé)."""
    if "memory_sync_dir" in cfg:
        value = str(cfg.get("memory_sync_dir") or "").strip()
        return Path(os.path.expandvars(value)).expanduser() if value else None
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if not (onedrive and Path(onedrive).is_dir()):
        return None
    new, old = Path(onedrive) / "MatthewAssistantMemory", Path(onedrive) / "JarvisMemory"   # ancien nom, encore lu
    return old if old.is_dir() and not new.exists() else new


class MemorySync:
    def __init__(self, store, memory, folder: Path, *, device: str | None = None, log=print):
        self.store, self.memory, self.folder, self._log = store, memory, Path(folder), log
        self.device = device or self._device_id()
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

    # ── identité de ce PC ──
    def _state(self, k: str, default: str | None = None) -> str | None:
        rows = self.store.query("SELECT v FROM sync_state WHERE k=?", (k,))
        return rows[0]["v"] if rows else default

    def _set_state(self, k: str, v: str, c=None) -> None:
        (c or self.store).execute("INSERT OR REPLACE INTO sync_state(k, v) VALUES (?,?)", (k, v))

    def _device_id(self) -> str:
        dev = self._state("device")
        if not dev:
            host = re.sub(r"[^A-Za-z0-9_-]+", "-", socket.gethostname() or "pc").strip("-")[:30] or "pc"
            dev = f"{host}-{uuid.uuid4().hex[:6]}"
            self._set_state("device", dev)
        return dev

    @property
    def own_file(self) -> Path:
        return self.folder / f"{self.device}.jsonl"

    def _columns(self) -> list[str]:
        return [r["name"] for r in self.store.query("PRAGMA table_info(memories)")]

    # ── export ──
    def seed(self) -> int:
        """Au premier partage, tout ce que ce PC sait déjà part une fois."""
        if self._state("seeded") == "1":
            return 0
        with self.store.transaction() as c:
            n = c.execute("INSERT INTO sync_outbox(memory_id, op, ts) "
                          "SELECT id, 'upsert', strftime('%Y-%m-%dT%H:%M:%S','now') FROM memories").rowcount
            self._set_state("seeded", "1", c)
        return n

    def export(self) -> int:
        rows = self.store.query("SELECT id, memory_id, op, ts FROM sync_outbox ORDER BY id LIMIT 2000")
        if not rows:
            return 0
        cols = self._columns()
        lines = []
        # Un souvenir changé trois fois depuis le dernier envoi part une fois,
        # dans son état actuel (l'ordre de la DERNIÈRE capture est gardé).
        last_seen = {(r["memory_id"], r["op"]): r["id"] for r in rows}
        rows = [r for r in rows if last_seen[(r["memory_id"], r["op"])] == r["id"]]
        for r in rows:
            if r["op"] == "upsert":
                m = self.store.query("SELECT * FROM memories WHERE id=?", (r["memory_id"],))
                if not m:
                    continue                      # effacé depuis : le delete suit
                payload = {k: m[0][k] for k in cols}
                lines.append({"v": SCHEMA, "dev": self.device, "op": "upsert", "row": payload})
            elif r["op"] == "delete":
                lines.append({"v": SCHEMA, "dev": self.device, "op": "delete", "id": r["memory_id"]})
            elif r["op"] == "forget_subject":
                lines.append({"v": SCHEMA, "dev": self.device, "op": "forget_subject",
                              "subject": r["memory_id"]})
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.own_file.open("a", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        last = rows[-1]["id"]
        self.store.execute("DELETE FROM sync_outbox WHERE id <= ?", (last,))
        return len(lines)

    # ── import ──
    def _apply(self, c, item: dict, cols: list[str]) -> bool:
        op = item.get("op")
        if op == "upsert":
            row = {k: v for k, v in (item.get("row") or {}).items() if k in cols}
            if not row.get("id") or not row.get("content"):
                return False
            if c.execute("SELECT 1 FROM sync_tombstones WHERE key=?", ("id:" + row["id"],)).fetchone():
                return False                      # oublié ici : ne revient jamais
            cur = c.execute("SELECT updated_at, created_at FROM memories WHERE id=?", (row["id"],)).fetchone()
            if cur:
                mine = cur["updated_at"] or cur["created_at"] or ""
                theirs = row.get("updated_at") or row.get("created_at") or ""
                if theirs <= mine:
                    return False                  # la nôtre est au moins aussi récente
                sets = ", ".join(f"{k}=?" for k in row if k != "id")
                c.execute(f"UPDATE memories SET {sets} WHERE id=?",
                          [row[k] for k in row if k != "id"] + [row["id"]])
            else:
                keys = list(row)
                c.execute(f"INSERT INTO memories({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                          [row[k] for k in keys])
            return True
        if op == "delete" and item.get("id"):
            return c.execute("DELETE FROM memories WHERE id=?", (item["id"],)).rowcount > 0
        return False

    def _bury(self, c) -> None:
        """Un souvenir reçu qui mentionne un sujet oublié ici, et qui date
        d'avant cet oubli, repart aussitôt : l'autre PC l'avait envoyé avant
        d'apprendre l'oubli."""
        for t in c.execute("SELECT key, ts FROM sync_tombstones WHERE kind='subject'").fetchall():
            subject = t["key"].split(":", 1)[1]
            for r in self.memory.about(subject):
                if (r.get("created_at") or "")[:19] <= t["ts"]:
                    c.execute("DELETE FROM memories WHERE id=?", (r["id"],))

    def import_(self) -> int:
        if not self.folder.is_dir():
            return 0
        cols = self._columns()
        applied = 0
        for path in sorted(self.folder.glob("*.jsonl")):
            if path.name == self.own_file.name:
                continue
            key = f"offset:{path.name}"
            offset = int(self._state(key, "0") or 0)
            try:
                with path.open("rb") as fh:
                    fh.seek(offset)
                    data = fh.read()
            except OSError:
                continue
            end = data.rfind(b"\n")
            if end < 0:
                continue                          # rien de complet encore
            chunk, new_offset = data[:end + 1], offset + end + 1
            subjects = []
            with self.store.transaction() as c:
                self._set_state("applying", "1", c)
                try:
                    for raw in chunk.splitlines():
                        try:
                            item = json.loads(raw.decode("utf-8"))
                        except Exception:
                            continue
                        if item.get("op") == "forget_subject" and item.get("subject"):
                            subjects.append(item["subject"])
                        elif self._apply(c, item, cols):
                            applied += 1
                    self._bury(c)
                    self._set_state(key, str(new_offset), c)
                finally:
                    self._set_state("applying", "0", c)
            for subject in subjects:
                # Le verrou (réentrant) tenu pendant tout l'oubli : aucune
                # écriture locale d'un autre fil ne peut passer pendant que la
                # capture est coupée, et donc échapper au partage.
                with self.store.lock:
                    self._set_state("applying", "1")
                    try:
                        applied += self.memory.forget_about(subject)
                    finally:
                        self._set_state("applying", "0")
        return applied

    # ── cycle ──
    def sync_once(self) -> tuple[int, int]:
        with self._lock:
            return self.export(), self.import_()

    def start(self, interval: float = INTERVAL_S, on_change=None) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.seed()

        def loop():
            while not self._stop.is_set():
                try:
                    out, inn = self.sync_once()
                    if inn and on_change:
                        on_change(inn)
                    if out or inn:
                        self._log(f"mémoire partagée : {out} envoyé(s), {inn} reçu(s) ({self.folder})")
                except Exception as exc:
                    self._log(f"mémoire partagée : {type(exc).__name__}: {exc}")
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="cognition-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.sync_once()                      # ce qui a été appris avant l'arrêt part
        except Exception:
            pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
