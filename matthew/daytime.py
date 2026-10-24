"""
matthew/daytime.py — entraîner Matthew de JOUR, quand l'utilisateur s'absente.

Le PC n'est plus allumé la nuit (2026-10-24). Le journal réel et les corrections ne quittent jamais
le PC (Modal ne reçoit que du synthétique) : c'est donc ici, pendant une absence, qu'ils sont appris.

Toutes les 30 minutes (tâche « Matthew jour »), `tick` :
    1. absent depuis daytime_idle_minutes (clavier et souris, GetLastInputInfo) ? sinon rien ;
    2. GPU libre (utilisation < daytime_gpu_busy %) ? sinon rien (jeu, vidéo, rendu) ;
    3. un cycle complet de la boucle (Flywheel.cycle) — qui n'entraîne que si les données neuves
       le valent (flywheel.worth_training) ;
    4. pendant l'entraînement, retour de l'utilisateur (moins de 60 s sans saisie) → arrêt net,
       carte libérée ; le prochain tick recommence de zéro (pas de reprise exacte : honnête).

    python -m matthew.daytime            un tick (ce que fait la tâche)
    python -m matthew.daytime install    pose la tâche « Matthew jour » (toutes les 30 min)
    python -m matthew.daytime status     absence, GPU, ce que ferait un tick
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import config, data_dir

BACK_S = 60          # moins d'une minute sans saisie : l'utilisateur est là


def idle_seconds() -> float | None:
    """Secondes depuis la dernière saisie (clavier, souris) ; None si on ne sait pas le dire."""
    if sys.platform != "win32":
        return None
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def gpu_util(runner=subprocess.run) -> int | None:
    """Utilisation du GPU en % (nvidia-smi) ; None sans carte NVIDIA lisible."""
    try:
        cp = runner(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=20)
        return max(int(x) for x in cp.stdout.split()) if cp.returncode == 0 and cp.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ready(cfg: dict, idle=idle_seconds, gpu=gpu_util) -> tuple[bool, str]:
    if not cfg.get("daytime_training", True):
        return False, "entraînement de jour coupé (daytime_training: false)"
    i = idle()
    if i is None:
        return False, "absence illisible sur ce système : on n'entraîne pas à l'aveugle"
    need = cfg["daytime_idle_minutes"] * 60
    if i < need:
        return False, f"tu es là (dernière saisie il y a {i / 60:.0f} min, {cfg['daytime_idle_minutes']} requises)"
    u = gpu()
    if u is not None and u > cfg["daytime_gpu_busy"]:
        return False, f"GPU occupé ({u} %)"
    return True, f"absent depuis {i / 60:.0f} min, GPU à {u if u is not None else '?'} %"


def tick(fw=None, cfg: dict | None = None, idle=idle_seconds, gpu=gpu_util) -> dict:
    cfg = cfg or config()
    ok, why = ready(cfg, idle, gpu)
    if not ok:
        return {"ran": False, "why": why}
    from matthew import train as T
    from matthew.flywheel import Flywheel

    def back() -> str | None:
        i = idle()
        return "tu es revenu" if i is not None and i < BACK_S else None
    if fw is None:
        fw = Flywheel(cfg=cfg)
        fw._trainer = lambda run: T.execute(run, cfg=fw.cfg, on_progress=fw._live, should_stop=back)
    rep = fw.cycle(execute=True)
    return {"ran": True, "why": why, "outcome": rep.get("outcome"),
            "seuil": next((s.get("reason") for s in rep.get("steps", []) if s["step"] == "seuil"), None)}


TASK = "Matthew jour"


def install(runner=subprocess.run, dry: bool = False) -> dict:
    from matthew import sched
    d = data_dir()
    return sched.every(TASK, 30, ["-m", "matthew.daytime"], d / "daytime.log", d, runner=runner, dry=dry,
                       stem="daytime")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install"]:
        out = install()
    elif argv[:1] == ["status"]:
        ok, why = ready(config())
        out = {"would_run": ok, "why": why, "idle_s": idle_seconds(), "gpu": gpu_util()}
    else:
        out = tick()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
