"""
context/model.py — the vocabulary. No logic, no I/O, no imports from JARVIS.

WHY THE MODEL IS ITS OWN FILE
    Three things need to agree on what "the user is driving" means: the phone
    that observes it, the rule that derives it, and the policy that acts on it.
    If each carried its own strings they would drift, and the drift would only
    show up as JARVIS talking out loud in a meeting. One vocabulary, imported
    by all three.

WHY RAW FACTS AND DERIVED SITUATION ARE SEPARATE TYPES
    `DeviceState` is what the phone can actually see: a battery percentage, a
    Bluetooth name, a screen that is on or off. `Situation` is an opinion about
    what those add up to. Keeping them apart means the inference can be wrong,
    be corrected, and be tested, without touching what the phone reports — and
    it means JARVIS can always fall back to stating the fact ("your headset is
    connected") when the opinion is unavailable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum


class Priority(str, Enum):
    """How much a proactive message is worth interrupting for.

    str-valued so it serialises to JSON with no custom encoder — same reason
    server/runtime_state.State is.
    """
    CRITICAL  = "CRITICAL"    # agir maintenant : sécurité, argent, perte de données
    IMPORTANT = "IMPORTANT"   # mérite d'être vu dans l'heure
    USEFUL    = "USEFUL"      # bon à savoir, aucune urgence
    TRIVIAL   = "TRIVIAL"     # n'aurait pas dû remonter


class Channel(str, Enum):
    """How a message reaches the user, once the policy has had its say."""
    INTERRUPT     = "INTERRUPT"       # couper ce qui joue et parler
    VOICE         = "VOICE"           # parler au prochain silence
    NOTIFY        = "NOTIFY"          # notification qui sonne / vibre
    NOTIFY_SILENT = "NOTIFY_SILENT"   # notification muette
    DEFER         = "DEFER"           # garder pour le prochain moment opportun
    DROP          = "DROP"            # ne rien faire, ne rien garder


class Route(str, Enum):
    """Where audio goes when the channel is VOICE or INTERRUPT.

    Deliberately NOT part of Channel: "faut-il parler" and "dans quoi parler"
    are answered by different facts, and merging them would make a headset
    change look like a priority change.
    """
    HEADSET = "HEADSET"   # casque filaire ou Bluetooth
    PHONE   = "PHONE"     # haut-parleur du téléphone
    DESKTOP = "DESKTOP"   # enceintes du PC
    NONE    = "NONE"      # aucune sortie audio disponible


class Situation(str, Enum):
    """The one label the policy reasons about."""
    DRIVING = "DRIVING"
    MEETING = "MEETING"
    ASLEEP  = "ASLEEP"
    ACTIVE  = "ACTIVE"    # écran allumé récemment, utilisateur aux commandes
    IDLE    = "IDLE"      # éveillé mais absent
    UNKNOWN = "UNKNOWN"   # aucune donnée fraîche — voir DeviceState.is_stale


class Activity(str, Enum):
    """Mirrors Android's ActivityRecognition vocabulary, trimmed to what we use."""
    STILL      = "STILL"
    WALKING    = "WALKING"
    RUNNING    = "RUNNING"
    CYCLING    = "CYCLING"
    IN_VEHICLE = "IN_VEHICLE"
    UNKNOWN    = "UNKNOWN"


class Ringer(str, Enum):
    NORMAL  = "NORMAL"
    VIBRATE = "VIBRATE"
    SILENT  = "SILENT"
    UNKNOWN = "UNKNOWN"


# ── raw facts ────────────────────────────────────────────────────────────────

@dataclass
class DeviceState:
    """What the phone reported, last time it reported.

    Every field is optional and every field has a "don't know" value, because
    a phone that has not granted a permission is the normal case, not an error.
    Nothing here is ever inferred — see situation.py for that.
    """
    battery_percent:  int | None = None
    battery_charging: bool | None = None

    screen_on:        bool | None = None
    # Seconds since the user last touched the phone. The single most useful
    # sleep signal there is, and the one a schedule alone cannot provide.
    idle_seconds:     float | None = None

    headset:          bool | None = None
    headset_name:     str = ""
    bluetooth_devices: list[str] = field(default_factory=list)

    ringer:           Ringer = Ringer.UNKNOWN
    dnd:              bool | None = None

    activity:         Activity = Activity.UNKNOWN
    activity_confidence: int | None = None   # 0-100, Android's own number

    network:          str = ""        # "wifi:<ssid>" | "cellular" | "offline"
    place:            str = ""        # étiquette libre ("maison", "bureau")

    # When the phone said this, in wall-clock seconds. 0.0 = jamais reçu.
    reported_at:      float = 0.0

    def age_seconds(self, now: float | None = None) -> float:
        if not self.reported_at:
            return float("inf")
        return max(0.0, (now if now is not None else time.time()) - self.reported_at)

    def is_stale(self, ttl: float, now: float | None = None) -> bool:
        """Stale facts are worse than no facts.

        A phone that drops off Wi-Fi stops sending, and the last thing it said
        stays true forever unless something expires it. "Headset connected",
        believed an hour after the headset was unplugged, is exactly how an
        assistant starts talking into a room it thinks is empty.
        """
        return self.age_seconds(now) > ttl


@dataclass
class TimeState:
    """Derived from the clock alone. Always available, never stale."""
    iso:         str = ""
    hour:        int = 0
    weekday:     int = 0        # 0 = lundi
    period:      str = ""       # morning | afternoon | evening | night
    quiet_hours: bool = False


@dataclass
class SystemState:
    """Whatever the host wants to contribute — a free dict on purpose.

    Typed fields here would mean context/ has an opinion about what the server
    runs, and the server would then have to be edited every time it grows a new
    service. It hands over what it has; nothing in context/ requires any of it.
    """
    facts: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    """One coherent reading of the world, taken at one instant."""
    device:    DeviceState = field(default_factory=DeviceState)
    time:      TimeState   = field(default_factory=TimeState)
    system:    SystemState = field(default_factory=SystemState)
    situation: Situation   = Situation.UNKNOWN
    route:     Route       = Route.NONE
    # Human-readable justification for `situation`, in order of weight. This is
    # what lets JARVIS answer "pourquoi tu n'as pas parlé ?" with a reason
    # instead of a shrug.
    reasons:   list[str]   = field(default_factory=list)
    taken_at:  float       = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    """The policy's answer. Inert: deciding and delivering are separate jobs."""
    channel:  Channel
    route:    Route
    priority: Priority
    reason:   str = ""
    situation: Situation = Situation.UNKNOWN

    @property
    def speaks(self) -> bool:
        return self.channel in (Channel.VOICE, Channel.INTERRUPT)

    @property
    def silent(self) -> bool:
        return self.channel in (Channel.DROP, Channel.DEFER)

    def to_dict(self) -> dict:
        return asdict(self)
