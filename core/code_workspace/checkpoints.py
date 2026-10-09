"""
checkpoints.py — a copy of the project before each series of changes.

Taken automatically before JARVIS creates files, before every round of fixes,
and before a restore (so a restore can itself be undone). Stored in
<root>/.studio/<project>/checkpoints/<id>/ as plain files plus a manifest, so
you can also open them by hand.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import time
from pathlib import Path

from .sandbox import SandboxError, Workspace

MAX_CHECKPOINTS = 25
MAX_CHECKPOINT_BYTES = 60 * 1024 * 1024


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Checkpoints:
    def __init__(self, ws: Workspace, project: str, journal=None) -> None:
        self.ws = ws
        self.project = project
        self.journal = journal
        self.dir = ws.meta_dir(project) / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- listing --
    def list(self) -> list[dict]:
        out = []
        for d in self.dir.iterdir():
            m = d / "manifest.json"
            if d.is_dir() and m.is_file():
                try:
                    data = json.loads(m.read_text(encoding="utf-8"))
                    out.append({"id": data["id"], "label": data.get("label", ""),
                                "ts": data.get("ts", 0), "files": len(data.get("files", []))})
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(out, key=lambda c: c["ts"], reverse=True)

    def _manifest(self, cid: str) -> dict:
        if not cid or any(ch in cid for ch in "/\\.:"):
            raise SandboxError("Point de sauvegarde inconnu.")
        m = self.dir / cid / "manifest.json"
        if not m.is_file():
            raise SandboxError("Point de sauvegarde inconnu.")
        return json.loads(m.read_text(encoding="utf-8"))

    def latest_id(self) -> str | None:
        items = self.list()
        return items[0]["id"] if items else None

    # -- create --
    def create(self, label: str) -> dict:
        cid = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        dest = self.dir / cid
        files_dir = dest / "files"
        files_dir.mkdir(parents=True)
        manifest = {"id": cid, "label": str(label)[:120], "ts": time.time(), "files": []}
        total = 0
        for f in self.ws.list_files(self.project):
            try:
                src = self.ws.resolve(self.project, f["path"])
            except SandboxError:
                continue
            total += f["size"]
            if total > MAX_CHECKPOINT_BYTES:
                break
            target = files_dir / f["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target, follow_symlinks=False)
            manifest["files"].append({"path": f["path"], "size": f["size"], "sha": _sha(target)})
        (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        self._prune()
        if self.journal is not None:
            self.journal.add("checkpoint", f"Point de sauvegarde « {label} » ({len(manifest['files'])} fichiers)",
                             id=cid, files=len(manifest["files"]))
        return {"id": cid, "label": label, "files": len(manifest["files"])}

    def _prune(self) -> None:
        items = self.list()
        for old in items[MAX_CHECKPOINTS:]:
            shutil.rmtree(self.dir / old["id"], ignore_errors=True)

    # -- compare --
    def changed_since(self, cid: str | None) -> list[dict]:
        """Files added / modified / deleted since checkpoint `cid`."""
        current = {f["path"]: f for f in self.ws.list_files(self.project)}
        if not cid:
            return [{"path": p, "op": "added"} for p in sorted(current)]
        try:
            man = self._manifest(cid)
        except (SandboxError, OSError, ValueError):
            return []
        before = {f["path"]: f for f in man.get("files", [])}
        out = []
        for path in sorted(set(current) | set(before)):
            if path not in before:
                out.append({"path": path, "op": "added"})
            elif path not in current:
                out.append({"path": path, "op": "deleted"})
            else:
                try:
                    now_sha = _sha(self.ws.resolve(self.project, path))
                except (SandboxError, OSError):
                    continue
                if now_sha != before[path].get("sha"):
                    out.append({"path": path, "op": "modified"})
        return out

    def diff(self, path: str, cid: str | None = None) -> str:
        cid = cid or self.latest_id()
        old = ""
        if cid:
            man = self._manifest(cid)
            if any(f["path"] == path for f in man.get("files", [])):
                p = self.dir / cid / "files" / path
                old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        new = self.ws.read_optional(self.project, path) or ""
        lines = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     fromfile=f"avant ({cid or 'rien'})", tofile="maintenant",
                                     lineterm="", n=3)
        text = "\n".join(lines)
        return text[:60000] if text else "(aucune différence)"

    # -- restore --
    def restore(self, cid: str) -> dict:
        man = self._manifest(cid)
        safety = self.create(f"avant restauration de {cid}")
        keep = {f["path"] for f in man.get("files", [])}
        removed = 0
        for f in self.ws.list_files(self.project):
            if f["path"] not in keep:
                try:
                    self.ws.delete(self.project, f["path"])
                    removed += 1
                except SandboxError:
                    pass
        written = 0
        for f in man.get("files", []):
            src = self.dir / cid / "files" / f["path"]
            if not src.is_file():
                continue
            try:
                dst = self.ws.resolve(self.project, f["path"], for_write=True)
            except SandboxError:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(".jarvis-restore-" + dst.name)
            shutil.copyfile(src, tmp, follow_symlinks=False)
            tmp.replace(dst)
            written += 1
        if self.journal is not None:
            self.journal.add("restore", f"Projet restauré au point « {man.get('label', cid)} » "
                                        f"({written} fichiers rétablis, {removed} retirés)",
                             id=cid, safety=safety["id"], written=written, removed=removed)
        return {"restored": cid, "written": written, "removed": removed, "safety": safety["id"]}
