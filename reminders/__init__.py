"""
reminders/ — un rappel arrive là où vous êtes : HUD, téléphone, voix.

Paquet racine au sens du contrat V2 (graphe `contrat`) : il n'importe rien de
main.py. actions/reminder.py y inscrit chaque rappel (import optionnel) et
main.py le branche par un hook d'une ligne (`attach`). Supprimer le dossier rend
les rappels identiques à avant : la tâche planifiée du système, seule.

POURQUOI
    Un rappel était une tâche planifiée Windows qui affichait une notification
    de bureau. Rien n'arrivait sur le téléphone, et JARVIS lui-même n'en savait
    rien. Tant que JARVIS tourne, c'est lui qui livre maintenant le rappel :

      1. une carte RAPPEL dans le HUD ;
      2. une notification sur le téléphone appairé (server/notify, le même
         chemin que les alertes de la veille), s'il est connecté ;
      3. la voix, si la politique de contexte permet de parler maintenant
         (réunion, sommeil… → non) ; sans context/, il parle.

    La tâche planifiée reste le filet : quand JARVIS est fermé, elle seule
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
#: Un rappel manqué de plus que ça (PC éteint) n'est plus livré par JARVIS.
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


def _to_phone(jarvis, message: str) -> bool:
    dash = getattr(jarvis, "_dashboard", None)
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


def deliver(jarvis, rid: str, r: dict) -> str:
    """Livrer un rappel par tous les chemins disponibles. Rend ce qui a marché."""
    message = r.get("message") or "Rappel"
    done = []
    # Décidé AVANT la notification : celle-ci, une fois reçue, arme le délai de
    # silence du niveau IMPORTANT (server/notify, décision N3), et la voix du même
    # rappel, décidée après, tombait dedans — un rappel n'était jamais dit dès
    # qu'un téléphone était connecté (trouvé par python -m e2e, 2026-10-14).
    speak = _may_speak()
    try:
        jarvis.ui.show_content("RAPPEL", message)
        done.append("hud")
    except Exception:
        pass
    if _to_phone(jarvis, message):
        done.append("téléphone")
    if speak and getattr(jarvis, "session", None):
        try:
            jarvis.plugin_say(
                f"[REMINDER] The reminder the user set for now is due: \"{message}\". "
                "Tell them in one short sentence, in their language. Do not call any tool.")
            done.append("voix")
        except Exception:
            pass
    try:
        jarvis.ui.write_log(f"SYS: Rappel livré ({', '.join(done) or 'rien'}) : {message[:60]}")
    except Exception:
        pass
    return ", ".join(done) or "rien"


#: La réponse de actions/reminder.py (figé) quand aucun planificateur du système
#: n'a accepté la tâche : ni schtasks, ni launchd, ni systemd-run --user, ni at.
NO_SCHEDULER = "I couldn't register the reminder with the system scheduler."


class ReminderFallback:
    """Le registre d'actions de JARVIS, à un détail près : un rappel que le
    système ne sait pas planifier est confié à JARVIS au lieu d'être refusé.

    POURQUOI (trouvé en écrivant l'e2e, 2026-10-14)
    Sur Oracle, un service systemd n'a pas de session utilisateur (systemd-run
    --user échoue) et `at` n'est pas installé : actions/reminder.py refusait donc
    tout rappel, alors que ce paquet sait le livrer tant que JARVIS tourne — et
    sur Oracle, il tourne 24 h/24. actions/ est figé (contrat V2) : on enveloppe
    le registre, comme server/routing.py, sans y toucher. Retirer reminders/
    rend le comportement d'avant, refus compris.
    """

    def __init__(self, inner, path: Path = PATH) -> None:
        self._inner = inner
        self._path = path

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def run(self, name: str, parameters: dict, ctx: dict | None = None) -> str:
        result = self._inner.run(name, parameters, ctx)
        if name != "reminder" or str(result).strip() != NO_SCHEDULER:
            return result
        p = parameters or {}
        try:
            when = datetime.strptime(f"{str(p.get('date', '')).strip()} {str(p.get('time', '')).strip()}",
                                     "%Y-%m-%d %H:%M")
        except ValueError:
            return result
        message = (str(p.get("message") or "").strip() or "Reminder")[:200]
        add(f"JARVISReminder_{when:%Y%m%d_%H%M%S}", when, message, path=self._path)
        return (f"Reminder set for {when:%B %d at %I:%M %p}. This machine has no system scheduler, "
                "so JARVIS delivers it himself (HUD, phone, voice) — as long as he is running.")


def attach(jarvis, interval: float = CHECK_EVERY_S) -> None:
    """Le hook de main.py : une boucle de fond, une seule par processus."""
    global _thread
    reg = getattr(jarvis, "_action_registry", None)
    if reg is not None and not isinstance(reg, ReminderFallback):
        jarvis._action_registry = ReminderFallback(reg)
    # Le hub des notifications retient la boucle asyncio qui l'a lié : il faut
    # donc le lier ici, depuis main.run(), et pas depuis le fil de fond. Sur
    # Oracle, run_headless l'a déjà lié ; en local, personne ne le faisait.
    dash = getattr(jarvis, "_dashboard", None)
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
                    mark_delivered(rid, deliver(jarvis, rid, r))
            except Exception as exc:  # une boucle de fond ne meurt pas
                print(f"[Reminders] {exc}")
            time.sleep(interval)

    _thread = threading.Thread(target=loop, name="reminders", daemon=True)
    _thread.start()
