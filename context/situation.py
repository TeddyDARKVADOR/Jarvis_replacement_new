"""
context/situation.py — turning facts into one label, with the reasoning kept.

WHY THE PRECEDENCE IS WHAT IT IS
    Several situations can look true at once, so the order they are tested in
    IS the design. The rule applied here: **a directly observed signal outranks
    an inferred one, and an inferred one outranks a user setting.**

      1. DRIVING   motion, observed by the accelerometer. Cannot be confused
                   with sleep, and being wrong costs the most (silence in a car
                   is silence in the one place voice is the only usable output).
      2. ASLEEP    never from the clock alone. Quiet hours AND a dark screen AND
                   a long idle — three signals, because a schedule on its own is
                   wrong every time the user stays up.
      3. MEETING   Do-Not-Disturb. Below sleep on purpose: a great many people
                   leave DND on all night, and a DND that outranked sleep would
                   relabel every night as a meeting.
      4. ACTIVE    screen on, or touched moments ago.
      5. IDLE      awake as far as we know, but not at the phone.

WHY THE REASONS ARE RETURNED
    A policy that can only say "I stayed quiet" is not debuggable and not
    trustworthy. Each rule that fires appends the fact that made it fire, so
    JARVIS can answer "pourquoi tu n'as pas parle ?" with "DND actif, ta
    derniere interaction remonte a 40 minutes" — and so a wrong call can be
    traced to the signal that caused it rather than guessed at.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .model import (
    Activity, DeviceState, Ringer, Route, Situation, Snapshot, SystemState, TimeState,
)


@dataclass
class Thresholds:
    """Every number the derivation uses, in one place and overridable.

    Defaults are deliberately conservative: each one errs toward "I don't know"
    rather than toward a confident wrong label.
    """
    # Au-dela, les donnees du telephone sont traitees comme absentes.
    device_ttl_s: float = 300.0

    # Heures calmes (debut inclus, fin exclue). Traversent minuit.
    quiet_start_hour: int = 23
    quiet_end_hour:   int = 7

    # Inactivite minimale avant qu'un ecran eteint pendant les heures calmes
    # puisse signifier "endormi".
    asleep_idle_s: float = 1800.0     # 30 min

    # En deca, l'utilisateur est considere aux commandes.
    active_idle_s: float = 300.0      # 5 min

    # Confiance minimale d'Android pour croire IN_VEHICLE.
    vehicle_confidence: int = 70

    # Noms Bluetooth qui trahissent une voiture quand la reconnaissance
    # d'activite est indisponible. Heuristique assumee : a completer par
    # l'utilisateur avec le nom reel de son autoradio.
    car_bluetooth_names: list[str] = field(default_factory=lambda: [
        "car", "auto", "vehicle", "carplay", "android auto",
        "sync", "uconnect", "mylink", "carkit",
    ])


def _period(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:                       # ex. 01 -> 07
        return start <= hour < end
    return hour >= start or hour < end    # traverse minuit, ex. 23 -> 07


def time_state(now: datetime | None = None, th: Thresholds | None = None) -> TimeState:
    th = th or Thresholds()
    now = now or datetime.now()
    return TimeState(
        iso         = now.isoformat(timespec="seconds"),
        hour        = now.hour,
        weekday     = now.weekday(),
        period      = _period(now.hour),
        quiet_hours = _in_quiet_hours(now.hour, th.quiet_start_hour, th.quiet_end_hour),
    )


def _looks_like_car(names: list[str], patterns: list[str]) -> str:
    """A pattern counts only as a whole word of the device name.

    A substring match took "Oscar's AirPods" for a car ("car" in "oscar"),
    and "Bose SyncBuds" too — and DRIVING outranks sleep and meetings, so the
    headset made JARVIS speak at 3 a.m. and in meetings (REG-0002). Words are
    delimited by anything that is not a letter or a digit, so "Peugeot CarKit",
    "Ford SYNC", "Android Auto" and "My Car" still match.
    """
    for name in names or []:
        low = str(name).lower()
        for pat in patterns:
            pat = str(pat).lower().strip()
            if pat and re.search(rf"(?<![^\W_]){re.escape(pat)}(?![^\W_])", low):
                return str(name)
    return ""


def derive_route(dev: DeviceState, sys_state: SystemState, stale: bool) -> Route:
    """Where audio would come out right now.

    Stale device facts fall through to the desktop rather than to the phone:
    a phone we have not heard from in five minutes is a phone that may not be
    listening, and guessing it is would send speech nowhere.
    """
    if not stale:
        if dev.headset:
            return Route.HEADSET
        if dev.reported_at:
            return Route.PHONE
    if sys_state.facts.get("desktop_audio"):
        return Route.DESKTOP
    return Route.NONE


def derive(
    dev: DeviceState,
    tstate: TimeState | None = None,
    sys_state: SystemState | None = None,
    th: Thresholds | None = None,
    now: float | None = None,
) -> Snapshot:
    """Build a full Snapshot. Pure: same inputs, same output, no I/O, never raises."""
    th        = th or Thresholds()
    sys_state = sys_state or SystemState()
    tstate    = tstate or time_state(th=th)
    now       = time.time() if now is None else now

    stale  = dev.is_stale(th.device_ttl_s, now)
    route  = derive_route(dev, sys_state, stale)
    reasons: list[str] = []

    def snap(situation: Situation) -> Snapshot:
        return Snapshot(device=dev, time=tstate, system=sys_state,
                        situation=situation, route=route,
                        reasons=reasons, taken_at=now)

    if stale:
        age = dev.age_seconds(now)
        reasons.append(
            "aucune donnee telephone" if age == float("inf")
            else f"donnees telephone perimees ({int(age)} s)"
        )
        # Pas de telephone n'est pas pas d'utilisateur.
        #
        # Le poste de travail connait sa propre presence : la derniere fois que
        # quelqu'un lui a parle. C'est le signal sur lequel la proactivite V1
        # reposait entierement, bien avant qu'un telephone existe, et l'ignorer
        # ici reviendrait a eteindre la proactivite sur toute installation sans
        # telephone. Un seul signal, donc jamais ASLEEP (aucun ecran a observer)
        # — seulement present ou absent.
        local = sys_state.facts.get("local_presence_s")
        if isinstance(local, (int, float)) and local >= 0:
            if local < th.active_idle_s:
                reasons.append(f"parle au poste il y a {int(local)} s")
                return snap(Situation.ACTIVE)
            reasons.append(f"silencieux au poste depuis {int(local // 60)} min")
            return snap(Situation.IDLE)
        return snap(Situation.UNKNOWN)

    # 1. DRIVING — observe
    if dev.activity is Activity.IN_VEHICLE and (
        dev.activity_confidence is None
        or dev.activity_confidence >= th.vehicle_confidence
    ):
        reasons.append(f"activite IN_VEHICLE (confiance {dev.activity_confidence})")
        return snap(Situation.DRIVING)

    car = _looks_like_car(dev.bluetooth_devices, th.car_bluetooth_names)
    if car:
        reasons.append(f"Bluetooth connecte a '{car}'")
        return snap(Situation.DRIVING)

    # 2. ASLEEP — trois signaux concordants, jamais l'horloge seule
    idle = dev.idle_seconds
    if (tstate.quiet_hours and dev.screen_on is False
            and idle is not None and idle >= th.asleep_idle_s):
        reasons.append(f"heures calmes, ecran eteint, inactif depuis {int(idle // 60)} min")
        return snap(Situation.ASLEEP)

    # 3. MEETING — reglage utilisateur, donc sous le sommeil
    if dev.dnd or dev.ringer is Ringer.SILENT:
        reasons.append("Ne pas deranger actif" if dev.dnd else "sonnerie en silencieux")
        return snap(Situation.MEETING)

    # 4. ACTIVE
    if dev.screen_on or (idle is not None and idle < th.active_idle_s):
        reasons.append("ecran allume" if dev.screen_on else f"interaction il y a {int(idle)} s")
        return snap(Situation.ACTIVE)

    # 5. IDLE
    reasons.append(
        f"inactif depuis {int(idle // 60)} min" if idle is not None
        else "aucun signe d'activite"
    )
    return snap(Situation.IDLE)
