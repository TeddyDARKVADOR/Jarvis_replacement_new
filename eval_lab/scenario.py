"""
eval_lab/scenario.py — one situation, in one shape, for every layer of JARVIS.

WHY ONE FORMAT FOR SEVERAL LAYERS
    The layers under test do not share a vocabulary: context/ speaks in
    Situation and Channel, server/targeting in DeviceInfo and Resolution,
    avatar/ in intents and blendshapes. A scenario format per layer would make
    every tool of the lab — generator, mutator, minimiser, clusterer — exist
    once per layer. So the envelope is shared and only two blocks vary:

        world      what is true before anything happens (time, phone, devices…)
        stimulus   what happens (a priority to decide, a capability to route…)

    `surface` says which layer the stimulus is aimed at, and therefore which
    part of `world` is read. The rest of the lab never looks inside either.

WHY THE ID IS A HASH
    Two scenarios that describe the same situation are the same scenario,
    whatever produced them. The id is derived from the semantic part only
    (surface, world, stimulus, events) — never from lineage or notes — so a
    generator that rediscovers a known case collides with it instead of
    inflating the corpus. Deduplication is not a pass; it is the key.

WHY `expected` IS A LIST OF PATHS AND NOT A GOLDEN TRACE
    A golden trace breaks on every harmless change (a reason string reworded,
    a new field in a result). An oracle states only what it actually cares
    about: `{"channel": "DEFER"}`, `{"steps.2.channel": "VOICE"}`. Everything
    it does not mention is free to change.

Nothing here imports JARVIS. The vocabularies below are mirrored on purpose
and a check in eval_lab/selftest.py fails the day they drift from the code.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

SCHEMA = "jarvis.scenario/1"

SURFACES = ("situation", "policy", "sequence", "routing", "face", "router")
# The tier a surface runs at. `router` needs a live loop and threads: FULL.
SURFACE_TIER = {"router": "full"}
SOURCES = ("legacy", "human", "generated", "mutated", "boundary", "adversarial", "llm", "regression")
TIERS = ("fast", "full", "real")

# Mirrors of the code's own enums. Checked against the code by the selftest.
PRIORITIES = ("CRITICAL", "IMPORTANT", "USEFUL", "TRIVIAL")
SITUATIONS = ("DRIVING", "MEETING", "ASLEEP", "ACTIVE", "IDLE", "UNKNOWN")
ROUTES = ("HEADSET", "PHONE", "DESKTOP", "NONE")
CHANNELS = ("INTERRUPT", "VOICE", "NOTIFY", "NOTIFY_SILENT", "DEFER", "DROP")
ACTIVITIES = ("STILL", "WALKING", "RUNNING", "CYCLING", "IN_VEHICLE", "UNKNOWN")
RINGERS = ("NORMAL", "VIBRATE", "SILENT", "UNKNOWN")
DEVICE_TYPES = ("pc", "android", "unknown")
FACE_STATES = ("LISTENING", "THINKING", "SPEAKING", "CONFIRM")
FACE_GAZES = (None, "user", "screen")

# The operations a `sequence` may chain. Each is interpreted by
# eval_lab/surfaces.py against the real context/ objects.
SEQUENCE_OPS = ("world", "advance", "decide", "deliver", "push", "release", "alerts")

PHONE_FIELDS = {
    "battery_percent": (int, type(None)), "battery_charging": (bool, type(None)),
    "screen_on": (bool, type(None)), "idle_seconds": (int, float, type(None)),
    "headset": (bool, type(None)), "headset_name": (str,),
    "bluetooth_devices": (list,), "ringer": (str,), "dnd": (bool, type(None)),
    "activity": (str,), "activity_confidence": (int, type(None)),
    "network": (str,), "place": (str,),
    # Seconds between the report and "now". None = the phone never reported.
    "age_s": (int, float, type(None)),
}

DEFAULT_TIME = "2026-09-23T14:00:00"


# ── identity ─────────────────────────────────────────────────────────────────

def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def semantic_key(s: dict) -> dict:
    """The part of a scenario that decides what JARVIS is confronted with."""
    return {
        "surface": s.get("surface"),
        "world": s.get("world", {}),
        "stimulus": s.get("stimulus", {}),
        "events": s.get("events", []),
    }


def fingerprint(s: dict) -> str:
    return hashlib.sha256(_canonical_json(semantic_key(s)).encode("utf-8")).hexdigest()


def scenario_id(s: dict) -> str:
    return f"{s.get('surface', 'x')}-{fingerprint(s)[:12]}"


def make(surface: str, *, world: dict | None = None, stimulus: dict | None = None,
         events: list | None = None, expected: dict | None = None,
         forbidden: dict | None = None, properties: list | None = None,
         source: str = "generated", family: str = "", difficulty: int = 1,
         tier: str = "fast", seed: int = 0, lineage: dict | None = None,
         oracle: str = "explicit", notes: str = "") -> dict:
    """Build a canonical scenario and stamp its id. The only constructor."""
    s = {
        "schema": SCHEMA,
        "id": "",
        "surface": surface,
        "source": source,
        "family": family or surface,
        "difficulty": int(difficulty),
        "tier": tier,
        "seed": int(seed),
        "world": copy.deepcopy(world or {}),
        "stimulus": copy.deepcopy(stimulus or {}),
        "events": copy.deepcopy(events or []),
        "expected": copy.deepcopy(expected or {}),
        "forbidden": copy.deepcopy(forbidden or {}),
        "properties": list(properties or []),
        # explicit : the oracle states a value.
        # implicit : the oracle is "no invariant is violated" — nothing else.
        "oracle": oracle,
        "lineage": {"parent": None, "mutations": [], "generator": None,
                    "legacy_ref": None, **(lineage or {})},
        "notes": notes,
    }
    if not s["events"]:
        del s["events"]
    s["id"] = scenario_id(s)
    return s


def restamp(s: dict) -> dict:
    """Recompute the id after an edit. A mutated scenario is a new scenario."""
    s["id"] = scenario_id(s)
    return s


# ── validation ───────────────────────────────────────────────────────────────

class Invalid(ValueError):
    """A scenario the lab refuses to run. Never a verdict about JARVIS."""


def _need(cond: bool, msg: str, errors: list) -> None:
    if not cond:
        errors.append(msg)


def _check_time(world: dict, errors: list) -> None:
    raw = (world.get("time") or {}).get("local", DEFAULT_TIME)
    try:
        datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        errors.append(f"world.time.local illisible : {raw!r}")


def _check_phone(phone, errors: list, where: str = "world.phone") -> None:
    if phone is None:
        return
    if not isinstance(phone, dict):
        errors.append(f"{where} doit etre un objet")
        return
    for key, value in phone.items():
        if key not in PHONE_FIELDS:
            errors.append(f"{where}.{key} inconnu")
            continue
        if not isinstance(value, PHONE_FIELDS[key]) or (
                isinstance(value, bool) and bool not in PHONE_FIELDS[key]):
            errors.append(f"{where}.{key} : type {type(value).__name__} refuse")
    if phone.get("ringer", "UNKNOWN") not in RINGERS:
        errors.append(f"{where}.ringer hors vocabulaire")
    if phone.get("activity", "UNKNOWN") not in ACTIVITIES:
        errors.append(f"{where}.activity hors vocabulaire")
    age = phone.get("age_s")
    if isinstance(age, (int, float)) and age < 0:
        errors.append(f"{where}.age_s negatif : un rapport venu du futur")
    idle = phone.get("idle_seconds")
    if isinstance(idle, (int, float)) and idle < 0:
        errors.append(f"{where}.idle_seconds negatif")


def _check_force(force: dict | None, errors: list) -> None:
    if not force:
        return
    if force.get("situation") not in (None, *SITUATIONS):
        errors.append("world.force.situation hors vocabulaire")
    if force.get("route") not in (None, *ROUTES):
        errors.append("world.force.route hors vocabulaire")


def _check_context_world(world: dict, errors: list) -> None:
    _check_time(world, errors)
    _check_phone(world.get("phone"), errors)
    _check_force(world.get("force"), errors)
    history = world.get("delivered", [])
    for i, h in enumerate(history):
        if h.get("priority") not in PRIORITIES:
            errors.append(f"world.delivered[{i}].priority hors vocabulaire")
        if not isinstance(h.get("ago_s"), (int, float)) or h["ago_s"] < 0:
            errors.append(f"world.delivered[{i}].ago_s invalide")


def _check_devices(world: dict, errors: list) -> None:
    devices = world.get("devices", [])
    ids = [d.get("id") for d in devices]
    _need(len(ids) == len(set(ids)), "world.devices : identifiants en double", errors)
    for d in devices:
        _need(bool(d.get("id")), "world.devices : appareil sans id", errors)
        _need(d.get("type", "unknown") in DEVICE_TYPES,
              f"world.devices[{d.get('id')}].type hors vocabulaire", errors)
        _need(isinstance(d.get("caps", []), list),
              f"world.devices[{d.get('id')}].caps doit etre une liste", errors)
    origin = (world.get("turn") or {}).get("origin")
    _need(origin is None or origin in ids,
          f"world.turn.origin '{origin}' ne designe aucun appareil", errors)


def validate(s: dict) -> dict:
    """Structural then semantic validation. Raises Invalid with every problem.

    Structural: the envelope is well-formed. Semantic: the situation it
    describes could exist — an origin that names a device that is not there,
    a phone that reported from the future, an intent the face does not know.
    A scenario that fails here is the lab's problem, never JARVIS's.
    """
    errors: list[str] = []
    if not isinstance(s, dict):
        raise Invalid(["le scenario n'est pas un objet"])

    _need(s.get("schema") == SCHEMA, f"schema != {SCHEMA}", errors)
    surface = s.get("surface")
    _need(surface in SURFACES, f"surface inconnue : {surface!r}", errors)
    _need(s.get("source") in SOURCES, f"source inconnue : {s.get('source')!r}", errors)
    _need(s.get("tier") in TIERS, f"tier inconnu : {s.get('tier')!r}", errors)
    _need(s.get("oracle") in ("explicit", "implicit"), "oracle doit etre explicit|implicit", errors)
    for key in ("world", "stimulus", "expected", "forbidden"):
        _need(isinstance(s.get(key), dict), f"{key} doit etre un objet", errors)
    _need(isinstance(s.get("properties", []), list), "properties doit etre une liste", errors)
    # REAL is physical hardware. Only a person (or a promoted regression) puts a
    # scenario there; nothing generated may ever reach a real device.
    if s.get("tier") == "real" and s.get("source") not in ("human", "regression"):
        errors.append("tier real reserve aux scenarios humains ou de regression")
    if s.get("tier") != "real" and surface in SURFACE_TIER and s.get("tier") != SURFACE_TIER[surface]:
        errors.append(f"la surface {surface} tourne au niveau {SURFACE_TIER[surface]}")
    if s.get("oracle") == "explicit":
        _need(bool(s.get("expected")) or bool(s.get("forbidden")),
              "oracle explicite sans rien d'attendu ni d'interdit", errors)
    if errors:
        raise Invalid(errors)

    world, stim = s["world"], s["stimulus"]
    if surface == "situation":
        _check_context_world(world, errors)
    elif surface == "policy":
        _check_context_world(world, errors)
        _need(stim.get("priority") in PRIORITIES, "stimulus.priority hors vocabulaire", errors)
    elif surface == "sequence":
        _check_context_world(world, errors)
        events = s.get("events") or []
        _need(bool(events), "une sequence sans evenement", errors)
        for i, ev in enumerate(events):
            op = ev.get("op")
            if op not in SEQUENCE_OPS:
                errors.append(f"events[{i}].op inconnu : {op!r}")
            if op in ("decide", "deliver", "push") and ev.get("priority") not in PRIORITIES:
                errors.append(f"events[{i}].priority hors vocabulaire")
            if op == "advance" and not (isinstance(ev.get("s"), (int, float)) and ev["s"] >= 0):
                errors.append(f"events[{i}] : on n'avance pas le temps a reculons")
            if op == "world":
                _check_phone(ev.get("phone"), errors, f"events[{i}].phone")
                _check_force(ev.get("force"), errors)
                if "time" in ev:
                    _check_time({"time": ev["time"]}, errors)
                if "at" in ev and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(ev["at"])):
                    errors.append(f"events[{i}].at doit etre HH:MM")
    elif surface == "routing":
        _check_devices(world, errors)
        _need(isinstance(stim.get("text", ""), str), "stimulus.text doit etre une chaine", errors)
        _need(isinstance(stim.get("capability", ""), str), "stimulus.capability doit etre une chaine", errors)
    elif surface == "router":
        _check_devices(world, errors)
        _need(isinstance(stim.get("tool"), str) and bool(stim.get("tool")), "stimulus.tool requis", errors)
        _need(isinstance(stim.get("parameters", {}), dict), "stimulus.parameters doit etre un objet", errors)
        ago = (world.get("turn") or {}).get("ago_s", 0)
        _need(isinstance(ago, (int, float)) and ago >= 0, "world.turn.ago_s invalide", errors)
    elif surface == "face":
        from .vocab import face_intents
        _need(stim.get("intent") in (None, *face_intents()),
              f"stimulus.intent inconnu : {stim.get('intent')!r}", errors)
        _need(stim.get("state") in FACE_STATES, "stimulus.state hors vocabulaire", errors)
        _need(stim.get("gaze") in FACE_GAZES, "stimulus.gaze hors vocabulaire", errors)
        for flag in ("speech", "urgent", "interrupted"):
            _need(isinstance(stim.get(flag), bool), f"stimulus.{flag} doit etre booleen", errors)

    _need(s.get("id") == scenario_id(s), "id ne correspond pas au contenu (restamp oublie)", errors)
    if errors:
        raise Invalid(errors)
    return s


# ── oracle matching ──────────────────────────────────────────────────────────

_MISSING = object()


def lookup(trace, path: str):
    """`steps.2.channel` -> trace["steps"][2]["channel"]; _MISSING if absent."""
    node = trace
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and re.fullmatch(r"-?\d+", part):
            idx = int(part)
            if -len(node) <= idx < len(node):
                node = node[idx]
            else:
                return _MISSING
        else:
            return _MISSING
    return node


def matches(actual, spec) -> bool:
    """A literal, or one operator: $in $ne $contains $len $gte $lte $absent."""
    if isinstance(spec, dict) and len(spec) == 1 and next(iter(spec)).startswith("$"):
        op, arg = next(iter(spec.items()))
        if op == "$absent":
            return (actual is _MISSING) is bool(arg)
        if actual is _MISSING:
            return False
        if op == "$in":
            return actual in arg
        if op == "$ne":
            return actual != arg
        if op == "$contains":
            return isinstance(actual, (str, list)) and arg in actual
        if op == "$len":
            return hasattr(actual, "__len__") and len(actual) == arg
        if op == "$gte":
            return actual >= arg
        if op == "$lte":
            return actual <= arg
        raise Invalid([f"operateur inconnu {op}"])
    return actual is not _MISSING and actual == spec


def diff(s: dict, trace: dict) -> list[dict]:
    """Every expectation the trace contradicts, as structured records."""
    out = []
    for path, spec in (s.get("expected") or {}).items():
        actual = lookup(trace, path)
        if not matches(actual, spec):
            out.append({"kind": "expected", "path": path, "want": spec,
                        "got": None if actual is _MISSING else actual})
    for path, spec in (s.get("forbidden") or {}).items():
        actual = lookup(trace, path)
        if actual is not _MISSING and matches(actual, spec):
            out.append({"kind": "forbidden", "path": path, "want": {"$not": spec},
                        "got": actual})
    return out


def dumps(s: dict) -> str:
    """One JSONL line, keys sorted, so a corpus file diffs cleanly in git."""
    return _canonical_json(s)
