"""
Who this machine is, and what it can honestly claim to do.

A capability is a promise. `targeting.py` routes on it: a device that declares
`computer_control` will be sent mouse and keyboard commands, and if it cannot
actually run them the user gets a failure on the machine they did not choose,
for a reason nothing on screen explains. So nothing here is asserted — every
capability is *proven* before it is declared:

```
   actions/*.py  --discover_actions()-->  the action really imported
                                                    |
                        + its device-bound?  --------+
                        + its dependency importable? |
                                                     v
                                              declared capability
```

**The action registry is reused, not reimplemented.** `core.action_loader` is
the same discovery `main.py` runs; pointing it at the same `actions/` directory
produces the same records, so a capability name is an action name by
construction and cannot drift from one.

**Only device-bound actions are declared.** `web_search`, `weather_report` and
`flight_finder` run perfectly well on the VPS and mean nothing in particular on
this laptop; declaring them would invent an ambiguity ("web_search: the PC or
the phone?") where none exists. What belongs here is what acts on *this*
computer: its applications, its windows, its keyboard, its screen, its files.
"""

from __future__ import annotations

import importlib.util
import platform
import socket
import sys
import threading
import uuid
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEVICE_TYPE = "pc"
PROTOCOL_VERSION = 1

#: Actions that act on *this* machine, and the import each one truly needs.
#:
#: The five pyautogui actions guard their import in a try/except and raise only
#: when called, so `discover_actions` happily loads them on a machine with no
#: pyautogui at all. Loading is therefore not evidence of anything, and this
#: table is what turns "the file imported" into "the action will work".
#:
#: An empty tuple means the standard library is enough.
DEVICE_BOUND_ACTIONS: dict[str, tuple[str, ...]] = {
    "open_app":          (),                 # subprocess / os.startfile
    "computer_control":  ("pyautogui",),     # this keyboard, this mouse
    "computer_settings": ("comtypes",),      # this machine's volume, power, wifi
    "desktop_control":   ("pyautogui",),     # this machine's windows
    "send_message":      ("pyautogui",),     # types into an app running here
    "youtube_video":     ("pyautogui",),     # plays on this screen
    "browser_control":   ("playwright",),    # drives a browser on this machine
    "file_controller":   (),                 # this filesystem
    "file_processor":    (),                 # this filesystem
}


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def default_display_name() -> str:
    try:
        return socket.gethostname() or platform.node() or "PC"
    except Exception:
        return "PC"


def new_device_id() -> str:
    """A stable logical id, generated once and then kept in the settings file.

    Not the hostname: two machines can share one, and renaming a laptop would
    silently create a second device with the first one's capabilities still
    registered. Not the MAC or a hardware id either — those are identifiers the
    user never agreed to send anywhere.
    """
    return f"pc-{uuid.uuid4().hex[:12]}"


def discover_capabilities(logger: Callable[[str], None] = print) -> list[str]:
    """The device-bound actions this machine can actually run, proven.

    Never raises: a client that cannot enumerate its abilities must still start
    and connect, it simply declares nothing and receives nothing routed.
    """
    try:
        from core.action_loader import discover_actions
    except Exception as exc:
        logger(f"Capacités : action_loader indisponible — {exc}")
        return []

    try:
        registry = discover_actions(
            _REPO_ROOT / "actions",
            logger=lambda _line: None,   # its own log belongs to the server
        )
        loaded = registry.names()
    except Exception as exc:
        logger(f"Capacités : découverte impossible — {exc}")
        return []

    capabilities: list[str] = []
    missing: list[str] = []
    for action, requirements in sorted(DEVICE_BOUND_ACTIONS.items()):
        if action not in loaded:
            continue
        absent = [module for module in requirements if not _importable(module)]
        if absent:
            missing.append(f"{action} (manque {', '.join(absent)})")
            continue
        capabilities.append(action)

    logger(f"Capacités déclarées : {len(capabilities)} — {', '.join(capabilities)}")
    if missing:
        logger(f"Capacités écartées : {'; '.join(missing)}")
    return capabilities


class CapabilityProbe:
    """Runs the discovery off the GUI thread and remembers the answer.

    Importing sixteen action modules pulls in pyautogui, playwright and OpenCV.
    That is seconds of work, and doing it on the Qt thread means the panel does
    not paint until it finishes — at login, on the frame the user is watching.
    """

    def __init__(self, logger: Callable[[str], None] = print) -> None:
        self._logger = logger
        self._capabilities: list[str] | None = None
        self._thread: threading.Thread | None = None
        self._done: Callable[[list[str]], None] | None = None

    @property
    def capabilities(self) -> list[str] | None:
        return self._capabilities

    def start(self, on_done: Callable[[list[str]], None]) -> None:
        if self._capabilities is not None:
            on_done(self._capabilities)
            return
        if self._thread is not None:
            return
        self._done = on_done

        def work() -> None:
            found = discover_capabilities(self._logger)
            self._capabilities = found
            callback = self._done
            if callback is not None:
                try:
                    callback(found)
                except Exception:
                    pass

        self._thread = threading.Thread(
            target=work, name="JarvisCapabilities", daemon=True
        )
        self._thread.start()


class LocalExecutor:
    """Runs a routed action here, using the project's own action registry.

    **Nothing is copied.** The registry built here is the same
    `core.action_loader` product `main.py` builds, over the same files, so an
    action behaves identically whether Oracle ran it or this client did.

    **A command addressed elsewhere is refused.** That is the second barrier the
    brief asks for: the server already resolved a target, and this check exists
    for the case where that resolution was wrong or a message was misdelivered.
    Two independent refusals are what make a wrong-device execution require two
    simultaneous faults rather than one.
    """

    def __init__(self, device_id: str, allowed: set[str],
                 logger: Callable[[str], None] = print) -> None:
        self.device_id = device_id
        self._allowed = set(allowed)
        self._logger = logger
        self._registry = None
        self._lock = threading.Lock()

    def _ensure_registry(self):
        with self._lock:
            if self._registry is None:
                from core.action_loader import discover_actions

                self._registry = discover_actions(
                    _REPO_ROOT / "actions", logger=lambda _l: None
                )
            return self._registry

    def execute(self, action: str, parameters: dict,
                target_device_id: str = "") -> str:
        """Returns whatever the action returns, or why it was refused."""
        if target_device_id and target_device_id != self.device_id:
            self._logger(
                f"Commande refusée : destinée à {target_device_id}, "
                f"nous sommes {self.device_id}."
            )
            return (
                f"Refusé : cette commande est destinée à {target_device_id}, "
                f"pas à cet appareil."
            )

        if action not in self._allowed:
            # Declared capabilities are the contract. Running something outside
            # them would mean the registry on Oracle and the one here disagree,
            # and the safe reading of that is to do nothing.
            self._logger(f"Commande refusée : « {action} » n'est pas déclarée ici.")
            return f"Refusé : « {action} » ne fait pas partie des capacités de cet appareil."

        try:
            registry = self._ensure_registry()
        except Exception as exc:
            return f"Registre d'actions indisponible : {exc}"

        if not registry.has(action):
            return f"L'action « {action} » n'existe pas sur cet appareil."

        self._logger(f"Exécution locale : {action} {parameters}")
        try:
            return registry.run(action, dict(parameters or {}), {})
        except Exception as exc:
            return f"L'action « {action} » a échoué : {exc}"
