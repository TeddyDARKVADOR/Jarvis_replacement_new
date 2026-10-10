"""
winsandbox.py — run a Studio project inside Windows Sandbox, on request.

The Studio's own runner limits time, memory and processes, but a program it
runs keeps the rights of your Windows account. Windows Sandbox is the
stronger option Windows ships with: a throwaway virtual machine, wiped when
its window closes.

WHAT THE SANDBOX GETS (projet.wsb, written next to the project's journal)
    • the project folder, READ-ONLY, copied inside before it runs (so the
      program can write its own files — in the sandbox, never on this PC);
    • this PC's Python installation, READ-ONLY (the sandbox has none). The
      Python packages of JARVIS's environment are NOT there: only the
      standard library and the project's own _deps/;
    • NO network, NO clipboard, NO printer, NO microphone, NO camera, no GPU
      sharing, and no folder of this PC it can write to.
    Then it starts the project the way its kind needs: a web page is served
    on the sandbox's own 127.0.0.1 and opened in its Edge; a script or a GUI
    application runs in a console that stays open to show its output.

WHAT IT NEEDS
    Windows 10/11 Pro, Enterprise or Education, virtualisation on, and the
    optional feature "Windows Sandbox" (Bac à sable Windows) enabled — an
    administrator step and a restart, done once by the user, never by JARVIS.
    One sandbox at a time. Node.js is not available inside.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .runner import CommandRefused, parse

SANDBOX_ROOT = "C:\\Sandbox"
SOURCE = SANDBOX_ROOT + "\\source"
PYTHON = SANDBOX_ROOT + "\\python"
BOOT = SANDBOX_ROOT + "\\boot"
WORKDIR = "C:\\projet"
WEB_PORT = 8000
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

ENABLE_HINT = ("Windows Sandbox n'est pas activé sur ce PC. Pour l'activer (une fois, en administrateur) : "
               "Démarrer → « Activer ou désactiver des fonctionnalités Windows » → cocher « Bac à sable "
               "Windows » (Windows Sandbox) → OK → redémarrer. Nécessite Windows Pro/Entreprise/Éducation "
               "et la virtualisation activée.")


def exe() -> Path:
    return Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "WindowsSandbox.exe"


def python_home() -> Path:
    """The real installation behind a venv: that is what can be mapped."""
    return Path(getattr(sys, "base_prefix", sys.prefix))


def running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=10,
                             creationflags=_NO_WINDOW).stdout.decode("utf-8", "replace").lower()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "windowssandbox" in out


def available() -> str:
    """'' when a project can be launched in Windows Sandbox now, else why not (French)."""
    if sys.platform != "win32":
        return "Windows Sandbox n'existe que sous Windows."
    if not exe().is_file():
        return ENABLE_HINT
    if running():
        return "Une fenêtre Windows Sandbox est déjà ouverte : une seule à la fois. Fermez-la d'abord."
    return ""


def _q(arg: str) -> str:
    """cmd.exe quoting for arguments already cleared by runner.parse (no shell characters)."""
    return f'"{arg}"' if (" " in arg or not arg) else arg


def launch_line(kind: str, run_command: str, test_command: str, entry: str) -> str:
    """The command run.cmd ends with. Raises CommandRefused when it cannot run in the sandbox."""
    if kind == "web" or entry.endswith((".html", ".htm")):
        page = (entry if entry.endswith((".html", ".htm")) else "index.html").replace("\\", "/").lstrip("/")
        return (f'start "serveur du projet" /min "{PYTHON}\\python.exe" -m http.server {WEB_PORT} '
                f'--bind 127.0.0.1 --directory "{WORKDIR}"\r\n'
                f"timeout /t 2 /nobreak >nul\r\n"
                f'start "" "http://127.0.0.1:{WEB_PORT}/{page}"')
    command = (run_command or test_command or "").strip()
    if not command:
        raise CommandRefused("Ce projet n'a ni commande de lancement ni commande de test.")
    argv = parse(command)
    prog = argv[0].lower().removesuffix(".exe")
    if prog in ("node", "npm"):
        raise CommandRefused("Node.js n'est pas disponible dans Windows Sandbox.")
    if prog == "pytest":
        argv = ["python", "-m", "pytest", *argv[1:]]
    elif prog not in ("python", "python3", "py"):
        raise CommandRefused(f"« {argv[0]} » ne peut pas être lancé dans Windows Sandbox (Python uniquement).")
    args = " ".join(_q(a) for a in argv[1:])
    title = "JARVIS - application" if kind == "gui" else "JARVIS - programme"
    return f'start "{title}" cmd /k ""{PYTHON}\\python.exe" {args}"'


def run_cmd(project: str, line: str) -> str:
    return "\r\n".join([
        "@echo off",
        f"title JARVIS - {project} (Windows Sandbox)",
        f'robocopy "{SOURCE}" "{WORKDIR}" /E /XD .studio node_modules __pycache__ /NFL /NDL /NJH /NJS /NP >nul',
        f'cd /d "{WORKDIR}"',
        f'set "PATH={PYTHON};{PYTHON}\\Scripts;%PATH%"',
        "set PYTHONUTF8=1",
        "set PYTHONIOENCODING=utf-8",
        "set PYTHONDONTWRITEBYTECODE=1",
        f'if exist "{WORKDIR}\\_deps" set "PYTHONPATH={WORKDIR}\\_deps"',
        line,
        "",
    ])


def config(project_dir: Path, boot_dir: Path, py_home: Path) -> str:
    folders = [(project_dir, SOURCE), (py_home, PYTHON), (boot_dir, BOOT)]
    mapped = "".join(
        "    <MappedFolder>\n"
        f"      <HostFolder>{escape(str(host))}</HostFolder>\n"
        f"      <SandboxFolder>{escape(inside)}</SandboxFolder>\n"
        "      <ReadOnly>true</ReadOnly>\n"
        "    </MappedFolder>\n" for host, inside in folders)
    return ("<Configuration>\n"
            "  <VGpu>Disable</VGpu>\n"
            "  <Networking>Disable</Networking>\n"
            "  <AudioInput>Disable</AudioInput>\n"
            "  <VideoInput>Disable</VideoInput>\n"
            "  <ProtectedClient>Enable</ProtectedClient>\n"
            "  <PrinterRedirection>Disable</PrinterRedirection>\n"
            "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
            "  <MemoryInMB>4096</MemoryInMB>\n"
            "  <MappedFolders>\n" + mapped + "  </MappedFolders>\n"
            "  <LogonCommand>\n"
            f"    <Command>{escape(BOOT)}\\run.cmd</Command>\n"
            "  </LogonCommand>\n"
            "</Configuration>\n")


def prepare(project: str, project_dir: Path, meta_dir: Path, pmap: dict) -> Path:
    """Write boot/run.cmd and projet.wsb under the project's meta folder; return the .wsb."""
    py_home = python_home()
    if not (py_home / "python.exe").is_file() and sys.platform == "win32":
        raise CommandRefused(f"Installation Python introuvable ({py_home}).")
    if "WindowsApps" in str(py_home):
        raise CommandRefused("Le Python du Microsoft Store ne peut pas être prêté au bac à sable : "
                             "installez Python depuis python.org.")
    line = launch_line(str(pmap.get("kind") or ""), str(pmap.get("run_command") or ""),
                       str(pmap.get("test_command") or ""), str(pmap.get("entry_point") or ""))
    root = meta_dir / "sandbox"
    boot = root / "boot"
    boot.mkdir(parents=True, exist_ok=True)
    (boot / "run.cmd").write_text(run_cmd(project, line), encoding="ascii", errors="replace")
    wsb = root / "projet.wsb"
    wsb.write_text(config(project_dir.resolve(), boot.resolve(), py_home.resolve()), encoding="utf-8")
    return wsb


def launch(wsb: Path) -> None:
    subprocess.Popen([str(exe()), str(wsb)], creationflags=_NO_WINDOW, close_fds=True)
