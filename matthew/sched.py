"""
matthew/sched.py — planifier les tâches de Matthew, sous Windows comme sous Linux.

    daily(nom, "02:30", argv)     chaque nuit         (la boucle d'entraînement ; catch_up=True :
                                                       rattrapée au réveil du PC)
    every(nom, 30, argv)          toutes les N minutes (la vitrine)
    at_login(nom, argv)           à l'ouverture de session (la passerelle)
    next_run(nom)                 la prochaine exécution, lue dans le système, jamais en dur

Windows : Planificateur de tâches (schtasks) et dossier Démarrage du compte (sans droits
administrateur). Linux : minuteries systemd UTILISATEUR (~/.config/systemd/user) — Persistent=true
rattrape une nuit manquée parce que le PC dormait ; sans systemd, crontab. Pour que les minuteries
tournent sans session ouverte : `loginctl enable-linger $USER` (une fois).

Chaque tâche passe par un petit script (run.cmd / run.sh) : dossier du dépôt, UTF-8, journal.
`dry=True` rend les commandes sans rien poser (affichage, selftest).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from matthew import REPO

WINDOWS = sys.platform == "win32"
#: Noms des tâches : « Matthew flywheel » dans le Planificateur, matthew-flywheel pour systemd.
CRON_MARK = "# matthew:"


def unit(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def systemd_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def has_systemd(which=shutil.which) -> bool:
    return not WINDOWS and bool(which("systemctl")) and Path("/run/systemd/system").exists()


def script(name: str, argv: list[str], log: Path, folder: Path, windows: bool = WINDOWS, stem: str | None = None) -> Path:
    """Le lanceur de la tâche : le dépôt comme dossier, UTF-8, sortie dans `log`."""
    folder.mkdir(parents=True, exist_ok=True)
    args = " ".join(f'"{a}"' if " " in a else a for a in argv)
    if windows:
        path = folder / f"{stem or unit(name)}.cmd"
        path.write_text("\r\n".join(["@echo off", f"rem {name} (généré par matthew/sched.py)", f'cd /d "{REPO}"',
                                     "set PYTHONIOENCODING=utf-8", f'echo === %DATE% %TIME% >> "{log}"',
                                     f'"{sys.executable}" {args} >> "{log}" 2>&1', ""]), encoding="utf-8")
    else:
        path = folder / f"{stem or unit(name)}.sh"
        path.write_text("\n".join(["#!/bin/sh", f"# {name} (généré par matthew/sched.py)", f'cd "{REPO}" || exit 1',
                                   "export PYTHONIOENCODING=utf-8", f'echo "=== $(date)" >> "{log}"',
                                   f'exec "{sys.executable}" {args} >> "{log}" 2>&1', ""]), encoding="utf-8", newline="\n")
        path.chmod(0o755)
    return path


def _systemd_units(name: str, launcher: Path, calendar: str | None = None, minutes: int | None = None) -> dict[str, str]:
    u = unit(name)
    service = (f"[Unit]\nDescription={name}\n\n[Service]\nType=oneshot\nExecStart={launcher}\n")
    if calendar:
        when = f"OnCalendar={calendar}\nPersistent=true\n"
    else:
        when = f"OnBootSec=5min\nOnUnitActiveSec={minutes}min\n"
    timer = f"[Unit]\nDescription={name}\n\n[Timer]\n{when}\n[Install]\nWantedBy=timers.target\n"
    return {f"{u}.service": service, f"{u}.timer": timer}


def _install_linux(name: str, launcher: Path, cron: str, calendar: str | None, minutes: int | None,
                   runner, dry: bool, which=shutil.which) -> dict:
    if has_systemd(which):
        files = _systemd_units(name, launcher, calendar, minutes)
        cmds = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", f"{unit(name)}.timer"]]
        if dry:
            return {"ok": True, "how": "systemd", "files": files, "commands": cmds}
        d = systemd_dir()
        d.mkdir(parents=True, exist_ok=True)
        for f, text in files.items():
            (d / f).write_text(text, encoding="utf-8", newline="\n")
        for c in cmds:
            cp = runner(c, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if cp.returncode != 0:
                return {"ok": False, "how": "systemd", "out": (cp.stderr or cp.stdout).strip()[:200]}
        return {"ok": True, "how": "systemd", "out": f"minuterie {unit(name)}.timer active"
                + " (pour tourner sans session ouverte : loginctl enable-linger $USER)"}
    line = f"{cron} {launcher} {CRON_MARK}{unit(name)}"
    if dry:
        return {"ok": True, "how": "cron", "line": line}
    cur = runner(["crontab", "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    keep = [x for x in (cur.stdout or "").splitlines() if not x.endswith(CRON_MARK + unit(name))]
    cp = runner(["crontab", "-"], input="\n".join(keep + [line]) + "\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return {"ok": cp.returncode == 0, "how": "cron", "out": (cp.stderr or line).strip()[:200]}


def catch_up_command(name: str) -> list[str]:
    """Windows : « exécuter dès que possible si un démarrage planifié est manqué » (schtasks ne sait
    pas le poser ; PowerShell oui, sans droits administrateur — vérifié le 2026-10-23)."""
    ps = (f"$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction Stop; $t.Settings.StartWhenAvailable = $true; "
          "Set-ScheduledTask -InputObject $t -ErrorAction Stop | Out-Null")
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]


def daily(name: str, hour: str, argv: list[str], log: Path, folder: Path, runner=subprocess.run, dry: bool = False,
          stem: str | None = None, catch_up: bool = False) -> dict:
    """`catch_up` : une heure manquée (PC éteint ou en veille) est rattrapée à son retour. Sous Linux,
    la minuterie systemd le fait toujours (Persistent=true) ; crontab, jamais."""
    launcher = script(name, argv, log, folder, stem=stem)
    if WINDOWS:
        cmd = ["schtasks", "/Create", "/TN", name, "/SC", "DAILY", "/ST", hour, "/TR", f'"{launcher}"', "/F"]
        cmds = [cmd] + ([catch_up_command(name)] if catch_up else [])
        if dry:
            return {"ok": True, "how": "schtasks", "commands": cmds}
        for c in cmds:
            cp = runner(c, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if cp.returncode != 0:
                break
        return {"ok": cp.returncode == 0, "how": "schtasks", "catch_up": catch_up,
                "out": (cp.stdout or cp.stderr).strip()[:200]}
    h, m = hour.split(":")
    return _install_linux(name, launcher, f"{int(m)} {int(h)} * * *", f"*-*-* {int(h):02d}:{int(m):02d}:00", None, runner, dry)


def every(name: str, minutes: int, argv: list[str], log: Path, folder: Path, runner=subprocess.run, dry: bool = False,
          stem: str | None = None) -> dict:
    launcher = script(name, argv, log, folder, stem=stem)
    if WINDOWS:
        cmd = ["schtasks", "/Create", "/TN", name, "/SC", "MINUTE", "/MO", str(minutes), "/TR", f'"{launcher}"', "/F"]
        if dry:
            return {"ok": True, "how": "schtasks", "commands": [cmd]}
        cp = runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return {"ok": cp.returncode == 0, "how": "schtasks", "out": (cp.stdout or cp.stderr).strip()[:200]}
    return _install_linux(name, launcher, f"*/{minutes} * * * *", None, minutes, runner, dry)


def at_login(name: str, argv: list[str], log: Path, folder: Path, runner=subprocess.run, dry: bool = False,
             stem: str | None = None) -> dict:
    """Windows : dossier Démarrage du compte (une tâche « à l'ouverture de session » exige les
    droits administrateur, refusé le 2026-10-20), sans fenêtre (wscript). Linux : service systemd
    utilisateur, sinon ~/.config/autostart (session de bureau)."""
    launcher = script(name, argv, log, folder, stem=stem)
    if WINDOWS:
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        if not startup.is_dir():
            return {"ok": False, "out": f"dossier Démarrage introuvable : {startup}"}
        vbs = startup / f"{name}.vbs"
        if not dry:
            vbs.write_text(f'CreateObject("WScript.Shell").Run """{launcher}""", 0, False\r\n', encoding="utf-8")
        return {"ok": True, "how": "Démarrage", "out": f"lancée à chaque ouverture de session : {vbs}"}
    u = unit(name)
    if has_systemd():
        service = (f"[Unit]\nDescription={name}\nAfter=network-online.target\n\n[Service]\nExecStart={launcher}\n"
                   "Restart=on-failure\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n")
        cmds = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", f"{u}.service"]]
        if dry:
            return {"ok": True, "how": "systemd", "files": {f"{u}.service": service}, "commands": cmds}
        d = systemd_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{u}.service").write_text(service, encoding="utf-8", newline="\n")
        for c in cmds:
            cp = runner(c, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if cp.returncode != 0:
                return {"ok": False, "how": "systemd", "out": (cp.stderr or cp.stdout).strip()[:200]}
        return {"ok": True, "how": "systemd", "out": f"service {u}.service actif"}
    desk = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / f"{u}.desktop"
    if not dry:
        desk.parent.mkdir(parents=True, exist_ok=True)
        desk.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={launcher}\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n", encoding="utf-8", newline="\n")
    return {"ok": True, "how": "autostart", "out": f"lancée à chaque ouverture de session : {desk}"}


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def next_run(name: str, runner=subprocess.run, now=None) -> dict | None:
    """{"at": "AAAA-MM-JJTHH:MM:SS+HH:MM" | None, "enabled": bool}, ou None sans tâche."""
    if WINDOWS:
        ps = (f"$i = Get-ScheduledTaskInfo -TaskName '{name}' -ErrorAction Stop; "
              f"$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction Stop; "
              "if ($i.NextRunTime) { $i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz') } else { '-' }; [string]$t.State")
        try:
            cp = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        lines = [x.strip() for x in (cp.stdout or "").splitlines() if x.strip()]
        if cp.returncode != 0 or len(lines) < 2:
            return None
        return {"at": lines[0] if _ISO.match(lines[0]) else None, "enabled": lines[1].lower() != "disabled"}
    u = unit(name)
    try:
        if has_systemd():
            cp = runner(["systemctl", "--user", "show", f"{u}.timer", "--timestamp=unix",
                         "-p", "NextElapseUSecRealtime", "-p", "UnitFileState", "-p", "LoadState"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            props = dict(x.split("=", 1) for x in (cp.stdout or "").splitlines() if "=" in x)
            if cp.returncode != 0 or props.get("LoadState") != "loaded":
                return None
            m = re.match(r"@(\d+)", props.get("NextElapseUSecRealtime", ""))
            at = datetime.fromtimestamp(int(m.group(1))).astimezone().isoformat(timespec="seconds") if m else None
            return {"at": at, "enabled": props.get("UnitFileState") == "enabled"}
        cp = runner(["crontab", "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    line = next((x for x in (cp.stdout or "").splitlines() if x.endswith(CRON_MARK + u)), None)
    if not line:
        return None
    return {"at": cron_next(line, now), "enabled": not line.lstrip().startswith("#")}


def cron_next(line: str, now=None) -> str | None:
    """Prochaine heure d'une ligne cron quotidienne (« M H * * * ») ou « */N * * * * »."""
    now = (now or datetime.now()).astimezone().replace(microsecond=0)
    f = line.split()
    if len(f) < 5:
        return None
    if f[0].startswith("*/") and f[1] == "*":
        n = int(f[0][2:])
        nxt = now.replace(second=0) + timedelta(minutes=n - now.minute % n)
        return nxt.isoformat(timespec="seconds")
    if f[0].isdigit() and f[1].isdigit():
        nxt = now.replace(hour=int(f[1]), minute=int(f[0]), second=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt.isoformat(timespec="seconds")
    return None
