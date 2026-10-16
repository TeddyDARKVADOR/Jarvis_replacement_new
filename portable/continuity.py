"""
Continuité d'identité — JARVIS reste « lui-même » d'une machine à l'autre.

Un journal de sessions vit sur la clé (data/continuity.json). À chaque démarrage
on y inscrit : quand, sur quelle machine, quel modèle, en ligne ou non. Au boot
suivant — y compris sur un PC jamais vu — JARVIS compose un récap de reprise :
    « Rebonjour, Sir. La dernière fois, il y a 3 h, sur DESKTOP-BUREAU. Ici c'est
      une nouvelle machine, sans GPU : je passe sur Ollama. »

    python portable/continuity.py start     inscrit une session (au lancement)
    python portable/continuity.py end        clôt la session courante (à l'arrêt)
    python portable/continuity.py recap       affiche le récap de reprise
    python portable/continuity.py focus "<texte>"   note ce sur quoi on travaille

Lu aussi par l'outil « portable » (action resume). Ne lève jamais ; tout tient
dans un seul JSON sur la clé, donc la mémoire du « moi » voyage avec elle.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

MAX_SESSIONS = 40


def _data_dir() -> Path:
    d = os.environ.get("JARVIS_DATA_DIR", "").strip()
    return Path(d) if d else (Path(__file__).resolve().parents[1] / "data")


def _journal() -> Path:
    return _data_dir() / "continuity.json"


def _load() -> dict:
    try:
        return json.loads(_journal().read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": []}


def _save(data: dict) -> None:
    try:
        data["sessions"] = data.get("sessions", [])[-MAX_SESSIONS:]
        _data_dir().mkdir(parents=True, exist_ok=True)
        _journal().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _host() -> dict:
    """Profil léger de la machine (sans mesurer le disque : rapide au boot)."""
    try:
        from portable import host_profile
        p = host_profile.profile(measure_disk=False)
        return {"host_id": p["host_id"], "label": p.get("cpu", "")[:40] or p["host_id"],
                "os": p["os"], "gpu": p.get("gpu_nvidia", False), "vram": p.get("vram_gb", 0),
                "model": p["recommended_model"], "online": p.get("online", True)}
    except Exception:
        import platform
        return {"host_id": platform.node() or "host", "label": platform.node() or "host",
                "os": platform.system(), "gpu": False, "vram": 0, "model": "", "online": True}


def start() -> None:
    data = _load()
    h = _host()
    data.setdefault("sessions", []).append({
        "start": time.time(), "end": None, "host_id": h["host_id"], "label": h["label"],
        "os": h["os"], "gpu": h["gpu"], "model": h["model"], "online": h["online"], "focus": None,
    })
    _save(data)


def end() -> None:
    data = _load()
    if data.get("sessions"):
        data["sessions"][-1]["end"] = time.time()
        _save(data)


def set_focus(text: str) -> None:
    data = _load()
    if data.get("sessions"):
        data["sessions"][-1]["focus"] = str(text)[:200]
        _save(data)


def _ago(seconds: float) -> str:
    s = max(0, int(time.time() - seconds))
    if s < 90:
        return "à l'instant"
    if s < 5400:
        return f"il y a {max(1, s // 60)} min"
    if s < 172800:
        return f"il y a {max(1, s // 3600)} h"
    return f"il y a {s // 86400} j"


def recap() -> str:
    """Le récap de reprise, parlé. Conscient de la machine (connue ou nouvelle)."""
    data = _load()
    sessions = data.get("sessions", [])
    now = _host()
    # la session courante (start) est la dernière ; on regarde celle d'AVANT.
    past = [s for s in sessions if s.get("start")]
    prev = past[-2] if len(past) >= 2 else (past[-1] if past else None)
    known = any(s.get("host_id") == now["host_id"] for s in past[:-1]) if len(past) >= 1 else False

    if prev is None:
        return (f"Première mise en route sur cette clé, Sir. Machine : {now['os']}, "
                f"{'GPU ' + str(now['vram']) + ' Go' if now['gpu'] else 'sans GPU dédié'}. "
                f"{'Je suis en ligne.' if now['online'] else 'Hors-ligne : je travaillerai en local.'}")

    where = "sur cette même machine" if prev.get("host_id") == now["host_id"] else f"sur {prev.get('label', 'une autre machine')}"
    bits = [f"Rebonjour, Sir. La dernière fois, {_ago(prev['start'])}, {where}."]
    if prev.get("focus"):
        bits.append(f"On en était à : {prev['focus']}.")
    if prev.get("host_id") != now["host_id"]:
        bits.append("Nouvelle machine ici" + (" — déjà venu dessus par le passé." if known else ", que je découvre."))
    if now["online"]:
        bits.append("En ligne, Gemini disponible.")
    else:
        bits.append("Hors-ligne : je bascule sur le modèle local.")
    if not now["gpu"]:
        bits.append("Pas de GPU dédié ici, je reste sur un petit modèle.")
    return " ".join(bits)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "recap"
    try:
        if cmd == "start":
            start()
        elif cmd == "end":
            end()
        elif cmd == "focus" and len(argv) > 1:
            set_focus(argv[1])
        else:
            text = recap()
            if "--to" in argv:                      # écrire en UTF-8 (évite le codepage batch)
                dest = Path(argv[argv.index("--to") + 1])
                with open(dest, "a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
            print(text)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
