"""
e2e/sandbox.py — une copie jetable du dépôt, et JARVIS dedans.

    box = Sandbox(REPO, tmp).build()          # les fichiers suivis par Git, une fausse clé
    jarvis = Jarvis(box, fake).start()        # python -m e2e.launch, dans la copie
    jarvis.wait_ready() · jarvis.url("/status") · jarvis.stop() · jarvis.log_tail()

CE QUI EST COPIÉ
    `git ls-files --cached --others --exclude-standard` : le code tel qu'il est
    sur le disque, nouveaux fichiers compris, mais rien de ce que .gitignore
    écarte — donc jamais config/api_key(s).json, jamais memory/*.db ni
    long_term.json, jamais avatar/. Sans Git (archive), une copie qui écarte
    explicitement ces mêmes fichiers. Le client Android, la préprod, docs/ et
    .github/ ne servent pas à JARVIS : ils ne sont pas copiés.

LE PROCESSUS DE JARVIS
    Son environnement n'a aucune variable qui ressemble à une clé ; il a
    GOOGLE_GEMINI_BASE_URL et SSL_CERT_FILE (le faux Gemini), un proxy
    injoignable pour tout le reste (127.0.0.1 excepté), un HOME et des dossiers
    utilisateur à lui. Il a son propre groupe de processus : l'arrêter arrête
    tout ce qu'il a lancé.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

FAKE_KEY = "e2e-fake-gemini-key-0000000000"
SKIP_PREFIXES = ("client-android/", "jarvis-preprod/", "docs/", ".github/")
_SECRET_ENV = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|GEMINI|GOOGLE|GROQ|OPENROUTER)", re.I)
_NEVER = re.compile(r"(^|/)(api_keys?\.json|[^/]*credentials[^/]*\.json|long_term\.json|cognition\.db.*|"
                    r"nightshift\.json|token_usage\.jsonl|\.git|avatar|__pycache__|\.venv|node_modules)(/|$)")


def tracked_files(repo: Path) -> list[str]:
    try:
        p = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           cwd=str(repo), capture_output=True, timeout=60)
        if p.returncode == 0:
            return sorted({f for f in p.stdout.decode("utf-8", "replace").split("\0") if f})
    except Exception:
        pass
    out = []
    for path in repo.rglob("*"):
        rel = path.relative_to(repo).as_posix()
        if path.is_file() and not _NEVER.search(rel):
            out.append(rel)
    return out


class Sandbox:
    def __init__(self, repo: Path, root: Path) -> None:
        self.repo = Path(repo).resolve()
        self.root = Path(root).resolve()

    def build(self, config: dict | None = None) -> "Sandbox":
        self.root.mkdir(parents=True, exist_ok=True)
        copied = 0
        for rel in tracked_files(self.repo):
            if rel.startswith(SKIP_PREFIXES) or _NEVER.search(rel):
                continue
            src = self.repo / rel
            if not src.is_file():
                continue
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        self.copied = copied
        cfg = {"gemini_api_key": FAKE_KEY, "assistant_name": "JARVIS", "user_name": "Testeur"}
        cfg.update(config or {})
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "api_key.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                                                           encoding="utf-8")
        for d in ("home", "appdata", "localappdata", "xdg-config", "xdg-data", "xdg-cache", "tmp"):
            (self.root / ".e2e" / d).mkdir(parents=True, exist_ok=True)
        return self

    def path(self, rel: str) -> Path:
        return self.root / rel

    # ── les commandes du système, remplacées (POSIX) ─────────────────────────

    #: commande → (code de sortie, sortie). Toutes notent leur appel dans os-calls.log.
    SHIMS = {
        # Comme sur Oracle : pas de session utilisateur systemd, pas de `at`.
        "systemd-run": (1, "Failed to connect to bus: No medium found"),
        "at": (1, "at: cannot open lockfile"),
        # Jamais exécutées pour de vrai, même si une porte de confirmation cassait.
        "systemctl": (0, ""), "shutdown": (0, ""), "reboot": (0, ""), "poweroff": (0, ""),
        "nmcli": (0, "enabled"),
        # Rien ne s'ouvre ni ne sonne sur le poste de celui qui lance les tests.
        "xdg-open": (0, ""), "notify-send": (0, ""), "pactl": (0, ""), "brightnessctl": (0, ""),
        "espeak-ng": (0, ""), "spd-say": (0, ""),
    }

    def install_shims(self) -> Path | None:
        """Un dossier bin/ placé en tête du PATH de JARVIS. POSIX seulement :
        sous Windows, CreateProcess cherche System32 avant le PATH, un faux
        schtasks.exe n'y serait jamais pris — les scénarios qui en dépendent
        n'y tournent qu'en CI, sur une machine jetable."""
        if os.name == "nt":
            return None
        bin_dir = self.root / ".e2e" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name, (code, out) in self.SHIMS.items():
            echo = ""
            if out:
                echo = f"echo '{out}'" + (" >&2" if code else "") + "\n"
            script = bin_dir / name
            script.write_text(f'#!/bin/sh\necho "{name} $*" >> "$E2E_OS_CALLS"\n{echo}exit {code}\n',
                              encoding="utf-8")
            script.chmod(0o755)
        return bin_dir

    def os_calls(self) -> list[str]:
        try:
            return self.path(".e2e/os-calls.log").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []


def free_pair() -> int:
    """Un port libre dont le suivant l'est aussi (le tableau de bord ouvre PORT et PORT+1)."""
    for _ in range(200):
        port = random.randint(20000, 60000)
        try:
            for p in (port, port + 1):
                with socket.socket() as s:
                    s.bind(("0.0.0.0", p))
            return port
        except OSError:
            continue
    raise RuntimeError("aucune paire de ports libres")


class Jarvis:
    """Le vrai JARVIS, dans le bac à sable, en processus à part."""

    def __init__(self, sandbox: Sandbox, gemini, mode: str = "headless", name: str = "jarvis") -> None:
        self.box = sandbox
        self.gemini = gemini
        self.mode = mode
        self.port = free_pair()
        self.log_path = sandbox.root / ".e2e" / f"{name}.log"
        self.proc: subprocess.Popen | None = None
        self._log = None

    def env(self) -> dict:
        e = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
        e2e = self.box.root / ".e2e"
        nowhere = "http://127.0.0.1:9"
        e.update({
            "GOOGLE_GEMINI_BASE_URL": self.gemini.base_url,
            "SSL_CERT_FILE": str(self.gemini.ca),
            "HTTPS_PROXY": nowhere, "HTTP_PROXY": nowhere, "https_proxy": nowhere, "http_proxy": nowhere,
            "ALL_PROXY": nowhere, "all_proxy": nowhere,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
            "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "QT_QPA_PLATFORM": "offscreen", "JARVIS_E2E": "1",
            "TMPDIR": str(e2e / "tmp"), "TEMP": str(e2e / "tmp"), "TMP": str(e2e / "tmp"),
        })
        if os.name == "nt":
            e.update({"APPDATA": str(e2e / "appdata"), "LOCALAPPDATA": str(e2e / "localappdata")})
        else:
            e.update({"HOME": str(e2e / "home"), "XDG_CONFIG_HOME": str(e2e / "xdg-config"),
                      "XDG_DATA_HOME": str(e2e / "xdg-data"), "XDG_CACHE_HOME": str(e2e / "xdg-cache")})
        e["PYTHONPATH"] = str(self.box.root)
        bin_dir = self.box.root / ".e2e" / "bin"
        if bin_dir.is_dir():
            e["PATH"] = f"{bin_dir}{os.pathsep}{e.get('PATH', '')}"
        e["E2E_OS_CALLS"] = str(self.box.root / ".e2e" / "os-calls.log")
        return e

    def start(self) -> "Jarvis":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab")
        kw: dict = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "e2e.launch", "--port", str(self.port), "--mode", self.mode],
            cwd=str(self.box.root), env=self.env(), stdin=subprocess.DEVNULL,
            stdout=self._log, stderr=subprocess.STDOUT, **kw)
        return self

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def wait_ready(self, timeout: float = 120.0) -> float:
        """Attend que /health réponde ; renvoie le temps de démarrage en secondes."""
        from e2e import clients

        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if not self.alive():
                raise RuntimeError(f"JARVIS s'est arrêté au démarrage (code {self.proc.returncode}) :\n"
                                   + self.log_tail(25))
            status, _ = clients.http("GET", self.url("/health"), timeout=2)
            if status == 200:
                return time.monotonic() - t0
            time.sleep(0.3)
        raise TimeoutError(f"/health muet après {timeout:.0f} s :\n" + self.log_tail(25))

    def credential(self) -> str:
        data = json.loads(self.box.path("config/device_credentials.json").read_text(encoding="utf-8"))
        return data["device_token"]

    def stop(self, timeout: float = 20.0) -> int | None:
        if self.proc is None:
            return None
        if self.proc.poll() is None:
            try:
                if os.name == "nt":
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.proc.send_signal(signal.SIGTERM)
                self.proc.wait(timeout)
            except Exception:
                self._kill()
        if self._log:
            self._log.close()
            self._log = None
        return self.proc.returncode

    def _kill(self) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True, timeout=30)
            else:
                os.killpg(self.proc.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            self.proc.kill()
            self.proc.wait(10)
        except Exception:
            pass

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def log_tail(self, n: int = 40) -> str:
        return "\n".join(self.log_text().splitlines()[-n:])

    def wait_log(self, needle: str, timeout: float = 20.0, after: int = 0) -> str:
        """La première ligne du journal (après le caractère `after`) qui contient `needle`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.log_text()[after:]
            for line in text.splitlines():
                if needle in line:
                    return line
            if not self.alive():
                break
            time.sleep(0.2)
        raise TimeoutError(f"« {needle} » absent du journal de JARVIS")
