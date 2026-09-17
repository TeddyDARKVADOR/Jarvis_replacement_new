"""
context/ — what JARVIS knows about your situation, and what it may do about it.

    from context import get_store, get_policy
    get_store().update_device({"battery_percent": 14, "headset": True})
    snap = get_store().snapshot()
    decision = get_policy().decide(Priority.IMPORTANT, snap)

FOUR FILES, FOUR JOBS
    model.py      le vocabulaire      — enums et dataclasses, zero logique
    situation.py  la deduction        — faits bruts -> une situation, pure et testable
    store.py      la memoire courante — thread-safe, ce que le telephone a dit
    policy.py     la decision         — priorite x situation -> canal de sortie

WHY THE SINGLETONS ARE HERE AND NOT IN THE CALLERS
    Two unrelated places need the same store: whoever receives the phone's
    reports, and whoever decides to speak. Passing one object between them
    would mean threading a parameter through the core — the exact edit the V2
    contract exists to avoid. A module-level accessor costs one import at each
    end and nothing in between.

REMOVING THIS PACKAGE
    Delete the directory. Nothing in main.py, ui.py, core/ or dashboard/ imports
    it except through guarded optional imports that fall back to V1 behaviour.
    See docs/V2_CONTRACT.md, section 4.
"""
from __future__ import annotations

import threading

from .model import (
    Activity, Channel, Decision, DeviceState, Priority, Ringer, Route,
    Situation, Snapshot, SystemState, TimeState,
)
from .policy import DeferralQueue, Deferred, PolicyConfig, ProactivityPolicy
from .situation import Thresholds, derive, derive_route, time_state
from .store import ContextStore

__all__ = [
    "Activity", "Channel", "Decision", "DeviceState", "Priority", "Ringer",
    "Route", "Situation", "Snapshot", "SystemState", "TimeState",
    "ContextStore", "ProactivityPolicy", "PolicyConfig",
    "DeferralQueue", "Deferred", "Thresholds",
    "derive", "derive_route", "time_state",
    "get_store", "get_policy", "get_queue", "reset",
]

_lock = threading.Lock()
_store: ContextStore | None = None
_policy: ProactivityPolicy | None = None
_queue: DeferralQueue | None = None


def _thresholds_from_config() -> Thresholds:
    """Read the user's quiet hours if the config layer is available.

    Optional on purpose: context/ must import and run inside a bare `python -c`
    with no config file, no API key and no JARVIS around it, or it cannot be
    tested on its own — which is the requirement that keeps it removable.
    """
    th = Thresholds()
    try:
        from memory.config_manager import get_plugin_config
        values = get_plugin_config("context") or {}
    except Exception:
        return th
    for key in ("device_ttl_s", "asleep_idle_s", "active_idle_s"):
        if key in values:
            try:
                setattr(th, key, float(values[key]))
            except (TypeError, ValueError):
                pass
    for key in ("quiet_start_hour", "quiet_end_hour", "vehicle_confidence"):
        if key in values:
            try:
                setattr(th, key, int(values[key]))
            except (TypeError, ValueError):
                pass
    extra = values.get("car_bluetooth_names")
    if isinstance(extra, str) and extra.strip():
        th.car_bluetooth_names += [n.strip().lower() for n in extra.split(",") if n.strip()]
    elif isinstance(extra, (list, tuple)):
        th.car_bluetooth_names += [str(n).strip().lower() for n in extra if str(n).strip()]
    return th


def get_store() -> ContextStore:
    global _store
    with _lock:
        if _store is None:
            _store = ContextStore(thresholds=_thresholds_from_config())
        return _store


def get_policy() -> ProactivityPolicy:
    global _policy
    with _lock:
        if _policy is None:
            _policy = ProactivityPolicy()
        return _policy


def get_queue() -> DeferralQueue:
    global _queue
    with _lock:
        if _queue is None:
            _queue = DeferralQueue()
        return _queue


def reset() -> None:
    """Drop the singletons — for tests, and for re-reading config after a change."""
    global _store, _policy, _queue
    with _lock:
        _store = _policy = _queue = None
