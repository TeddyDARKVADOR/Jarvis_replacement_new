"""
quota/ — un seul registre des quotas gratuits pour tout JARVIS.

Chaque module (resilience pour les appels du cœur, pilot, meeting) découvrait
seul qu'un modèle était épuisé, en se faisant refuser. Ici, un registre commun :

    quota.allowed(model)          False si le modèle est épuisé pour la minute ou le jour
    quota.order(models)           la liste sans les modèles épuisés (ordre de l'appelant gardé)
    quota.note_ok(model)          une requête réussie de plus
    quota.note_error(model, err)  lit le 429 : quel quota, quelle limite, jusqu'à quand
    quota.status()                le tableau : requêtes du jour, limite, repos

CE QU'ON SAIT (lu dans les erreurs 429 de Google le 2026-10-16, et mesuré par
aiplug le 2026-09-02) : les quotas gratuits sont comptés PAR PROJET et PAR
MODÈLE (pas par clé), et repartent à minuit heure du Pacifique.
  gemini-3.5/3.6/3.7/3.8-flash, 3-flash-preview : 5 par minute, 20 par jour
  gemini-3.5-flash-lite, 3.1-flash-lite         : 15 par minute, 500 par jour
Les autres limites s'apprennent : chaque 429 porte « quotaId » et « quotaValue ».

Le registre vit dans memory/quota.json (ignoré par Git), écrit atomiquement :
la voix, le pilote et le copilote du même poste le partagent.

Paquet racine (contrat V2), bibliothèque standard seulement ; chaque appelant
l'importe sous garde — le supprimer rend chaque module à son comportement seul.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

#: (requêtes par minute, requêtes par jour) du palier gratuit.
KNOWN: dict[str, tuple[int, int]] = {
    "gemini-3.5-flash": (5, 20), "gemini-3.6-flash": (5, 20), "gemini-3.7-flash": (5, 20),
    "gemini-3.8-flash": (5, 20), "gemini-3-flash-preview": (5, 20),
    "gemini-3.5-flash-lite": (15, 500), "gemini-3.1-flash-lite": (15, 500),
}
PATH = Path(os.environ.get("JARVIS_QUOTA_FILE") or Path(__file__).resolve().parent.parent / "memory" / "quota.json")

_lock = threading.RLock()
_state: dict | None = None
_minute: dict[str, deque] = {}
_dirty_at = 0.0


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:
        from datetime import timezone
        return timezone(timedelta(hours=-8))


def pacific_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), _tz()).strftime("%Y-%m-%d")


def next_reset(now: float | None = None) -> float:
    """L'instant (epoch) du prochain minuit, heure du Pacifique (plus une minute de marge)."""
    t = datetime.fromtimestamp(now if now is not None else time.time(), _tz())
    nxt = (t + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return nxt.timestamp()


def _bare(model: str) -> str:
    return str(model or "").removeprefix("models/").strip()


def _load() -> dict:
    global _state
    if _state is None:
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        _state = {"day": data.get("day", ""), "count": data.get("count", {}), "limits": data.get("limits", {}),
                  "rest": data.get("rest", {}), "last_error": data.get("last_error", {})}
    if _state["day"] != pacific_day():                 # minuit est passé : les compteurs du jour repartent
        _state["day"], _state["count"] = pacific_day(), {}
        _state["rest"] = {m: t for m, t in _state["rest"].items() if t > time.time()}
    return _state


def _save(force: bool = False) -> None:
    global _dirty_at
    if not force and time.time() - _dirty_at < 2.0:
        return
    _dirty_at = time.time()
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_name(f".quota-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, PATH)
    except OSError:
        pass                                            # un registre non écrit ne doit rien arrêter


def limits(model: str) -> tuple[int | None, int | None]:
    """(par minute, par jour) : appris d'abord, connus ensuite, None si inconnu."""
    m = _bare(model)
    with _lock:
        learned = _load()["limits"].get(m, {})
    known = KNOWN.get(m, (None, None))
    return learned.get("rpm", known[0]), learned.get("rpd", known[1])


def _recent(model: str) -> deque:
    q = _minute.setdefault(_bare(model), deque())
    while q and time.time() - q[0] > 60:
        q.popleft()
    return q


def allowed(model: str) -> bool:
    m = _bare(model)
    rpm, rpd = limits(m)
    with _lock:
        st = _load()
        if st["rest"].get(m, 0) > time.time():
            return False
        if rpd is not None and st["count"].get(m, 0) >= rpd:
            return False
        if rpm is not None and len(_recent(m)) >= rpm:
            return False
    return True


def order(models) -> list[str]:
    return [m for m in models if allowed(m)]


def note_ok(model: str) -> None:
    m = _bare(model)
    with _lock:
        st = _load()
        st["count"][m] = st["count"].get(m, 0) + 1
        _recent(m).append(time.time())
        _save()


def parse(err: str) -> tuple[str | None, int | None, float | None]:
    """(« day » | « minute » | « quota » | None, limite lue, délai demandé) d'un message d'erreur. Pur."""
    text = str(err or "")
    if not ("429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()):
        return None, None, None
    kind = "day" if "PerDay" in text else "minute" if "PerMinute" in text else "quota"
    value = re.search(r"""['"]quotaValue['"]\s*:\s*['"]?(\d+)""", text)
    delay = re.search(r"""['"]retryDelay['"]\s*:\s*['"](\d+(?:\.\d+)?)s""", text)
    return kind, int(value.group(1)) if value else None, float(delay.group(1)) if delay else None


def note_error(model: str, err: str) -> str | None:
    """Range un refus de quota. Rend son genre (day, minute, quota), ou None si ce n'en est pas un."""
    kind, value, delay = parse(err)
    if kind is None:
        return None
    m = _bare(model)
    with _lock:
        st = _load()
        if kind == "day":
            st["rest"][m] = next_reset()
            if value:
                st["limits"].setdefault(m, {})["rpd"] = value
        elif kind == "minute":
            st["rest"][m] = time.time() + (delay if delay and delay <= 120 else 60)
            if value:
                st["limits"].setdefault(m, {})["rpm"] = value
        else:
            st["rest"][m] = time.time() + (delay if delay and delay <= 300 else 300)
        st["last_error"][m] = {"kind": kind, "at": datetime.now().isoformat(timespec="seconds")}
        _save(force=True)
    return kind


def status() -> list[dict]:
    """Une ligne par modèle connu ou utilisé aujourd'hui."""
    with _lock:
        st = _load()
        names = sorted(set(st["count"]) | set(st["rest"]) | set(st["limits"]) | set(KNOWN))
        out = []
        for m in names:
            rpm, rpd = limits(m)
            used = st["count"].get(m, 0)
            until = st["rest"].get(m, 0)
            out.append({"model": m, "today": used, "per_day": rpd, "per_minute": rpm,
                        "left": None if rpd is None else max(0, rpd - used),
                        "resting_until": datetime.fromtimestamp(until).strftime("%H:%M") if until > time.time() else ""})
        return out


def summary() -> str:
    """Le tableau en phrases, pour la voix et le HUD."""
    lines = []
    for r in status():
        if not r["today"] and not r["resting_until"]:
            continue
        lim = f"/{r['per_day']}" if r["per_day"] else ""
        rest = f" — épuisé jusqu'à {r['resting_until']}" if r["resting_until"] else ""
        lines.append(f"{r['model']} : {r['today']}{lim} aujourd'hui{rest}")
    reset = datetime.fromtimestamp(next_reset()).strftime("%H:%M")
    return ("\n".join(lines) or "Aucune requête Gemini comptée aujourd'hui.") + f"\nRemise à zéro à {reset} (heure locale)."


def reset_for_tests(path: Path | None = None) -> None:
    global _state, PATH
    with _lock:
        _state = None
        _minute.clear()
        if path is not None:
            PATH = Path(path)
