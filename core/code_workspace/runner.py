"""
runner.py — the controlled terminal.

    parse()      a command line → argv, refusing anything that needs a shell
    classify()   allow / confirm / deny, and the argv actually executed
    Runner.run() executes inside the project folder, streams every line to the
                 journal, enforces a timeout and can be stopped at any moment
    Runner.start_background()  previews and launched apps, stoppable from the HUD

WHAT A COMMAND GETS
    • no shell: `|`, `&`, `>`, `;`, backticks… are refused before anything runs
    • cwd = the project folder; path-like arguments must stay inside it
    • an environment with every *KEY*/*TOKEN*/*SECRET*/*PASSWORD* variable
      removed, TEMP pointed into the workspace, and _deps/ on PYTHONPATH
    • a Windows Job Object: memory cap, process-count cap, and the whole tree
      killed on stop or timeout (no orphaned grandchildren)
    • below-normal priority, no console window
    • nothing at all if JARVIS itself runs as administrator

WHAT NEEDS YOUR CONFIRMATION (on the HUD, via core/confirm.py)
    installing packages (pip / npm install — always into the project's _deps/
    or node_modules, never into your Python), `python -c …`, creating a venv,
    and running an executable that lives in the project.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from .sandbox import SandboxError, split_relative

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900
MAX_OUTPUT = 200_000
MEMORY_LIMIT_MB = 2048
MAX_PROCESSES = 48

_SHELL_CHARS = set("|&;<>`^%\n\r")
_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION)", re.I)
_SAFE_PY_MODULES = {"pytest", "unittest", "py_compile", "compileall", "json.tool", "doctest",
                    "pyflakes", "tabnanny", "timeit"}
_PIP_READONLY = {"list", "show", "freeze", "check", "--version", "-V", "help", "--help"}
_NPM_ALLOWED = {"test", "t", "run", "run-script", "start", "--version", "-v", "ls", "list"}
_NPM_INSTALL = {"install", "i", "ci", "add"}


class CommandRefused(Exception):
    pass


@dataclass
class Verdict:
    decision: str                     # "allow" | "confirm" | "deny"
    reason: str
    argv: list[str] = field(default_factory=list)


# ── privileges ──────────────────────────────────────────────────────────────

def is_admin() -> bool:
    try:
        if sys.platform == "win32":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


# ── parsing and policy ──────────────────────────────────────────────────────

def parse(command) -> list[str]:
    if isinstance(command, (list, tuple)):
        argv = [str(a) for a in command]
    else:
        text = str(command or "").strip()
        if not text:
            raise CommandRefused("Commande vide.")
        bad = sorted(ch for ch in set(text) if ch in _SHELL_CHARS)
        if bad:
            raise CommandRefused(f"Caractères de shell refusés ({' '.join(repr(b) for b in bad)}) : "
                                 "une seule commande, sans redirection ni enchaînement.")
        try:
            argv = shlex.split(text.replace("\\", "/"), posix=True)
        except ValueError as e:
            raise CommandRefused(f"Commande illisible : {e}")
    if not argv or len(argv) > 64:
        raise CommandRefused("Commande vide ou trop longue.")
    return argv


def _check_arg(arg: str) -> None:
    a = arg.strip()
    if not a or a.startswith("-") and "=" not in a:
        return
    value = a.split("=", 1)[1] if a.startswith("-") else a
    v = value.replace("\\", "/")
    if v.startswith("/") or v.startswith("//") or re.match(r"^[A-Za-z]:", v) or v.startswith("~"):
        raise CommandRefused(f"Argument « {arg} » : chemin absolu refusé, restez dans le projet.")
    if PureWindowsPath(value).drive:
        raise CommandRefused(f"Argument « {arg} » : chemin hors projet refusé.")
    if ".." in v.split("/"):
        raise CommandRefused(f"Argument « {arg} » : '..' refusé.")


def _inside_file(project_dir: Path, rel: str) -> Path | None:
    try:
        parts = split_relative(rel)
    except SandboxError:
        return None
    p = project_dir.joinpath(*parts)
    try:
        p.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def classify(argv: list[str], project_dir: Path) -> Verdict:
    prog = argv[0].lower()
    if prog.endswith(".exe") and prog[:-4] in ("python", "python3", "py", "node", "npm", "pytest"):
        prog = prog[:-4]
    rest = argv[1:]
    for a in rest:
        _check_arg(a)

    if prog in ("python", "python3", "py"):
        if not rest:
            return Verdict("deny", "Python interactif n'est pas disponible ici.")
        if rest[0] == "-m" and len(rest) >= 2:
            mod = rest[1]
            if mod == "pip":
                sub = rest[2] if len(rest) > 2 else ""
                if sub in _PIP_READONLY:
                    return Verdict("allow", "consultation pip", [sys.executable, *rest])
                if sub in ("install", "download"):
                    if any(x in rest for x in ("--user", "-e", "--editable", "--prefix", "--root")):
                        return Verdict("deny", "pip : --user / -e / --prefix refusés.")
                    pkgs = [x for x in rest[3:] if not x.startswith("-")]
                    extra = [] if any(x.startswith("--target") or x == "-t" for x in rest) \
                        else ["--target", "_deps"]
                    return Verdict("confirm",
                                   f"Installer {', '.join(pkgs) or 'des paquets'} dans _deps/ du projet "
                                   f"(accès réseau)", [sys.executable, *rest, *extra])
                return Verdict("deny", f"pip {sub} n'est pas autorisé.")
            if mod == "http.server":
                return Verdict("deny", "Utilisez l'aperçu du Studio pour servir une page.")
            if mod == "venv":
                return Verdict("confirm", "Créer un environnement virtuel dans le projet",
                               [sys.executable, *rest])
            if mod in _SAFE_PY_MODULES or mod.split(".")[0] in _SAFE_PY_MODULES:
                return Verdict("allow", f"python -m {mod}", [sys.executable, *rest])
            top = mod.split(".")[0]
            if (project_dir / top).is_dir() or (project_dir / f"{top}.py").is_file():
                return Verdict("allow", f"module du projet {mod}", [sys.executable, *rest])
            return Verdict("confirm", f"Lancer le module Python inconnu « {mod} »", [sys.executable, *rest])
        if rest[0] == "-c":
            return Verdict("confirm", "Exécuter du code Python en ligne (python -c)", [sys.executable, *rest])
        script = next((a for a in rest if not a.startswith("-")), None)
        if script is None:
            return Verdict("deny", "Aucun script indiqué.")
        if _inside_file(project_dir, script) is None:
            return Verdict("deny", f"« {script} » n'est pas un fichier du projet.")
        return Verdict("allow", f"python {script}", [sys.executable, *rest])

    if prog == "pytest":
        return Verdict("allow", "pytest", [sys.executable, "-m", "pytest", *rest])

    if prog == "node":
        node = shutil.which("node")
        if not node:
            return Verdict("deny", "Node.js n'est pas installé sur ce PC.")
        script = next((a for a in rest if not a.startswith("-")), None)
        if rest and rest[0] in ("--version", "-v"):
            return Verdict("allow", "node --version", [node, *rest])
        if script is None or _inside_file(project_dir, script) is None:
            return Verdict("deny", "node : indiquez un fichier .js du projet.")
        return Verdict("allow", f"node {script}", [node, *rest])

    if prog == "npm":
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            return Verdict("deny", "npm n'est pas installé sur ce PC.")
        sub = rest[0] if rest else ""
        if sub in _NPM_INSTALL:
            if any(x in rest for x in ("-g", "--global", "--location=global")):
                return Verdict("deny", "npm global refusé.")
            return Verdict("confirm", f"npm {' '.join(rest)} (accès réseau, dans node_modules du projet)",
                           [npm, *rest])
        if sub in _NPM_ALLOWED:
            return Verdict("allow", f"npm {sub}", [npm, *rest])
        return Verdict("deny", f"npm {sub} n'est pas autorisé.")

    exe = _inside_file(project_dir, argv[0])
    if exe is not None and exe.suffix.lower() == ".exe":
        return Verdict("confirm", f"Lancer l'exécutable du projet « {argv[0]} »", [str(exe), *rest])
    return Verdict("deny", f"« {argv[0]} » n'est pas une commande autorisée "
                           "(python, pytest, node, npm).")


def clean_env(project_dir: Path, tmp_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not _SECRET_RE.search(k)}
    for k in ("PYTHONSTARTUP", "PYTHONHOME", "PYTHONINSPECT", "NODE_OPTIONS"):
        env.pop(k, None)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env.update({"TEMP": str(tmp_dir), "TMP": str(tmp_dir), "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                "JARVIS_STUDIO": "1"})
    deps = project_dir / "_deps"
    env["PYTHONPATH"] = str(deps) if deps.is_dir() else ""
    return env


# ── Windows Job Object ──────────────────────────────────────────────────────

class _Job:
    """Memory / process caps and kill-the-whole-tree. No-op off Windows."""

    def __init__(self, memory_mb: int = MEMORY_LIMIT_MB, max_procs: int = MAX_PROCESSES) -> None:
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    wintypes.LPVOID, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._k32 = k32
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = (0x2000      # KILL_ON_JOB_CLOSE
                                                     | 0x0400    # DIE_ON_UNHANDLED_EXCEPTION
                                                     | 0x0200    # JOB_MEMORY
                                                     | 0x0008)   # ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = max_procs
            info.JobMemoryLimit = memory_mb * 1024 * 1024
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(h)
                return
            self.handle = h
        except Exception:
            self.handle = None

    def assign(self, proc: subprocess.Popen) -> bool:
        if not self.handle:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))
        except Exception:
            return False

    def terminate(self) -> None:
        if self.handle:
            try:
                self._k32.TerminateJobObject(self.handle, 1)
            except Exception:
                pass

    def close(self) -> None:
        if self.handle:
            try:
                self._k32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


def _kill_tree(proc: subprocess.Popen, job: _Job) -> None:
    job.terminate()
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            try:
                os.killpg(proc.pid, 9)
            except Exception:
                proc.kill()


def _popen(argv: list[str], cwd: Path, env: dict) -> tuple[subprocess.Popen, _Job]:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000 | 0x00000200 | 0x00004000  # NO_WINDOW|NEW_GROUP|BELOW_NORMAL
    else:
        kwargs["start_new_session"] = True
    job = _Job()
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    job.assign(proc)
    return proc, job


# ── the runner ──────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    command: str
    code: int | None
    output: str
    seconds: float
    timed_out: bool = False
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out and not self.stopped


class Runner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: tuple[subprocess.Popen, _Job] | None = None
        self._background: dict[str, dict] = {}

    def run(self, argv: list[str], cwd: Path, env: dict, journal=None,
            timeout: int = DEFAULT_TIMEOUT, stop_event: threading.Event | None = None,
            label: str = "") -> RunResult:
        if is_admin():
            raise CommandRefused("JARVIS tourne avec les droits administrateur : le Studio refuse "
                                 "d'exécuter des commandes dans ce mode.")
        timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
        shown = label or " ".join(Path(argv[0]).stem if i == 0 else a for i, a in enumerate(argv))
        if journal is not None:
            journal.add("command", f"$ {shown}", command=shown, state="start", timeout=timeout)
            journal.terminal(f"$ {shown}", "cmd")
        t0 = time.monotonic()
        proc, job = _popen(argv, cwd, env)
        with self._lock:
            self._current = (proc, job)
        chunks: list[str] = []
        size = [0]

        def reader() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if journal is not None:
                    journal.terminal(line)
                if size[0] < MAX_OUTPUT:
                    chunks.append(line)
                    size[0] += len(line) + 1

        th = threading.Thread(target=reader, daemon=True, name="studio-run-out")
        th.start()
        timed_out = stopped = False
        while proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                _kill_tree(proc, job)
                break
            if time.monotonic() - t0 > timeout:
                timed_out = True
                _kill_tree(proc, job)
                break
            time.sleep(0.1)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_tree(proc, job)
        th.join(timeout=3)
        job.close()
        with self._lock:
            self._current = None
        secs = round(time.monotonic() - t0, 2)
        res = RunResult(shown, proc.returncode, "\n".join(chunks), secs, timed_out, stopped)
        if journal is not None:
            state = "timeout" if timed_out else "stopped" if stopped else "end"
            msg = (f"⏱ délai dépassé ({timeout} s) : {shown}" if timed_out else
                   f"■ arrêtée : {shown}" if stopped else
                   f"{'✓' if res.code == 0 else '✗'} code {res.code} en {secs} s : {shown}")
            journal.add("command", msg, command=shown, state=state, code=res.code, seconds=secs)
            journal.terminal(f"[{state}] code={res.code} {secs}s", "cmd")
        return res

    def stop_current(self) -> bool:
        with self._lock:
            cur = self._current
        if cur is None:
            return False
        _kill_tree(*cur)
        return True

    # -- long-lived: previews and launched applications --
    def start_background(self, key: str, argv: list[str], cwd: Path, env: dict,
                         journal=None, label: str = "", url: str = "") -> dict:
        if is_admin():
            raise CommandRefused("JARVIS tourne en administrateur : lancement refusé.")
        self.stop_background(key)
        proc, job = _popen(argv, cwd, env)

        def pump() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                if journal is not None:
                    journal.terminal(raw.decode("utf-8", errors="replace").rstrip("\r\n"), key)

        threading.Thread(target=pump, daemon=True, name=f"studio-bg-{key}").start()
        with self._lock:
            self._background[key] = {"proc": proc, "job": job, "label": label, "url": url,
                                     "started": time.time()}
        if journal is not None:
            journal.add("preview", f"▶ {label}" + (f" — {url}" if url else ""), key=key, url=url)
        return {"key": key, "url": url, "pid": proc.pid}

    def stop_background(self, key: str) -> bool:
        with self._lock:
            item = self._background.pop(key, None)
        if not item:
            return False
        _kill_tree(item["proc"], item["job"])
        item["job"].close()
        return True

    def background(self) -> list[dict]:
        with self._lock:
            items = list(self._background.items())
        out = []
        for key, it in items:
            alive = it["proc"].poll() is None
            if not alive:
                with self._lock:
                    self._background.pop(key, None)
                it["job"].close()
                continue
            out.append({"key": key, "label": it["label"], "url": it["url"], "started": it["started"]})
        return out

    def stop_all(self) -> None:
        self.stop_current()
        for item in self.background():
            self.stop_background(item["key"])


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
