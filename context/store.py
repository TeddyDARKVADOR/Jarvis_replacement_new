"""
context/store.py — the one place that holds "what is true right now".

WHY A STORE AND NOT A FUNCTION CALL
    The facts arrive by push, not by poll: the phone reports when something
    changes, from a WebSocket handler, on a thread that has nothing to do with
    whoever will read it. Something has to hold the last reading and hand out a
    consistent copy. That is all this is — a mailbox with a lock.

WHY IT ACCEPTS ANYTHING AND VALIDATES EVERYTHING
    The payload comes off a network socket from an app that ships separately
    and will be a version behind sooner or later. `update_device` therefore
    coerces field by field and drops what it does not recognise, rather than
    trusting a schema both ends are assumed to share. An older phone sending
    six fields instead of ten is a normal Tuesday, not an error — and a phone
    sending nonsense must not be able to raise inside a WebSocket handler.

WHY IT NEVER TALKS TO JARVIS
    Nothing here imports main.py, ui.py or the dashboard. The store is written
    to by whoever receives phone messages and read by whoever needs context;
    neither is named in this file. That is what makes the package deletable.
"""
from __future__ import annotations

import threading
import time

from .model import Activity, DeviceState, Ringer, Snapshot, SystemState
from .situation import Thresholds, derive, time_state


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return None


def _as_int(value, lo: int | None = None, hi: int | None = None) -> int | None:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return None
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _as_float(value, lo: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:                     # NaN
        return None
    if lo is not None:
        out = max(lo, out)
    return out


def _as_str(value, limit: int = 120) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _as_enum(enum_cls, value, default):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().upper())
    except (ValueError, AttributeError):
        return default


class ContextStore:
    """Thread-safe. Written from WebSocket handlers, read from the event loop."""

    def __init__(self, thresholds: Thresholds | None = None):
        self._lock = threading.Lock()
        self._device = DeviceState()
        self._system = SystemState()
        self._system_probe = None
        self.thresholds = thresholds or Thresholds()
        self._updates = 0

    # ── writes ───────────────────────────────────────────────────────────────

    def update_device(self, payload: dict) -> DeviceState:
        """Merge one phone report. Unknown keys are ignored; bad values are dropped.

        Merges rather than replaces, so a report carrying only a battery level
        does not erase the headset state the previous one established. The cost
        is that a field only ever goes stale with the whole record, which is
        why DeviceState.is_stale expires the record as a unit.
        """
        if not isinstance(payload, dict):
            return self.device()

        with self._lock:
            dev = self._device

            if "battery_percent" in payload:
                dev.battery_percent = _as_int(payload["battery_percent"], 0, 100)
            if "battery_charging" in payload:
                dev.battery_charging = _as_bool(payload["battery_charging"])
            if "screen_on" in payload:
                dev.screen_on = _as_bool(payload["screen_on"])
            if "idle_seconds" in payload:
                dev.idle_seconds = _as_float(payload["idle_seconds"], 0.0)
            if "headset" in payload:
                dev.headset = _as_bool(payload["headset"])
            if "headset_name" in payload:
                dev.headset_name = _as_str(payload["headset_name"])
            if "bluetooth_devices" in payload:
                raw = payload["bluetooth_devices"]
                dev.bluetooth_devices = (
                    [_as_str(n) for n in raw][:10] if isinstance(raw, (list, tuple)) else []
                )
            if "ringer" in payload:
                dev.ringer = _as_enum(Ringer, payload["ringer"], Ringer.UNKNOWN)
            if "dnd" in payload:
                dev.dnd = _as_bool(payload["dnd"])
            if "activity" in payload:
                dev.activity = _as_enum(Activity, payload["activity"], Activity.UNKNOWN)
            if "activity_confidence" in payload:
                dev.activity_confidence = _as_int(payload["activity_confidence"], 0, 100)
            if "network" in payload:
                dev.network = _as_str(payload["network"])
            if "place" in payload:
                dev.place = _as_str(payload["place"], 60)

            # Toujours l'heure locale du serveur, jamais celle annoncee par le
            # telephone : une horloge de travers sur le client rendrait les
            # donnees eternellement fraiches ou eternellement perimees.
            dev.reported_at = time.time()
            self._updates += 1
            return _copy_device(dev)

    def forget_device(self) -> None:
        """Drop everything the phone told us — call this when it disconnects.

        Not the same as letting the TTL expire: a clean disconnect is knowledge
        ("this phone is gone now"), and acting on it immediately beats five
        minutes of believing a headset is still connected.
        """
        with self._lock:
            self._device = DeviceState()

    def set_system_facts(self, facts: dict) -> None:
        with self._lock:
            self._system = SystemState(facts=dict(facts or {}))

    def bind_system_probe(self, fn) -> None:
        """Optional `() -> dict` read at snapshot time, for facts that are cheaper
        to pull than to push (uptime, whether the Live session is open)."""
        self._system_probe = fn

    # ── reads ────────────────────────────────────────────────────────────────

    def device(self) -> DeviceState:
        with self._lock:
            return _copy_device(self._device)

    def snapshot(self, now: float | None = None) -> Snapshot:
        """A full, derived reading. Never raises: a failing probe is a missing
        fact, not an exception escaping into a caller's event loop."""
        with self._lock:
            dev = _copy_device(self._device)
            facts = dict(self._system.facts)

        if self._system_probe is not None:
            try:
                extra = self._system_probe() or {}
                if isinstance(extra, dict):
                    facts.update(extra)
            except Exception as e:
                facts["probe_error"] = f"{type(e).__name__}: {e}"

        return derive(
            dev,
            tstate    = time_state(th=self.thresholds),
            sys_state = SystemState(facts=facts),
            th        = self.thresholds,
            now       = now,
        )

    def stats(self) -> dict:
        with self._lock:
            return {"updates": self._updates,
                    "age_s": round(self._device.age_seconds(), 1)}

    # ── for the prompt ───────────────────────────────────────────────────────

    def describe(self, snapshot: Snapshot | None = None) -> str:
        """A short French block to paste into a Gemini prompt.

        Facts first, opinion last, and nothing invented: a line only appears if
        the corresponding fact was actually reported. An assistant that claims
        the battery is fine because no battery was reported is worse than one
        that says nothing about the battery.
        """
        snap = snapshot or self.snapshot()
        dev, lines = snap.device, []

        lines.append(f"Heure : {snap.time.iso} ({snap.time.period})")

        if dev.reported_at:
            age = int(dev.age_seconds())
            bits = []
            if dev.battery_percent is not None:
                charge = " en charge" if dev.battery_charging else ""
                bits.append(f"batterie {dev.battery_percent} %{charge}")
            if dev.headset:
                name = f" ({dev.headset_name})" if dev.headset_name else ""
                bits.append(f"casque connecte{name}")
            elif dev.headset is False:
                bits.append("pas de casque")
            if dev.screen_on is not None:
                bits.append("ecran allume" if dev.screen_on else "ecran eteint")
            if dev.dnd:
                bits.append("Ne pas deranger actif")
            if dev.activity is not Activity.UNKNOWN:
                bits.append(f"activite {dev.activity.value}")
            if dev.place:
                bits.append(f"lieu {dev.place}")
            if dev.network:
                bits.append(f"reseau {dev.network}")
            lines.append(f"Telephone (il y a {age} s) : " + ", ".join(bits)
                         if bits else f"Telephone : connecte, aucun detail (il y a {age} s)")
        else:
            lines.append("Telephone : aucune donnee")

        if snap.system.facts:
            readable = ", ".join(f"{k}={v}" for k, v in list(snap.system.facts.items())[:8])
            lines.append(f"Systeme : {readable}")

        lines.append(f"Situation deduite : {snap.situation.value} "
                     f"(sortie audio {snap.route.value}) — "
                     + (" ; ".join(snap.reasons) or "aucun signal"))
        return "\n".join(lines)


def _copy_device(dev: DeviceState) -> DeviceState:
    """Hand out copies, never the live object.

    A caller that held the store's own DeviceState would see it mutate midway
    through its own reasoning, which is exactly the class of bug that only
    reproduces when the phone happens to report at the wrong millisecond.
    """
    return DeviceState(
        battery_percent=dev.battery_percent, battery_charging=dev.battery_charging,
        screen_on=dev.screen_on, idle_seconds=dev.idle_seconds,
        headset=dev.headset, headset_name=dev.headset_name,
        bluetooth_devices=list(dev.bluetooth_devices),
        ringer=dev.ringer, dnd=dev.dnd,
        activity=dev.activity, activity_confidence=dev.activity_confidence,
        network=dev.network, place=dev.place, reported_at=dev.reported_at,
    )
