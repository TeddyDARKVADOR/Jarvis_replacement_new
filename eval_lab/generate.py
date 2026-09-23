"""
eval_lab/generate.py — new situations, by strategy, without an LLM.

    pairwise     A  every PAIR of dimension values at least once, greedily —
                    ~100 scenarios cover what a cartesian product needs
                    millions for, then random fill for the rest of the count.
    boundary     B  each threshold the code compares against, at -eps, exactly
                    on it, and +eps. Oracles only where the code states which
                    side the line belongs to (">" vs ">=").
    sequence     D  stories: sleep, wake, meetings, silences, check-ins,
                    alerts, releases — over simulated time.
    adversarial  E  inputs built to fool a heuristic: substrings, negations,
                    two devices in one sentence, contradictory signals.
    rare         F  oversamples what the pairwise pass sees once: a stale
                    phone with a headset, CRITICAL asleep with waking off,
                    no route at all, confidence exactly on the line.

    (C, mutation, lives in mutate.py; G, coverage-guided, in coverage.py.)

DETERMINISM
    Every strategy takes a seed and uses its own random.Random. Same seed,
    same scenarios, same ids — which is what makes a generated failure
    reproducible by name, weeks later.

WHAT A GENERATED SCENARIO EXPECTS
    Mostly nothing explicit: `oracle: implicit`, judged by the surface's
    properties. An explicit oracle is written only where the code or its
    documentation states the answer; the note says where.
"""
from __future__ import annotations

import itertools
import random

from . import space
from .scenario import make

# ── builders: an assignment of dimension values -> a canonical scenario ──────


def _local(hour: int, minute: int = 0, second: int = 0) -> str:
    return f"2026-09-23T{hour:02d}:{minute:02d}:{second:02d}"


def context_world(a: dict) -> dict:
    world: dict = {"time": {"local": _local(a.get("hour", 14), a.get("minute", 0))}}
    kind = a.get("phone", "fresh")
    if kind != "none":
        phone = {"age_s": a.get("age_fresh", 0) if kind == "fresh" else a.get("age_stale", 3600)}
        for key in ("screen_on", "idle_seconds", "dnd", "headset"):
            if a.get(key) is not None:
                phone[key] = a[key]
        if a.get("ringer", "UNKNOWN") != "UNKNOWN":
            phone["ringer"] = a["ringer"]
        if a.get("activity", "UNKNOWN") != "UNKNOWN":
            phone["activity"] = a["activity"]
            if a.get("confidence") is not None:
                phone["activity_confidence"] = a["confidence"]
        if a.get("bluetooth"):
            phone["bluetooth_devices"] = list(a["bluetooth"])
        world["phone"] = phone
    system = {}
    if a.get("desktop_audio", "absent") != "absent":
        system["desktop_audio"] = a["desktop_audio"]
    if a.get("local_presence_s", "absent") != "absent":
        system["local_presence_s"] = a["local_presence_s"]
    if system:
        world["system"] = system
    d = a.get("delivered", "none")
    if d != "none":
        prio, ago = d.split("@")
        world["delivered"] = [{"priority": prio, "ago_s": float(ago)}]
    if a.get("wake_for_critical") is False:
        world["policy"] = {"wake_for_critical": False}
    return world


def routing_world(a: dict) -> tuple[dict, dict, list[str]]:
    """-> (world, stimulus, repairs). An origin naming an absent device is
    repaired to None rather than emitted invalid: combinatorial generation
    knows the rules and has no excuse for breaking them."""
    devices, repairs = [], []
    for key, dev_id, dtype, caps in (("pc", "desktop-01", "pc", space.PC_CAPS),
                                     ("phone", "phone-01", "android", space.PHONE_CAPS)):
        state = a.get(key, "online")
        if state == "absent":
            continue
        d = {"id": dev_id, "type": dtype, "caps": list(caps)}
        if state == "offline":
            d["online"] = False
        elif state == "stale":
            d["last_seen_ago_s"] = 3600.0
        devices.append(d)
    unk = a.get("unknown_dev", "absent")
    if unk != "absent":
        devices.append({"id": "tablet-01", "type": "unknown",
                        "caps": ["open_app"] if unk == "capable" else []})
    ids = {"pc": "desktop-01", "phone": "phone-01", "unknown_dev": "tablet-01"}
    origin = ids.get(a.get("origin")) if a.get("origin") else None
    if origin and origin not in {d["id"] for d in devices}:
        repairs.append(f"origin {origin} absent -> None")
        origin = None
    text = a.get("text_raw") or space.ROUTING_TEXTS.get(a.get("text", "generic"), "")
    stim = {"text": text, "capability": a.get("capability", ""), "model_hint": a.get("model_hint", "")}
    return {"devices": devices, "turn": {"origin": origin}}, stim, repairs


def _gen(strategy: str, seed: int, index: int, **extra) -> dict:
    return {"generator": {"strategy": strategy, "seed": seed, "index": index, **extra}}


def build_policy(a: dict, *, strategy: str, seed: int, index: int, source: str = "generated",
                 family: str = "", **kw) -> dict:
    return make("policy", world=context_world(a), stimulus={"priority": a.get("priority", "IMPORTANT")},
                source=source, family=family or f"policy.{strategy}", seed=seed, oracle=kw.pop("oracle", "implicit"),
                lineage=_gen(strategy, seed, index, assignment=a), **kw)


def build_routing(a: dict, *, strategy: str, seed: int, index: int, source: str = "generated",
                  family: str = "", **kw) -> dict:
    world, stim, repairs = routing_world(a)
    return make("routing", world=world, stimulus=stim, source=source,
                family=family or f"routing.{strategy}", seed=seed, oracle=kw.pop("oracle", "implicit"),
                lineage=_gen(strategy, seed, index, assignment=a, repairs=repairs), **kw)


# ── A. pairwise ──────────────────────────────────────────────────────────────


def _pairs(dims: dict) -> set:
    keys = sorted(dims)
    out = set()
    for k1, k2 in itertools.combinations(keys, 2):
        for v1 in range(len(dims[k1])):
            for v2 in range(len(dims[k2])):
                out.add((k1, v1, k2, v2))
    return out


def _covered(assign_idx: dict) -> set:
    keys = sorted(assign_idx)
    return {(k1, assign_idx[k1], k2, assign_idx[k2]) for k1, k2 in itertools.combinations(keys, 2)}


def pairwise_assignments(dims: dict, count: int, seed: int, candidates: int = 40):
    """Greedy all-pairs, then random. Yields dicts of dimension -> value."""
    rng = random.Random(seed)
    uncovered = _pairs(dims)
    produced = 0
    while produced < count:
        pool = [{k: rng.randrange(len(v)) for k, v in dims.items()} for _ in range(candidates)]
        if uncovered:
            best = max(pool, key=lambda p: len(_covered(p) & uncovered))
            uncovered -= _covered(best)
        else:
            best = pool[0]
        produced += 1
        yield {k: dims[k][i] for k, i in best.items()}


def pairwise(count: int, seed: int = 1, surface: str = "policy"):
    if surface == "routing":
        for i, a in enumerate(pairwise_assignments(space.ROUTING_DIMS, count, seed)):
            yield build_routing(a, strategy="pairwise", seed=seed, index=i)
    else:
        for i, a in enumerate(pairwise_assignments(space.CONTEXT_DIMS, count, seed)):
            yield build_policy(a, strategy="pairwise", seed=seed, index=i)


# ── B. boundaries ────────────────────────────────────────────────────────────


def boundary(seed: int = 1):
    """Every threshold at -eps / on / +eps. Deterministic; `seed` only labels."""
    th = space.thresholds()
    E = space.EPS
    i = itertools.count()

    def b(name, offset, s):
        s["family"] = f"boundary.{name}"
        s["source"] = "boundary"
        s["lineage"]["generator"].update({"boundary": name, "offset": offset})
        from .scenario import restamp
        return restamp(s)

    def pol(a, name, offset, expected=None, notes=""):
        s = build_policy(a, strategy="boundary", seed=seed, index=next(i),
                         expected=expected or {}, oracle="explicit" if expected else "implicit",
                         notes=notes)
        return b(name, offset, s)

    ttl = th["device_ttl_s"]
    for off, age in ((-1, ttl - E), (0, ttl), (1, ttl + E)):
        yield pol({"phone": "fresh", "age_fresh": age, "screen_on": True, "priority": "IMPORTANT",
                   "hour": 14}, "device_ttl_s", off,
                  expected={"situation": "UNKNOWN"} if off > 0 else {"situation": "ACTIVE"},
                  notes="context/model.py is_stale : age > ttl (strict) -> perime")

    asleep = th["asleep_idle_s"]
    for off, idle in ((-1, asleep - E), (0, asleep), (1, asleep + E)):
        yield pol({"phone": "fresh", "screen_on": False, "idle_seconds": idle, "hour": 3,
                   "priority": "IMPORTANT"}, "asleep_idle_s", off,
                  expected={"situation": "ASLEEP" if off >= 0 else "IDLE"},
                  notes="context/situation.py : idle >= asleep_idle_s")

    active = th["active_idle_s"]
    for off, idle in ((-1, active - E), (0, active), (1, active + E)):
        yield pol({"phone": "fresh", "screen_on": False, "idle_seconds": idle, "hour": 14,
                   "priority": "USEFUL"}, "active_idle_s", off,
                  expected={"situation": "ACTIVE" if off < 0 else "IDLE"},
                  notes="context/situation.py : idle < active_idle_s -> ACTIVE")
        yield pol({"phone": "none", "local_presence_s": idle, "desktop_audio": True,
                   "priority": "IMPORTANT"}, "local_presence_s", off,
                  expected={"situation": "ACTIVE" if off < 0 else "IDLE"},
                  notes="context/situation.py : local < active_idle_s -> ACTIVE")

    conf = int(th["vehicle_confidence"])
    for off, c in ((-1, conf - 1), (0, conf), (1, conf + 1)):
        yield pol({"phone": "fresh", "activity": "IN_VEHICLE", "confidence": c, "screen_on": True,
                   "priority": "USEFUL"}, "vehicle_confidence", off,
                  expected={"situation": "DRIVING" if off >= 0 else "ACTIVE"},
                  notes="context/situation.py : confidence >= vehicle_confidence")

    qs, qe = int(th["quiet_start_hour"]), int(th["quiet_end_hour"])
    for name, (h, m, sec), quiet in (("quiet_start", (qs - 1, 59, 59), False), ("quiet_start", (qs, 0, 0), True),
                                     ("quiet_end", (qe - 1, 59, 59), True), ("quiet_end", (qe, 0, 0), False)):
        s = build_policy({"phone": "fresh", "screen_on": False, "idle_seconds": 7200, "priority": "USEFUL",
                          "hour": h, "minute": m}, strategy="boundary", seed=seed, index=next(i),
                         expected={"quiet_hours": quiet, "situation": "ASLEEP" if quiet else "IDLE"},
                         oracle="explicit", notes="context/situation.py : debut inclus, fin exclue")
        s["world"]["time"]["local"] = _local(h, m, sec)
        yield b(name, 0 if quiet else -1, s)

    for prio, key in (("IMPORTANT", "cooldown_important_s"), ("USEFUL", "cooldown_useful_s")):
        w = th[key]
        for off, ago in ((-1, w - E), (0, w), (1, w + E)):
            s = build_policy({"phone": "fresh", "screen_on": True, "headset": True, "priority": prio,
                              "hour": 14}, strategy="boundary", seed=seed, index=next(i),
                             expected={"silent": off < 0}, oracle="explicit",
                             notes="context/policy.py : (now - last) < window -> DEFER")
            s["world"]["delivered"] = [{"priority": prio, "ago_s": ago}]
            yield b(key, off, s)

    stale = th["device_stale_s"]
    for off, ago in ((-1, stale - E), (0, stale), (1, stale + E)):
        s = build_routing({"pc": "online", "phone": "online", "origin": "phone", "text": "pc",
                           "capability": "open_app"}, strategy="boundary", seed=seed, index=next(i),
                          expected={"kind": "unavailable" if off > 0 else "device"}, oracle="explicit",
                          notes="server/devices.py is_stale : now - last_seen > 90 (strict)")
        s["world"]["devices"][0]["last_seen_ago_s"] = ago
        yield b("device_stale_s", off, s)

    age_max = th["queue_max_age_s"]
    for off, wait in ((-1, age_max - E), (0, age_max), (1, age_max + E)):
        s = make("sequence", source="boundary", family="boundary.queue_max_age_s", seed=seed,
                 world={"phone": {"age_s": 0, "screen_on": True, "dnd": True}},
                 events=[{"op": "push", "priority": "USEFUL", "payload": "note"},
                         {"op": "advance", "s": wait},
                         {"op": "world", "phone": {"age_s": 0, "dnd": False}},
                         {"op": "release"}],
                 expected={"steps.3.released": {"$len": 0 if off > 0 else 1}}, oracle="explicit",
                 notes="context/policy.py release : age > max_age_s (strict) -> perime",
                 lineage=_gen("boundary", seed, next(i), boundary="queue_max_age_s", offset=off))
        yield s


# ── D. sequences ─────────────────────────────────────────────────────────────

_MOMENTS = {
    "asleep":  ("03:00", {"age_s": 0, "screen_on": False, "idle_seconds": 7200, "dnd": False}),
    "wake":    ("07:30", {"age_s": 0, "screen_on": True, "idle_seconds": 5, "dnd": False}),
    "meeting": (None, {"age_s": 0, "screen_on": True, "idle_seconds": 20, "dnd": True}),
    "drive":   (None, {"age_s": 0, "activity": "IN_VEHICLE", "activity_confidence": 90, "dnd": False}),
    "away":    (None, {"age_s": 0, "screen_on": False, "idle_seconds": 2400, "dnd": False,
                       "activity": "STILL"}),
    "active":  (None, {"age_s": 0, "screen_on": True, "idle_seconds": 5, "dnd": False, "activity": "STILL"}),
    "headset": (None, {"age_s": 0, "headset": True}),
    "gone":    (None, None),
}
_WAITS = [10, 299, 301, 899, 901, 1801, 3601, 86401]


def _moment_event(name: str) -> dict:
    at, phone = _MOMENTS[name]
    ev: dict = {"op": "world", "phone": dict(phone) if phone else None}
    if at:
        ev["at"] = at            # the NEXT 03:00 — time never runs backwards
    return ev


def sequences(count: int, seed: int = 1, min_len: int = 3, max_len: int = 12):
    """Random walks through the day. The check-in (`decide` IMPORTANT with
    payload "proactive") is modelled on main.py's hook: it pushes the bare
    string "proactive" when deferred and marks delivery when it speaks."""
    rng = random.Random(seed)
    for n in range(count):
        events = [_moment_event(rng.choice(list(_MOMENTS)[:-1]))]
        for _ in range(rng.randint(min_len, max_len)):
            r = rng.random()
            if r < 0.25:
                events.append(_moment_event(rng.choice(list(_MOMENTS))))
            elif r < 0.45:
                events.append({"op": "advance", "s": float(rng.choice(_WAITS))})
            elif r < 0.70:
                prio = rng.choice(space.PRIORITIES)
                # IMPORTANT is main.py's proactive check-in: queued whenever it
                # does not speak. Other levels have no producer yet; they are
                # queued only on DEFER.
                queue_rule = "push_unless_speaks" if prio == "IMPORTANT" else "push_if_deferred"
                events.append({"op": "decide", "priority": prio, queue_rule: True,
                               "deliver_if_speaks": True,
                               "payload": "proactive" if prio == "IMPORTANT" else f"msg-{prio.lower()}"})
            elif r < 0.85:
                events.append({"op": "alerts", "alerts": [f"[MONITOR_ALERT] sujet {k}\nHeadline: titre {k}"
                                                          for k in range(rng.randint(1, 3))]})
            else:
                events.append({"op": "release"})
        yield make("sequence", source="generated", family="sequence.walk", seed=seed, events=events,
                   world={"time": {"local": _local(rng.choice([2, 9, 14, 21]))},
                          "system": {"desktop_audio": rng.random() < 0.7}},
                   oracle="implicit", difficulty=2,
                   lineage=_gen("sequence", seed, n))


# ── E. adversarial ───────────────────────────────────────────────────────────

# Each: (text, capability, origin, note, expected-or-None). Expected only where
# targeting.py's own docstring decides; otherwise implicit, for FULL/LLM tiers.
_ADVERSARIAL_TEXTS = [
    ("ne l'ouvre pas sur mon PC, ouvre-le ici", "open_app", "phone",
     "negation : le motif « sur mon PC » est present mais nie", None),
    ("depuis mon PC, envoie ça sur mon téléphone", "open_app", "pc",
     "deux appareils dans la phrase : source et destination", None),
    ("ouvre ça sur mon ordinateur de bureau", "open_app", "phone",
     "« ordinateur de bureau » : aucun motif PC ne le couvre", None),
    ("ouvre ça sur mon Pc", "open_app", "phone", "casse mixte : le repli minuscules doit suffire",
     {"hint": "pc"}),
    ("ouvre ça sur mon téléphone portable", "open_app", "pc", "« téléphone portable » = le telephone",
     {"hint": "android"}),
    ("mets la musique sur l'autre", "open_app", "phone", "« l'autre » sans nom d'appareil", None),
    ("ouvre-le sur le pc du salon", "open_app", "phone", "un PC nomme par sa piece", None),
    ("ouvre YouTube sur mon mobile", "open_app", "pc", "« mobile »", {"hint": "android"}),
    ("lance ça sur mon automobile", "open_app", "pc", "« mobile » cache dans « automobile »",
     {"hint": None}),
]

# Bluetooth names that contain a car pattern by accident.
_BLUETOOTH_TRAPS = [
    ("Oscar's AirPods", "« car » dans Oscar"),
    ("Bose SyncBuds", "« sync » dans un casque"),
    ("AutoFocus Cam", "« auto » dans une camera"),
    ("Sony WH-1000XM5", "casque ordinaire, temoin"),
    ("Peugeot 208", "vraie voiture sans motif"),
]


def adversarial(seed: int = 1):
    i = itertools.count()
    for text, cap, origin, note, expected in _ADVERSARIAL_TEXTS:
        yield build_routing({"pc": "online", "phone": "online", "origin": origin, "text_raw": text,
                             "capability": cap}, strategy="adversarial", seed=seed, index=next(i),
                            source="adversarial", family="adversarial.routing_text",
                            expected=expected or {}, oracle="explicit" if expected else "implicit",
                            difficulty=3, notes=note)
    for name, note in _BLUETOOTH_TRAPS:
        car = name == "Peugeot 208"
        yield build_policy({"phone": "fresh", "screen_on": True, "headset": True, "bluetooth": [name],
                            "priority": "USEFUL", "hour": 14}, strategy="adversarial", seed=seed,
                           index=next(i), source="adversarial", family="adversarial.bluetooth_name",
                           expected={} if car else {"situation": "ACTIVE"},
                           oracle="implicit" if car else "explicit", difficulty=3,
                           notes=note + (" — non couvert : faux negatif assume par le README" if car
                                         else " — ecran allume, casque : ce n'est pas une voiture"))
    # Contradictory signals: every observed signal at once.
    yield build_policy({"phone": "fresh", "activity": "IN_VEHICLE", "confidence": 95, "screen_on": False,
                        "idle_seconds": 7200, "dnd": True, "ringer": "SILENT", "hour": 3,
                        "priority": "CRITICAL"}, strategy="adversarial", seed=seed, index=next(i),
                       source="adversarial", family="adversarial.contradiction",
                       expected={"situation": "DRIVING"}, oracle="explicit",
                       notes="context/situation.py : DRIVING d'abord, observe > deduit > reglage")


# ── F. rare events ───────────────────────────────────────────────────────────

_RARE = {
    "wake_for_critical": False, "phone": "stale", "priority": "CRITICAL",
    "confidence": 70, "desktop_audio": False, "delivered": "CRITICAL@5",
    "local_presence_s": 299, "headset": True,
}


def rare(count: int, seed: int = 1):
    """Pairwise-like sampling where each rare value is forced with p=0.5."""
    rng = random.Random(seed)
    for n in range(count):
        a = {k: rng.choice(v) for k, v in space.CONTEXT_DIMS.items()}
        for k, v in _RARE.items():
            if rng.random() < 0.5:
                a[k] = v
        if a["confidence"] == 70:
            a["activity"] = "IN_VEHICLE"
        yield build_policy(a, strategy="rare", seed=seed, index=n, family="policy.rare", difficulty=2)


# ── FULL: the router ─────────────────────────────────────────────────────────

ROUTER_DIMS = {
    "pc": ["online", "online", "offline", "stale", "absent"],
    "phone": ["online", "online", "offline", "stale", "absent"],
    "pc_channel": [True, True, False],
    "phone_channel": [True, True, False],
    "origin": ["pc", "phone", None],
    "text": ["generic", "pc", "phone", "here", "other"],
    "turn_age": [0.0, 179.5, 180.0, 180.5, 600.0],     # device_api TURN_CONTEXT_TTL = 180
    "tool": ["open_app", "computer_control", "phone_camera", "web_search"],
    "target_device": ["", "", "pc", "android", "here", "other"],
}


def router(count: int, seed: int = 1):
    """FULL-tier scenarios for the real ActionRouter. `web_search` is claimed
    by no device on purpose: it is the path that must stay exactly V1."""
    for i, a in enumerate(pairwise_assignments(ROUTER_DIMS, count, seed)):
        world, _, repairs = routing_world({**a, "unknown_dev": "absent"})
        for d in world["devices"]:
            d["channel"] = a["pc_channel"] if d["type"] == "pc" else a["phone_channel"]
        origin = world["turn"]["origin"]
        world["turn"] = {"origin": origin, "text": space.ROUTING_TEXTS[a["text"]],
                         "ago_s": a["turn_age"]} if origin else {"origin": None}
        world["local_tools"] = sorted({a["tool"], "web_search"})
        yield make("router", world=world, tier="full", oracle="implicit", seed=seed,
                   family="router.pairwise", difficulty=2,
                   stimulus={"tool": a["tool"], "parameters": {"app": "chrome"},
                             "target_device": a["target_device"]},
                   lineage=_gen("router", seed, i, assignment=a, repairs=repairs))


EXPLICIT_DIMS = {
    # Who declares the tool: nobody (the V1 local path), one side, both.
    "claimed_by": ["nobody", "nobody", "pc", "phone", "both"],
    "pc": ["online", "online", "offline", "absent"],
    "phone": ["online", "online", "offline", "absent"],
    "origin": ["pc", "phone", None],
    # Named in the sentence, by the model, or not at all (the control group).
    "named": ["pc", "phone", "here", "other", "none"],
    "via": ["text", "text", "model_hint"],
    "turn_age": [3.0, 179.5, 180.5],          # the sentence stops counting at 180 s
}


def explicit_target(count: int, seed: int = 1):
    """Around decision N1: a named target versus the V1 "nobody claims it,
    run it locally" rule. Half the frontier is the claim (nobody vs someone),
    the other half is whether the name still counts (turn age around 180 s)."""
    texts = {"pc": "mets le volume a 30 sur mon PC", "phone": "mets le volume a 30 sur mon telephone",
             "here": "mets le volume a 30 ici", "other": "mets le volume a 30 sur l'autre appareil",
             "none": "mets le volume a 30"}
    hints = {"pc": "pc", "phone": "android", "here": "here", "other": "other", "none": ""}
    for i, a in enumerate(pairwise_assignments(EXPLICIT_DIMS, count, seed)):
        devices = []
        for key, dev_id, dtype in (("pc", "desktop-01", "pc"), ("phone", "phone-01", "android")):
            if a[key] == "absent":
                continue
            caps = ["open_app"] + (["set_volume"] if a["claimed_by"] in (key, "both") else [])
            devices.append({"id": dev_id, "type": dtype, "caps": caps, "online": a[key] == "online"})
        ids = {"pc": "desktop-01", "phone": "phone-01"}
        origin = ids.get(a["origin"])
        if origin not in {d["id"] for d in devices}:
            origin = None
        by_text = a["via"] == "text"
        turn = {"origin": origin, "text": texts[a["named"]] if by_text else texts["none"],
                "ago_s": a["turn_age"]} if origin else {"origin": None}
        yield make("router", tier="full", oracle="implicit", seed=seed, family="router.explicit_target",
                   difficulty=2,
                   world={"devices": devices, "turn": turn, "local_tools": ["set_volume", "open_app"]},
                   stimulus={"tool": "set_volume", "parameters": {"level": 30},
                             "target_device": "" if by_text else hints[a["named"]]},
                   lineage=_gen("explicit_target", seed, i, assignment=a))


STRATEGIES = {
    "explicit-target": explicit_target,
    "router": router,
    "pairwise": lambda count, seed: pairwise(count, seed, "policy"),
    "pairwise-routing": lambda count, seed: pairwise(count, seed, "routing"),
    "boundary": lambda count, seed: boundary(seed),
    "sequence": sequences,
    "adversarial": lambda count, seed: adversarial(seed),
    "rare": rare,
}
