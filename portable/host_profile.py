"""
Profil matériel par machine — JARVIS portable s'adapte au PC où on branche la clé.

Détecte CPU / RAM / GPU, en déduit le modèle Ollama le mieux adapté (gros GPU
NVIDIA → gros modèle ; sinon petit modèle qui tient sur CPU), et écrit un profil
par machine dans data/host-profiles/<id>.json. La fois suivante sur le même PC,
le profil est relu tout de suite.

    python portable/host_profile.py            détecte, écrit le profil, affiche le JSON
    python portable/host_profile.py --model    n'affiche que le modèle recommandé (pour les scripts)

Aucune dépendance obligatoire : psutil est utilisé s'il est là, sinon on se rabat
sur os.cpu_count() et des sondes système légères. Ne lève jamais.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        pass
    try:  # Windows sans psutil
        if platform.system() == "Windows":
            out = subprocess.run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                                 capture_output=True, text=True, timeout=8).stdout
            nums = [int(s) for s in out.split() if s.isdigit()]
            if nums:
                return round(nums[0] / (1024 ** 3), 1)
        else:  # Linux
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal"):
                    return round(int(line.split()[1]) / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0


def _nvidia_vram_gb() -> float:
    """VRAM du plus gros GPU NVIDIA via nvidia-smi, 0 si pas de NVIDIA."""
    if not shutil.which("nvidia-smi"):
        return 0.0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout
        vals = [float(x) for x in out.split() if x.strip().replace(".", "").isdigit()]
        return round(max(vals) / 1024, 1) if vals else 0.0   # Mo -> Go
    except Exception:
        return 0.0


def disk_write_mbps(target: Path) -> float:
    """Débit d'écriture approximatif de la clé (Mo/s) : écrit 8 Mo et mesure.
    Sert à prévenir quand la clé est lente (USB 2.0) et à adapter les attentes."""
    import time
    data = os.urandom(8 * 1024 * 1024)
    f = target / ".jarvis-speedtest.tmp"
    try:
        target.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        with open(f, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        dt = time.perf_counter() - t0
        return round(8 / dt, 1) if dt > 0 else 0.0
    except Exception:
        return 0.0
    finally:
        try:
            f.unlink()
        except Exception:
            pass


def battery() -> dict:
    """État batterie : sur secteur ou non, pourcentage. {} si pas de batterie."""
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return {}
        return {"on_battery": not b.power_plugged, "percent": round(b.percent)}
    except Exception:
        return {}


def is_online(timeout: float = 2.5) -> bool:
    """Internet joignable ? (pour la bascule Gemini/cloud ↔ Ollama local.)"""
    import socket
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            socket.create_connection((host, 53), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def recommend_model(vram_gb: float, ram_gb: float) -> str:
    """Modèle Ollama outillé (tool-calling) le mieux adapté au matériel."""
    if vram_gb >= 16:
        return "qwen3:14b"
    if vram_gb >= 8:
        return "qwen3:8b"
    if vram_gb >= 5:
        return "qwen3:4b-instruct"
    # pas de gros GPU : on vise la RAM, petit modèle
    if ram_gb >= 16:
        return "qwen3:4b-instruct"
    return "llama3.2:3b"


def _host_id() -> str:
    raw = (platform.node() or "host") + "|" + platform.machine()
    import hashlib
    return platform.node().replace(" ", "_")[:24] + "-" + hashlib.sha1(raw.encode()).hexdigest()[:8]


def profile(measure_disk: bool = True) -> dict:
    vram = _nvidia_vram_gb()
    ram = _ram_gb()
    mbps = disk_write_mbps(_data_dir() / "tmp") if measure_disk else 0.0
    return {
        "host_id": _host_id(),
        "os": platform.system(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count() or 0,
        "ram_gb": ram,
        "gpu_nvidia": vram > 0,
        "vram_gb": vram,
        "recommended_model": recommend_model(vram, ram),
        "use_gpu": vram >= 5,
        "key_write_mbps": mbps,
        "key_slow": 0 < mbps < 12,     # USB 2.0 typique : on previent
        "online": is_online(),
        "battery": battery(),
    }


def _data_dir() -> Path:
    d = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if d:
        return Path(d)
    # hors portable : à côté du dépôt, dossier jetable
    return Path(__file__).resolve().parents[1] / "data"


def save(prof: dict) -> Path | None:
    try:
        d = _data_dir() / "host-profiles"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{prof['host_id']}.json"
        f.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        return f
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        prof = profile()
    except Exception as e:
        print(f"(profil indisponible : {e})")
        print("llama3.2:3b" if "--model" in argv else "")
        return 0
    if "--model" in argv:
        print(prof["recommended_model"])
        return 0
    save(prof)
    print(json.dumps(prof, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
