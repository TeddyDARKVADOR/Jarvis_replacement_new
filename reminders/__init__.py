"""
reminders/ — un rappel arrive là où vous êtes : HUD, téléphone, voix.

Paquet racine au sens du contrat V2 (graphe `contrat`) : il n'importe rien de
main.py. actions/reminder.py y inscrit chaque rappel (import optionnel) et
main.py le branche par un hook d'une ligne (`attach`). Supprimer le dossier rend
les rappels identiques à avant : la tâche planifiée du système, seule.

POURQUOI
    Un rappel était une tâche planifiée Windows qui affichait une notification
    de bureau. Rien n'arrivait sur le téléphone, et Matthew lui-même n'en savait
    rien. Tant que Matthew tourne, c'est lui qui livre maintenant le rappel :

      1. une carte RAPPEL dans le HUD ;
      2. une notification sur le téléphone appairé (server/notify, le même
         chemin que les alertes de la veille), s'il est connecté ;
      3. la voix, si la politique de contexte permet de parler maintenant
         (réunion, sommeil… → non) ; sans context/, il parle.

    La tâche planifiée reste le filet : quand Matthew est fermé, elle seule
    prévient. Quand il tourne, elle attend 20 s, voit le rappel marqué livré
    dans memory/reminders.json et se tait : pas de doublon.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "memory" / "reminders.json"
#: Un rappel manqué de plus que ça (PC éteint) n'est plus livré par Matthew.
STALE = timedelta(hours=6)
CHECK_EVERY_S = 5.0
_LOCK = threading.Lock()
_thread: threading.Thread | None = None


def _load(path: Path = PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict, path: Path = PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def add(rid: str, due: datetime, message: str, path: Path = PATH) -> None:
    """Inscrire un rappel. `rid` est le nom de la tâche planifiée."""
    with _LOCK:
        data = _load(path)
        data[rid] = {"due": due.isoformat(timespec="seconds"), "message": message,
                     "created": datetime.now().isoformat(timespec="seconds"), "delivered": None}
        # on ne garde pas l'historique indéfiniment
        horizon = (datetime.now() - timedelta(days=30)).isoformat()
        data = {k: v for k, v in data.items() if v.get("due", "") >= horizon or not v.get("delivered")}
        _save(data, path)


def due(now: datetime | None = None, path: Path = PATH) -> list[tuple[str, dict]]:
    """Les rappels à livrer maintenant : échus, pas livrés, pas trop vieux."""
    now = now or datetime.now()
    out = []
    for rid, r in _load(path).items():
        try:
            when = datetime.fromisoformat(r["due"])
        except Exception:
            continue
        if not r.get("delivered") and when <= now and now - when <= STALE:
            out.append((rid, r))
    return sorted(out, key=lambda item: item[1]["due"])


def mark_delivered(rid: str, how: str, path: Path = PATH) -> None:
    with _LOCK:
        data = _load(path)
        if rid in data:
            data[rid]["delivered"] = f"{datetime.now().isoformat(timespec='seconds')} {how}"
            _save(data, path)


def upcoming(path: Path = PATH) -> list[tuple[str, dict]]:
    now = datetime.now().isoformat()
    return sorted(((k, v) for k, v in _load(path).items()
                   if not v.get("delivered") and v.get("due", "") > now),
                  key=lambda item: item[1]["due"])


# ── livraison ────────────────────────────────────────────────────────────────

def _may_speak() -> bool:
    try:
        from context import Priority, get_policy, get_store
        return bool(get_policy().decide(Priority.IMPORTANT, get_store().snapshot()).speaks)
    except ImportError:
        return True
    except Exception:
        return True


def _to_phone(matthew_assistant, message: str) -> bool:
    dash = getattr(matthew_assistant, "_dashboard", None)
    if dash is None:
        return False
    try:
        from server.notify import get_hub
    except ImportError:
        return False
    hub = get_hub()
    if getattr(hub, "_dashboard", None) is None:
        return False          # pas lié : voir attach(), qui le lie depuis la boucle
    try:
        return hub.notify("IMPORTANT", "Rappel", message) is not None
    except Exception:
        return False


def deliver(matthew_assistant, rid: str, r: dict) -> str:
    """Livrer un rappel par tous les chemins disponibles. Rend ce qui a marché."""
    message = r.get("message") or "Rappel"
    done = []
    try:
        matthew_assistant.ui.show_content("RAPPEL", message)
        done.append("hud")
    except Exception:
        pass
    if _to_phone(matthew_assistant, message):
        done.append("téléphone")
    if _may_speak() and getattr(matthew_assistant, "session", None):
        try:
            matthew_assistant.plugin_say(
                f"[REMINDER] The reminder the user set for now is due: \"{message}\". "
                "Tell them in one short sentence, in their language. Do not call any tool.")
            done.append("voix")
        except Exception:
            pass
    try:
        matthew_assistant.ui.write_log(f"SYS: Rappel livré ({', '.join(done) or 'rien'}) : {message[:60]}")
    except Exception:
        pass
    return ", ".join(done) or "rien"


def attach(matthew_assistant, interval: float = CHECK_EVERY_S) -> None:
    """Le hook de main.py : une boucle de fond, une seule par processus."""
    global _thread
    # Le hub des notifications retient la boucle asyncio qui l'a lié : il faut
    # donc le lier ici, depuis main.run(), et pas depuis le fil de fond. Sur
    # Oracle, run_headless l'a déjà lié ; en local, personne ne le faisait.
    dash = getattr(matthew_assistant, "_dashboard", None)
    if dash is not None:
        try:
            from server.notify import get_hub
            hub = get_hub()
            if getattr(hub, "_dashboard", None) is None:
                hub.bind_dashboard(dash)
        except Exception:
            pass
    if _thread is not None and _thread.is_alive():
        return

    def loop() -> None:
        while True:
            try:
                for rid, r in due():
                    mark_delivered(rid, deliver(matthew_assistant, rid, r))
            except Exception as exc:  # une boucle de fond ne meurt pas
                print(f"[Reminders] {exc}")
            time.sleep(interval)

    _thread = threading.Thread(target=loop, name="reminders", daemon=True)
    _thread.start()
