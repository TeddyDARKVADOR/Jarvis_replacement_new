"""
linuxsandbox.py — run a Studio project in a throwaway sandbox, on Linux. The twin of winsandbox.py.

Same promise as Windows Sandbox, built from what Linux ships:

    bubblewrap (bwrap) — the sandbox Flatpak uses, no daemon, no root:
      • the whole system READ-ONLY (so Python, Node and the project's tools are there);
      • the project copied into a tmpfs /projet before it runs: it writes freely, nothing
        reaches the disk, everything vanishes with the process;
      • a private /tmp, an empty home, a minimal /dev (no microphone, no camera, no GPU);
      • NO network (its own empty network namespace), its own PID namespace, dies with
        its parent; no folder of this PC it can write to;
      • 4 GB of memory at most when systemd can enforce it (systemd-run --user --scope).
    A GUI application gets the display socket (X11 or Wayland) and nothing else; X11
    lets a window read the keyboard of other X11 windows — said in the journal.
    Without bwrap: podman or docker (--network none, read-only project), scripts only.

A web page is NOT run here: the sandbox has no network and its own browser would be
another program to trust. The Studio's preview already serves static files only.

    available()                      '' when it can run now, else why not (French)
    prepare(project, dir, meta, map) -> the launch spec (sandbox/launch.json)
    launch(spec)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .runner import CommandRefused, parse

WORKDIR = "/projet"
SOURCE = "/source"
MEMORY_MB = 4096
ENABLE_HINT = ("Aucun bac à sable n'est installé sur ce PC. Le plus simple : "
               "« sudo apt install bubblewrap » (Debian, Ubuntu) ou « sudo dnf install bubblewrap » (Fedora). "
               "Podman ou Docker conviennent aussi, pour les scripts sans fenêtre.")
_PIDFILE = Path(tempfile.gettempdir()) / "jarvis-linuxsandbox.pid"


def engine(which=shutil.which) -> str:
    for name in ("bwrap", "podman", "docker"):
        if which(name):
            return name
    return ""


def running() -> bool:
    try:
        pid = int(_PIDFILE.read_text())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def available(which=shutil.which) -> str:
    if not sys.platform.startswith("linux"):
        return "Ce bac à sable n'existe que sous Linux."
    if not engine(which):
        return ENABLE_HINT
    if running():
        return "Un projet tourne déjà dans le bac à sable : une seule fois à la fois. Fermez-le d'abord."
    return ""


def command(kind: str, run_command: str, test_command: str, entry: str) -> list[str]:
    """What runs inside /projet. Raises CommandRefused when it cannot run in the sandbox."""
    if kind == "web" or entry.endswith((".html", ".htm")):
        raise CommandRefused("Une page web ne se lance pas dans le bac à sable Linux (pas de réseau) : "
                             "utilisez l'aperçu du Studio, qui ne sert que des fichiers.")
    line = (run_command or test_command or "").strip()
    if not line:
        raise CommandRefused("Ce projet n'a ni commande de lancement ni commande de test.")
    argv = parse(line)
    prog = Path(argv[0]).name.lower().removesuffix(".exe")
    if prog == "pytest":
        return ["python3", "-m", "pytest", *argv[1:]]
    if prog in ("python", "python3", "py"):
        return ["python3", *argv[1:]]
    if prog == "node":
        return ["node", *argv[1:]]
    raise CommandRefused(f"« {argv[0]} » ne peut pas être lancé dans le bac à sable (Python ou Node uniquement).")


def _display(gui: bool) -> tuple[list[str], dict]:
    if not gui:
        return [], {}
    args, env = [], {}
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_RUNTIME_DIR"):
        sock = Path(os.environ["XDG_RUNTIME_DIR"]) / os.environ["WAYLAND_DISPLAY"]
        if sock.exists():
            args += ["--ro-bind", str(sock), f"/run/user/sandbox/{sock.name}"]
            env.update(XDG_RUNTIME_DIR="/run/user/sandbox", WAYLAND_DISPLAY=sock.name)
    if os.environ.get("DISPLAY") and Path("/tmp/.X11-unix").exists():
        args += ["--ro-bind", "/tmp/.X11-unix", "/tmp/.X11-unix"]
        env["DISPLAY"] = os.environ["DISPLAY"]
    return args, env


def bwrap_argv(project_dir: Path, inner: list[str], gui: bool = False) -> list[str]:
    shell = (f'cp -a {SOURCE}/. {WORKDIR}/ 2>/dev/null; rm -rf {WORKDIR}/.studio {WORKDIR}/node_modules; '
             f'cd {WORKDIR} && [ -d _deps ] && export PYTHONPATH={WORKDIR}/_deps; exec "$@"')
    disp, env = _display(gui)
    argv = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--tmpfs", "/home", "--tmpfs", "/root", "--tmpfs", "/run", "--tmpfs", WORKDIR,
            "--ro-bind", str(project_dir), SOURCE, *disp,
            "--unshare-all", "--die-with-parent", "--new-session", "--chdir", WORKDIR,
            "--setenv", "HOME", "/home/sandbox", "--setenv", "PYTHONUTF8", "1",
            "--setenv", "PYTHONIOENCODING", "utf-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    for k, v in env.items():
        argv += ["--setenv", k, v]
    argv += ["sh", "-c", shell, "sandbox", *inner]
    if shutil.which("systemd-run") and Path("/run/systemd/system").exists():
        argv = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={MEMORY_MB}M", "--", *argv]
    return argv


def container_argv(tool: str, project_dir: Path, inner: list[str]) -> list[str]:
    image = "docker.io/library/node:22-slim" if inner[0] == "node" else "docker.io/library/python:3.12-slim"
    shell = (f"cp -a {SOURCE}/. {WORKDIR}/ && cd {WORKDIR} && "
             f'( [ -d _deps ] && export PYTHONPATH={WORKDIR}/_deps ; exec "$@" )')
    return [tool, "run", "--rm", "--network", "none", "--memory", f"{MEMORY_MB}m", "--pids-limit", "256",
            "-v", f"{project_dir}:{SOURCE}:ro", "--tmpfs", WORKDIR, "-e", "PYTHONUTF8=1",
            image, "sh", "-c", shell, "sandbox", *inner]


def terminal(which=shutil.which) -> list[str] | None:
    """A terminal that stays open on the output, like `cmd /k` in Windows Sandbox."""
    for name, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                       ("xfce4-terminal", ["-x"]), ("xterm", ["-hold", "-e"])):
        if which(name):
            return [name, *args]
    return None


def prepare(project: str, project_dir: Path, meta_dir: Path, pmap: dict) -> Path:
    kind = str(pmap.get("kind") or "")
    inner = command(kind, str(pmap.get("run_command") or ""), str(pmap.get("test_command") or ""),
                    str(pmap.get("entry_point") or ""))
    tool = engine()
    if not tool:
        raise CommandRefused(ENABLE_HINT)
    if tool == "bwrap":
        argv = bwrap_argv(project_dir.resolve(), inner, gui=kind == "gui")
    elif kind == "gui":
        raise CommandRefused("Une application avec fenêtre demande bubblewrap (sudo apt install bubblewrap).")
    else:
        argv = container_argv(tool, project_dir.resolve(), inner)
    root = meta_dir / "sandbox"
    root.mkdir(parents=True, exist_ok=True)
    spec = root / "launch.json"
    spec.write_text(json.dumps({"project": project, "argv": argv, "engine": tool, "gui": kind == "gui",
                                "log": str(root / "run.log")}, ensure_ascii=False, indent=1), encoding="utf-8")
    return spec


def launch(spec: Path) -> None:
    s = json.loads(Path(spec).read_text(encoding="utf-8"))
    argv = s["argv"]
    term = None if s.get("gui") else terminal()
    if term and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        argv = [*term, *argv]
        out = subprocess.DEVNULL
    else:
        out = open(s["log"], "ab")
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            start_new_session=True, close_fds=True)
    _PIDFILE.write_text(str(proc.pid))
