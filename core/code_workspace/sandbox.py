"""
sandbox.py — the one place that decides whether a path is inside the workspace.

Every read, write, delete and listing the Studio performs goes through
`Workspace.resolve()`. The model never hands a path straight to `open()`.

WHAT IS REFUSED, AND WHY
    absolute paths, drive letters      "C:/Windows/…", "/etc", "D:x"
    UNC and device namespaces          "\\\\server\\share", "\\\\?\\C:\\", "\\\\.\\PhysicalDrive0"
    alternate data streams             "notes.txt:hidden" (any ':' at all)
    reserved device names              CON, NUL, COM1, LPT1… even as "nul.txt"
    trailing dots and spaces           Windows silently strips them: "a.py." is "a.py"
    '..' that escapes                  checked after normalisation AND after resolve()
    links of every kind                symlinks, junctions, any reparse point on the
                                       way — a junction inside the workspace pointing
                                       at C:\\ would otherwise be a door out
    hard links                         a file with several names may be the same bytes
                                       as a file outside; it is neither read nor
                                       written in place (writes replace the entry)
    the Studio's own metadata          ".studio" — the journal cannot be rewritten
                                       by the code it is journaling

    The root itself is validated when configured: never a drive root, never
    your whole profile, never a system folder, never the JARVIS repository (or
    a folder that contains it).
"""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
from pathlib import Path, PureWindowsPath

META_DIR = ".studio"

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
             "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
_BAD_CHARS = set('<>:"|?*') | {chr(c) for c in range(32)}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,47}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

#: Never written to or listed, even though they are inside a project.
IGNORED_DIRS = {META_DIR, ".git", "__pycache__", "node_modules", ".venv", "venv",
                "_deps", ".pytest_cache", ".mypy_cache", "dist", "build"}

MAX_FILE_BYTES = 1_000_000          # one source file JARVIS reads or writes
MAX_PROJECT_FILES = 400             # what a listing / checkpoint will walk


class SandboxError(ValueError):
    """A path or root the Studio refuses. The message is shown to the model."""


# ── links ───────────────────────────────────────────────────────────────────

def _is_link(p: Path) -> bool:
    """Symlink, junction, or any other reparse point (Windows)."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    is_junction = getattr(p, "is_junction", None)
    try:
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _check_component(part: str) -> None:
    if not part or part in (".",):
        return
    if part == "..":
        raise SandboxError("'..' n'est pas autorisé : les chemins restent dans le projet.")
    if any(ch in _BAD_CHARS for ch in part):
        raise SandboxError(f"Caractère interdit dans « {part} » (pas de ':', '*', '?', '|', '<', '>', '\"').")
    if part != part.rstrip(" ."):
        raise SandboxError(f"« {part} » se termine par un point ou un espace (Windows le tronquerait).")
    stem = part.split(".")[0].upper().rstrip(" ")
    if stem in _RESERVED:
        raise SandboxError(f"« {part} » est un nom de périphérique réservé par Windows.")
    if part.lower() == META_DIR:
        raise SandboxError("Le dossier .studio appartient au Studio et ne peut pas être modifié.")


def split_relative(raw: str) -> list[str]:
    """Normalise a model-supplied relative path into safe components."""
    if not isinstance(raw, str) or not raw.strip():
        raise SandboxError("Chemin vide.")
    if "\x00" in raw:
        raise SandboxError("Chemin invalide.")
    text = raw.strip().replace("\\", "/")
    if text.startswith("//") or text.startswith("/") or PureWindowsPath(raw.strip()).drive \
            or PureWindowsPath(raw.strip()).root:
        raise SandboxError("Chemin absolu refusé : donnez un chemin relatif au projet.")
    if re.match(r"^[A-Za-z]:", text):
        raise SandboxError("Lettre de lecteur refusée : donnez un chemin relatif au projet.")
    if text.startswith("~"):
        raise SandboxError("'~' n'est pas autorisé.")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts:
        raise SandboxError("Chemin vide.")
    if len(parts) > 12 or len(text) > 240:
        raise SandboxError("Chemin trop long.")
    for part in parts:
        _check_component(part)
    return parts


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _norm(p: Path) -> Path:
    return Path(os.path.normcase(str(p)))


# ── the root ────────────────────────────────────────────────────────────────

def default_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("SystemDrive", "C:") + "\\") / "Jarvis_Workspace"
    return Path.home() / "Jarvis_Workspace"


def jarvis_repo() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_root(raw: str | os.PathLike) -> Path:
    """Check a folder the USER chose as the workspace root. Returns it resolved.

    Only the HUD calls this (the tool cannot change the root: a sandbox the
    sandboxed party can move is no sandbox)."""
    if not str(raw or "").strip():
        raise SandboxError("Aucun dossier indiqué.")
    p = Path(os.path.expandvars(str(raw).strip())).expanduser()
    if not p.is_absolute():
        raise SandboxError("Indiquez un chemin complet, par exemple C:\\Jarvis_Workspace.")
    if str(p).startswith("\\\\"):
        raise SandboxError("Les dossiers réseau (\\\\serveur\\…) ne sont pas acceptés.")
    if p.exists() and _is_link(p):
        raise SandboxError("Le dossier racine ne doit pas être un lien ou une jonction.")
    resolved = p.resolve(strict=False)
    if resolved.parent == resolved:
        raise SandboxError("Un lecteur entier ne peut pas servir d'espace de travail.")
    if len(resolved.parts) < 2:
        raise SandboxError("Choisissez un sous-dossier dédié.")

    rn = _norm(resolved)
    repo = _norm(jarvis_repo())
    if _contains(repo, rn) or _contains(rn, repo):
        raise SandboxError("L'espace de travail ne peut pas être dans le projet JARVIS, ni le contenir.")
    home = _norm(Path.home().resolve())
    if rn == home or _contains(rn, home):
        raise SandboxError("Votre dossier utilisateur entier ne peut pas servir d'espace de travail.")
    for key in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
                "ProgramW6432", "APPDATA", "LOCALAPPDATA"):
        sysdir = os.environ.get(key)
        if sysdir:
            sd = _norm(Path(sysdir).resolve(strict=False))
            if rn == sd or _contains(sd, rn) or _contains(rn, sd):
                raise SandboxError(f"Dossier système refusé ({sysdir}).")
    for anc in [resolved, *resolved.parents]:
        if anc.exists() and _is_link(anc):
            raise SandboxError(f"Le chemin passe par un lien ou une jonction ({anc}).")
    return resolved


def safe_project_name(name: str) -> str:
    """Folder name for a project: letters, digits, '-' and '_' only."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(name or "").strip()).strip("_-")[:48]
    if not cleaned or not _NAME_RE.match(cleaned):
        raise SandboxError("Nom de projet invalide (lettres, chiffres, '-' et '_').")
    if cleaned.split(".")[0].upper() in _RESERVED or cleaned.lower() == META_DIR:
        raise SandboxError("Nom de projet réservé.")
    return cleaned


# ── the workspace ───────────────────────────────────────────────────────────

class Workspace:
    """A validated root. Every path the Studio touches is produced here."""

    def __init__(self, root: str | os.PathLike, create: bool = True) -> None:
        self.root = validate_root(root)
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise SandboxError(f"{self.root} n'est pas un dossier.")
        if _is_link(self.root):
            raise SandboxError("Le dossier racine ne doit pas être un lien ou une jonction.")
        self.meta_root = self.root / META_DIR
        self.meta_root.mkdir(exist_ok=True)

    # -- projects --
    def project_dir(self, name: str, create: bool = False) -> Path:
        safe = safe_project_name(name)
        d = self.root / safe
        if d.exists() and (_is_link(d) or not d.is_dir()):
            raise SandboxError(f"« {safe} » n'est pas un dossier de projet valide.")
        if create:
            d.mkdir(exist_ok=True)
        return d

    def meta_dir(self, name: str) -> Path:
        d = self.meta_root / safe_project_name(name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def projects(self) -> list[str]:
        out = []
        for p in sorted(self.root.iterdir(), key=lambda x: x.name.lower()):
            if p.name.startswith(".") or not p.is_dir() or _is_link(p):
                continue
            try:
                out.append(safe_project_name(p.name))
            except SandboxError:
                continue
        return out

    # -- paths --
    def resolve(self, project: str, rel: str, *, for_write: bool = False) -> Path:
        """`rel` inside `project`, or SandboxError. Never follows a link."""
        base = self.project_dir(project, create=for_write)
        parts = split_relative(rel)
        if any(p in IGNORED_DIRS for p in parts[:-1]) and for_write:
            raise SandboxError(f"Écriture refusée dans « {'/'.join(parts[:-1])} ».")
        cur = base
        for part in parts:
            cur = cur / part
            if cur.exists() or os.path.lexists(cur):
                if _is_link(cur):
                    raise SandboxError(f"« {rel} » passe par un lien ou une jonction : refusé.")
        final = cur.resolve(strict=False)
        if not _contains(_norm(base.resolve()), _norm(final)):
            raise SandboxError("Ce chemin sort du projet : refusé.")
        if not _contains(_norm(self.root), _norm(final)):
            raise SandboxError("Ce chemin sort de l'espace de travail : refusé.")
        return cur

    def relpath(self, project: str, p: Path) -> str:
        return p.relative_to(self.project_dir(project)).as_posix()

    # -- file operations (the only ones the Studio uses) --
    def read_text(self, project: str, rel: str) -> str:
        p = self.resolve(project, rel)
        if not p.is_file():
            raise SandboxError(f"« {rel} » n'existe pas.")
        st = os.lstat(p)
        if st.st_nlink > 1:
            raise SandboxError(f"« {rel} » est un lien physique : lecture refusée.")
        if st.st_size > MAX_FILE_BYTES:
            raise SandboxError(f"« {rel} » est trop gros ({st.st_size} octets).")
        return p.read_text(encoding="utf-8", errors="replace")

    def read_optional(self, project: str, rel: str) -> str | None:
        try:
            return self.read_text(project, rel)
        except SandboxError:
            return None

    def write_text(self, project: str, rel: str, content: str) -> tuple[Path, str | None]:
        """Atomic write. Returns (path, previous content or None).

        Written to a temp file then os.replace()d: replacing the directory
        entry means a hard link that pointed elsewhere is detached, not
        written through."""
        data = str(content).encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SandboxError("Fichier trop gros pour être écrit par JARVIS.")
        p = self.resolve(project, rel, for_write=True)
        if p.exists() and not p.is_file():
            raise SandboxError(f"« {rel} » est un dossier.")
        previous = None
        if p.is_file():
            try:
                previous = self.read_text(project, rel)
            except SandboxError:
                previous = None
        p.parent.mkdir(parents=True, exist_ok=True)
        # re-check the parents now that they exist
        self.resolve(project, rel, for_write=True)
        fd, tmp = tempfile.mkstemp(prefix=".jarvis-", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, p)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return p, previous

    def delete(self, project: str, rel: str) -> str | None:
        p = self.resolve(project, rel, for_write=True)
        if not p.is_file():
            raise SandboxError(f"« {rel} » n'est pas un fichier.")
        previous = self.read_optional(project, rel)
        p.unlink()
        return previous

    def list_files(self, project: str) -> list[dict]:
        base = self.project_dir(project)
        if not base.is_dir():
            return []
        out: list[dict] = []
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            d = Path(dirpath)
            dirnames[:] = sorted(n for n in dirnames
                                 if n not in IGNORED_DIRS and not _is_link(d / n))
            for fn in sorted(filenames):
                fp = d / fn
                if fn.startswith(".jarvis-") or _is_link(fp):
                    continue
                try:
                    size = fp.stat().st_size
                except OSError:
                    continue
                out.append({"path": fp.relative_to(base).as_posix(), "size": size})
                if len(out) >= MAX_PROJECT_FILES:
                    return out
        return out
