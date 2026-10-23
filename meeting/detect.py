"""
meeting/detect.py — qui utilise le micro ou la caméra, en ce moment, sur ce poste.

    usage = in_use()          Usage(mic=[apps], camera=[apps], source="windows", error="")

Aucune liste d'applications de visio : c'est l'usage du matériel qui compte,
quelle que soit l'application (Teams, Zoom, Meet dans un navigateur, Discord…).
Matthew lui-même est exclu : il écoute le micro pour son mot d'éveil, et capte la
réunion une fois qu'elle a commencé.

WINDOWS  micro : les sessions de capture WASAPI de chaque micro actif
         (IAudioSessionManager2, via pycaw) — l'état « active » et le PID de
         chaque processus qui enregistre, en temps réel, sans droits
         d'administrateur. caméra : HKCU\\…\\CapabilityAccessManager\\
         ConsentStore\\webcam, une clé par application (les applications de
         bureau sous NonPackaged, « # » pour « \\ ») ; LastUsedTimeStop == 0 :
         en cours. Mesuré le 2026-10-16 sur un Windows 11 Insider (26300) : ce
         registre n'y est plus tenu à jour (Windows écrit désormais dans une base
         réservée aux administrateurs), d'où WASAPI pour le micro ; le registre
         reste le repli du micro sans pycaw.
LINUX    caméra : un processus qui tient /dev/video* ouvert (/proc/<pid>/fd).
         micro : les flux d'enregistrement de PulseAudio ou de PipeWire
         (pactl list source-outputs), hors moniteurs (« .monitor » : c'est le
         son du poste, pas le micro) et hors flux en pause.
MACOS    CoreAudio (kAudioDevicePropertyDeviceIsRunningSomewhere de l'entrée
         par défaut, ce qui allume le point orange) et CoreMediaIO pour la
         caméra, par ctypes. macOS ne dit pas QUI : l'application vaut « ? »,
         et notre propre capture compte aussi (voir watcher.py).

Ne lève jamais : une erreur donne une liste vide et la raison dans `error`.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Usage:
    mic: list[str] = field(default_factory=list)
    camera: list[str] = field(default_factory=list)
    source: str = ""
    error: str = ""

    @property
    def any(self) -> bool:
        return bool(self.mic or self.camera)

    def describe(self) -> str:
        parts = []
        if self.camera:
            parts.append("caméra : " + ", ".join(sorted(set(self.camera))))
        if self.mic:
            parts.append("micro : " + ", ".join(sorted(set(self.mic))))
        return " ; ".join(parts) or "ni micro ni caméra"


# ── Windows ──────────────────────────────────────────────────────────────────

_CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"


def _own_executables() -> set[str]:
    """python.exe et pythonw.exe de ce processus : Matthew, à ne pas compter."""
    exe = Path(sys.executable)
    names = {exe.name.lower(), "python.exe", "pythonw.exe"}
    return {str(exe.with_name(n)).lower() for n in names}


def app_name(key: str) -> str:
    """« C:#Program Files#Zoom#Zoom.exe » → « Zoom.exe » ; « MSTeams_8wekyb3d8bbwe » → « MSTeams »."""
    if "#" in key:
        return key.rsplit("#", 1)[-1]
    return re.sub(r"_[a-z0-9]{13}$", "", key)


def windows_active(entries: list[tuple[str, int, int]], own: set[str]) -> list[str]:
    """(clé, LastUsedTimeStart, LastUsedTimeStop) → les applications en cours. Pur, testable."""
    out = []
    for key, start, stop in entries:
        if start and not stop and key.replace("#", "\\").lower() not in own:
            out.append(app_name(key))
    return out


def _windows_entries(capability: str) -> list[tuple[str, int, int]]:
    import winreg
    entries: list[tuple[str, int, int]] = []

    def walk(path: str, depth: int) -> None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                child = f"{path}\\{sub}"
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, child) as k:
                        start = int(winreg.QueryValueEx(k, "LastUsedTimeStart")[0])
                        stop = int(winreg.QueryValueEx(k, "LastUsedTimeStop")[0])
                    entries.append((sub, start, stop))
                except OSError:
                    if depth == 0:          # NonPackaged : un niveau de plus
                        walk(child, 1)

    walk(f"{_CONSENT}\\{capability}", 0)
    return entries


def capture_sessions_active(sessions: list[tuple[int, str, int]], own_pid: int) -> list[str]:
    """(pid, nom, état WASAPI) → les processus qui enregistrent. 1 = AudioSessionStateActive. Pur."""
    return [name for pid, name, state in sessions if state == 1 and pid not in (0, own_pid)]


def _wasapi_capture_sessions() -> list[tuple[int, str, int]]:
    import comtypes
    import psutil
    from comtypes import CLSCTX_ALL
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import IAudioSessionControl2, IAudioSessionManager2
    try:
        comtypes.CoInitialize()             # fil de fond : COM n'y est pas encore initialisé
    except OSError:
        pass                                # déjà fait, dans un autre mode : il sert tel quel
    enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
    devices = enum.EnumAudioEndpoints(1, 1)  # eCapture, DEVICE_STATE_ACTIVE : tous les micros
    out = []
    for i in range(devices.GetCount()):
        mgr = devices.Item(i).Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None) \
            .QueryInterface(IAudioSessionManager2)
        sessions = mgr.GetSessionEnumerator()
        for j in range(sessions.GetCount()):
            ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
            pid = int(ctl.GetProcessId())
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = "?"
            out.append((pid, name, int(ctl.GetState())))
    return out


def camera_streaming(report: dict[str, list[tuple[int, bool]]], own_pid: int) -> list[int]:
    """Rapport de Media Foundation {caméra: [(pid, en streaming)]} → les PID qui filment, hors nous. Pur."""
    return sorted({pid for procs in report.values() for pid, on in procs if on and pid != own_pid})


_camera_monitor = None
_camera_lock = __import__("threading").Lock()


def _windows_cameras(own: set[str]) -> tuple[list[str], str]:
    """Media Foundation (temps réel) ; à défaut, le registre."""
    global _camera_monitor
    with _camera_lock:
        if _camera_monitor is None:
            try:
                from meeting._win_sensor import CameraMonitor
                _camera_monitor = CameraMonitor()
                _camera_monitor.start()
            except Exception as e:
                _camera_monitor = False
                err = f"moniteur de caméra indisponible ({type(e).__name__}) : registre"
                return windows_active(_windows_entries("webcam"), own), err
    if not _camera_monitor or _camera_monitor.error:
        err = f"moniteur de caméra : {getattr(_camera_monitor, 'error', '') or 'absent'} — registre"
        return windows_active(_windows_entries("webcam"), own), err
    import psutil
    names = []
    for pid in camera_streaming(_camera_monitor.snapshot(), os.getpid()):
        try:
            names.append(psutil.Process(pid).name())
        except Exception:
            names.append(str(pid))
    return names, ""


def _windows() -> Usage:
    own = _own_executables()
    cameras, err = _windows_cameras(own)
    usage = Usage(camera=cameras, source="windows", error=err)
    try:
        usage.mic = capture_sessions_active(_wasapi_capture_sessions(), os.getpid())
    except Exception as e:                   # pycaw absent ou COM refusé : le registre, au mieux
        usage.mic = windows_active(_windows_entries("microphone"), own)
        usage.error = f"sessions WASAPI illisibles ({type(e).__name__}) : registre"
    return usage


# ── Linux ────────────────────────────────────────────────────────────────────

def linux_cameras(proc: Path = Path("/proc"), own_pid: int | None = None) -> list[str]:
    """Les processus qui tiennent /dev/video* ouvert (ceux de l'utilisateur : les autres sont illisibles)."""
    own_pid = os.getpid() if own_pid is None else own_pid
    out = []
    for d in proc.iterdir() if proc.is_dir() else []:
        if not d.name.isdigit() or int(d.name) == own_pid:
            continue
        try:
            fds = list((d / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(fd).startswith("/dev/video"):
                    out.append((d / "comm").read_text(encoding="utf-8", errors="replace").strip() or d.name)
                    break
            except OSError:
                continue
    return out


def pulse_recorders(outputs: list[dict], sources: list[dict], own_pid: int) -> list[str]:
    """Flux d'enregistrement actifs sur un vrai micro (pactl -f json). Pur, testable."""
    monitors = {s.get("index") for s in sources if str(s.get("name", "")).endswith(".monitor")
                or s.get("monitor_of_sink") not in (None, "", "n/a")}
    out = []
    for o in outputs:
        props = o.get("properties") or {}
        try:
            pid = int(props.get("application.process.id") or 0)
        except ValueError:
            pid = 0
        if pid == own_pid or o.get("corked") or o.get("source") in monitors:
            continue
        out.append(str(props.get("application.name") or props.get("application.process.binary") or "?"))
    return out


def parse_pactl_text(text: str) -> list[dict]:
    """Repli pour un pactl sans -f json : « Source Output #n », Source, Corked, propriétés."""
    items: list[dict] = []
    for block in re.split(r"^Source Output #", text, flags=re.MULTILINE)[1:]:
        props = dict(re.findall(r'^\s+([\w.]+) = "(.*)"$', block, re.MULTILINE))
        src = re.search(r"^\s+Source: (\d+)", block, re.MULTILINE)
        corked = re.search(r"^\s+Corked: (\w+)", block, re.MULTILINE)
        items.append({"source": int(src.group(1)) if src else None, "properties": props,
                      "corked": bool(corked and corked.group(1) in ("yes", "oui"))})
    return items


def _pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5,
                          encoding="utf-8", errors="replace").stdout


def _linux_mics() -> tuple[list[str], str]:
    try:
        outputs = json.loads(_pactl("-f", "json", "list", "source-outputs") or "[]")
        sources = json.loads(_pactl("-f", "json", "list", "sources") or "[]")
    except FileNotFoundError:
        return [], "pactl absent (ni PulseAudio ni pipewire-pulse) : micro non surveillé"
    except (json.JSONDecodeError, subprocess.SubprocessError):
        try:
            outputs = parse_pactl_text(_pactl("list", "source-outputs"))
            sources = [{"index": int(i), "name": n} for i, n in
                       re.findall(r"^(\d+)\s+(\S+)", _pactl("list", "short", "sources"), re.MULTILINE)]
        except Exception as e:
            return [], f"pactl illisible : {type(e).__name__}"
    return pulse_recorders(outputs, sources, os.getpid()), ""


def _linux() -> Usage:
    mic, err = _linux_mics()
    return Usage(mic=mic, camera=linux_cameras(), source="linux", error=err)


# ── macOS ────────────────────────────────────────────────────────────────────

def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


def _mac_running(lib, get, get_size, obj: int) -> bool:
    addr = _Address(_fourcc("gone"), _fourcc("glob"), 0)
    value, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
    if get(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(value)) != 0:
        return False
    return bool(value.value)


def _macos() -> Usage:
    usage = Usage(source="macos")
    try:
        ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        addr = _Address(_fourcc("dIn "), _fourcc("glob"), 0)
        dev, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
        if ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(dev)) == 0 \
                and dev.value and _mac_running(ca, ca.AudioObjectGetPropertyData, None, dev.value):
            usage.mic.append("?")
    except Exception as e:
        usage.error = f"CoreAudio : {type(e).__name__}"
    try:
        cm = ctypes.CDLL("/System/Library/Frameworks/CoreMediaIO.framework/CoreMediaIO")
        addr = _Address(_fourcc("dev#"), _fourcc("glob"), 0)
        size = ctypes.c_uint32(0)
        if cm.CMIOObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None, ctypes.byref(size)) == 0 and size.value:
            n = size.value // 4
            devs, used = (ctypes.c_uint32 * n)(), ctypes.c_uint32(0)
            if cm.CMIOObjectGetPropertyData(1, ctypes.byref(addr), 0, None, size, ctypes.byref(used), devs) == 0:
                for d in devs:
                    a2 = _Address(_fourcc("gone"), _fourcc("glob"), 0)
                    v, s2, u2 = ctypes.c_uint32(0), ctypes.c_uint32(4), ctypes.c_uint32(0)
                    if cm.CMIOObjectGetPropertyData(d, ctypes.byref(a2), 0, None, s2, ctypes.byref(u2),
                                                    ctypes.byref(v)) == 0 and v.value:
                        usage.camera.append("?")
                        break
    except Exception as e:
        usage.error = (usage.error + " ; " if usage.error else "") + f"CoreMediaIO : {type(e).__name__}"
    return usage


def in_use() -> Usage:
    """Le micro et la caméra, maintenant. Ne lève jamais."""
    try:
        if sys.platform == "win32":
            return _windows()
        if sys.platform == "darwin":
            return _macos()
        if sys.platform.startswith("linux"):
            return _linux()
        return Usage(source=sys.platform, error="système non pris en charge")
    except Exception as e:
        return Usage(source=sys.platform, error=f"{type(e).__name__}: {e}")
