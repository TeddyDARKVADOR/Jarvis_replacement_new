"""
What this workstation is doing — the numbers the SYSTÈME panel shows.

Sampled once a second on the GUI thread. Every call here is non-blocking:
`cpu_percent(None)` compares against the previous call instead of sleeping, and
the network rate is a delta between two counter reads. Without psutil the panel
says so rather than showing zeros that look like an idle machine.
"""

from __future__ import annotations

import os
import platform
import socket
import time

try:
    import psutil
except Exception:  # pragma: no cover - psutil is in requirements.txt
    psutil = None


class Metrics:
    def __init__(self) -> None:
        self._last_net: tuple[float, int, int] | None = None
        self._boot = psutil.boot_time() if psutil else 0.0
        self._disk_root = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
        if psutil:
            psutil.cpu_percent(None)  # primes the first delta

    def sample(self) -> dict:
        if psutil is None:
            return {"available": False}
        out: dict = {"available": True}
        try:
            out["cpu"] = psutil.cpu_percent(None)
            out["cores"] = psutil.cpu_count(logical=True) or 0
            vm = psutil.virtual_memory()
            out["ram"] = vm.percent
            out["ram_used"] = vm.used
            out["ram_total"] = vm.total
        except Exception:
            pass
        try:
            du = psutil.disk_usage(self._disk_root)
            out["disk"] = du.percent
            out["disk_free"] = du.free
        except Exception:
            pass
        try:
            io = psutil.net_io_counters()
            now = time.monotonic()
            if self._last_net is not None:
                t0, s0, r0 = self._last_net
                dt = max(1e-3, now - t0)
                out["net_up"] = max(0.0, (io.bytes_sent - s0) / dt)
                out["net_down"] = max(0.0, (io.bytes_recv - r0) / dt)
            self._last_net = (now, io.bytes_sent, io.bytes_recv)
        except Exception:
            pass
        try:
            battery = psutil.sensors_battery()
            if battery is not None:
                out["battery"] = battery.percent
                out["plugged"] = bool(battery.power_plugged)
        except Exception:
            pass
        if self._boot:
            out["uptime"] = max(0, int(time.time() - self._boot))
        return out


def identity() -> dict:
    """Static facts, sent once."""
    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_name": platform.processor() or platform.machine(),
    }
