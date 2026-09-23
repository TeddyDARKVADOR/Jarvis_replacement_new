"""
eval_lab/mutate.py — change exactly one thing, and ask whether the change in
behaviour makes sense.

MUTATION (strategy C)
    `mutate(s, rng)` returns a child that differs from its parent in ONE
    knob: a phone field, the hour, the priority, a device's presence, the
    wording of a sentence, one event of a story. The child records its
    parent and the mutation, so a failure found three generations deep can
    be walked back to the scenario a human wrote.

RELATIONS
    A property judges one trace. A relation judges a PAIR: parent and child
    differ by one variable, so whatever differs in their answers is caused by
    that variable. That is how a boundary shows up — and how a rule that
    "should not care" about something is caught caring.

    Same discipline as properties.py: every relation cites where the project
    makes the promise, and is hard or soft.
"""
from __future__ import annotations

import copy
import random
import unicodedata
from dataclasses import dataclass
from typing import Callable

from . import space
from .scenario import restamp

from .properties import INTRUSION, MONOTONIC_EXCEPTIONS, PRIO_ORDER

# ── knobs ────────────────────────────────────────────────────────────────────

_PHONE_KNOBS = {
    "screen_on": [None, True, False],
    "idle_seconds": [None, 5, 299, 301, 1799, 1801, 7200],
    "dnd": [None, True, False],
    "headset": [None, True, False],
    "ringer": ["NORMAL", "VIBRATE", "SILENT", "UNKNOWN"],
    "activity": ["UNKNOWN", "STILL", "IN_VEHICLE"],
    "activity_confidence": [None, 69, 70, 95],
    "age_s": [0, 299, 301, 3600],
    "bluetooth_devices": [[], ["Mi Band"], ["Peugeot CarKit"]],
}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _choice_other(rng, values, current):
    others = [v for v in values if v != current]
    return rng.choice(others) if others else current


def _context_mutations(s: dict, rng) -> list[tuple[str, object, object, Callable]]:
    world = s["world"]
    out = []
    phone = world.get("phone")
    if phone is not None:
        for key, values in _PHONE_KNOBS.items():
            cur = phone.get(key)
            new = _choice_other(rng, values, cur)

            def apply(w, key=key, new=new):
                if new is None:
                    w["phone"].pop(key, None)
                else:
                    w["phone"][key] = new
            out.append((f"world.phone.{key}", cur, new, apply))
        out.append(("world.phone", "present", None, lambda w: w.pop("phone", None)))
    else:
        out.append(("world.phone", None, "fresh", lambda w: w.__setitem__("phone", {"age_s": 0, "screen_on": True})))
    hour = int(((world.get("time") or {}).get("local") or "2026-09-23T14:00:00")[11:13])
    new_hour = (hour + rng.choice([-1, 1])) % 24
    out.append(("world.time.hour", hour, new_hour,
                lambda w: w.__setitem__("time", {"local": f"2026-09-23T{new_hour:02d}:00:00"})))
    audio = (world.get("system") or {}).get("desktop_audio")
    new_audio = _choice_other(rng, [None, True, False], audio)

    def set_audio(w):
        sysd = w.setdefault("system", {})
        if new_audio is None:
            sysd.pop("desktop_audio", None)
        else:
            sysd["desktop_audio"] = new_audio
    out.append(("world.system.desktop_audio", audio, new_audio, set_audio))
    wake = (world.get("policy") or {}).get("wake_for_critical", True)
    out.append(("world.policy.wake_for_critical", wake, not wake,
                lambda w: w.setdefault("policy", {}).__setitem__("wake_for_critical", not wake)))
    return out


def _routing_mutations(s: dict, rng) -> list:
    world, stim = s["world"], s["stimulus"]
    out = []
    for idx, d in enumerate(world.get("devices") or []):
        online = d.get("online", True)
        out.append((f"world.devices.{d['id']}.online", online, not online,
                    lambda w, idx=idx, v=not online: w["devices"][idx].__setitem__("online", v)))
        if d.get("caps"):
            cap = rng.choice(d["caps"])
            out.append((f"world.devices.{d['id']}.caps", cap, None,
                        lambda w, idx=idx, cap=cap: w["devices"][idx].__setitem__(
                            "caps", [c for c in w["devices"][idx]["caps"] if c != cap])))
    ids = [d["id"] for d in world.get("devices") or []] + [None]
    cur_origin = (world.get("turn") or {}).get("origin")
    new_origin = _choice_other(rng, ids, cur_origin)
    out.append(("world.turn.origin", cur_origin, new_origin,
                lambda w: w.__setitem__("turn", {"origin": new_origin})))
    return out


def _routing_stim_mutations(s: dict, rng) -> list:
    stim = s["stimulus"]
    out = []
    text = stim.get("text", "")
    if text:
        out.append(("stimulus.text~upper", text, text.upper(), lambda st: st.__setitem__("text", text.upper())))
        folded = _fold(text)
        if folded != text:
            out.append(("stimulus.text~accents", text, folded, lambda st: st.__setitem__("text", folded)))
    new_text = rng.choice(list(space.ROUTING_TEXTS.values()))
    out.append(("stimulus.text", text, new_text, lambda st: st.__setitem__("text", new_text)))
    cap = stim.get("capability", "")
    new_cap = _choice_other(rng, space.ROUTING_DIMS["capability"], cap)
    out.append(("stimulus.capability", cap, new_cap, lambda st: st.__setitem__("capability", new_cap)))
    hint = stim.get("model_hint", "")
    new_hint = _choice_other(rng, space.ROUTING_DIMS["model_hint"], hint)
    out.append(("stimulus.model_hint", hint, new_hint, lambda st: st.__setitem__("model_hint", new_hint)))
    return out


def _sequence_mutations(s: dict, rng) -> list:
    ev = s["events"]
    out = []
    if len(ev) > 1:
        i = rng.randrange(len(ev))
        out.append((f"events.{i}~drop", ev[i].get("op"), None, lambda e, i=i: e.pop(i)))
    if len(ev) > 2:
        i = rng.randrange(len(ev) - 1)
        out.append((f"events.{i}~swap", i, i + 1,
                    lambda e, i=i: e.__setitem__(slice(i, i + 2), [e[i + 1], e[i]])))
    for i, e in enumerate(ev):
        if e.get("op") == "advance":
            new = _choice_other(rng, [10.0, 299.0, 301.0, 899.0, 901.0, 1801.0, 86401.0], e["s"])
            out.append((f"events.{i}.s", e["s"], new, lambda e_, i=i, new=new: e_[i].__setitem__("s", new)))
        if e.get("op") == "decide":
            new = _choice_other(rng, PRIO_ORDER, e["priority"])
            out.append((f"events.{i}.priority", e["priority"], new,
                        lambda e_, i=i, new=new: e_[i].__setitem__("priority", new)))
    return out


def mutate(s: dict, rng: random.Random) -> dict | None:
    """One child with exactly one knob changed, or None if none applies."""
    child = copy.deepcopy(s)
    surface = s["surface"]
    target, options = None, []
    if surface in ("policy", "situation"):
        options = [("world", m) for m in _context_mutations(s, rng)]
        if surface == "policy":
            cur = s["stimulus"]["priority"]
            new = _choice_other(rng, PRIO_ORDER, cur)
            options.append(("stimulus", ("stimulus.priority", cur, new,
                                         lambda st: st.__setitem__("priority", new))))
    elif surface == "routing":
        options = [("world", m) for m in _routing_mutations(s, rng)]
        options += [("stimulus", m) for m in _routing_stim_mutations(s, rng)]
    elif surface == "sequence":
        options = [("events", m) for m in _sequence_mutations(s, rng)]
    elif surface == "face":
        stim = s["stimulus"]
        key = rng.choice(["state", "speech", "gaze", "urgent", "interrupted"])
        values = {"state": ["LISTENING", "THINKING", "SPEAKING", "CONFIRM"], "gaze": [None, "user", "screen"]}
        new = _choice_other(rng, values.get(key, [True, False]), stim[key])
        options = [("stimulus", (f"stimulus.{key}", stim[key], new,
                                 lambda st, key=key, new=new: st.__setitem__(key, new)))]
    if not options:
        return None
    target, (path, old, new, apply) = rng.choice(options)
    apply(child[target])
    child["source"] = "mutated"
    child["oracle"] = "implicit"          # the parent's oracle was about the parent
    child["expected"], child["forbidden"] = {}, {}
    lin = child["lineage"]
    lin["parent"] = s["id"]
    lin["mutations"] = list(s["lineage"].get("mutations") or []) + [{"path": path, "from": old, "to": new}]
    lin["generator"] = {"strategy": "mutation", "parent_family": s.get("family")}
    child["family"] = s.get("family", surface)
    restamp(child)
    # Swapping two identical events, upper-casing an upper-case sentence: a
    # mutation with no effect is not a child, it is the parent again.
    return None if child["id"] == s["id"] else child


# ── relations ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Relation:
    name: str
    strength: str
    source: str
    applies: Callable[[dict], bool]          # the mutation record
    check: Callable[[dict, dict, dict], str | None]   # parent trace, child trace, child scenario


RELATIONS: list[Relation] = []


def relation(name, strength, source, applies):
    def wrap(fn):
        RELATIONS.append(Relation(name, strength, source, applies, fn))
        return fn
    return wrap


@relation("HEADSET_DOES_NOT_CHANGE_SITUATION", "hard",
          "context/model.py Route + context/selftest « the headset decides the route, never the priority »",
          lambda m: m["path"] == "world.phone.headset")
def _r_headset(p, c, s):
    if p.get("situation") != c.get("situation"):
        return f"casque {p.get('situation')} -> {c.get('situation')}"
    return None


@relation("TEXT_CASE_AND_ACCENTS_IRRELEVANT", "hard",
          "server/targeting.py — accent- and case-insensitive",
          lambda m: m["path"] in ("stimulus.text~upper", "stimulus.text~accents"))
def _r_fold(p, c, s):
    keys = ("kind", "rule", "device", "hint")
    if any(p.get(k) != c.get(k) for k in keys):
        return " ; ".join(f"{k} {p.get(k)} -> {c.get(k)}" for k in keys if p.get(k) != c.get(k))
    return None


@relation("WAKE_TOGGLE_ONLY_TOUCHES_ASLEEP_CRITICAL", "hard",
          "context/policy.py — la seule case de la table qui contredit « tu dors -> silence »",
          lambda m: m["path"] == "world.policy.wake_for_critical")
def _r_wake(p, c, s):
    if p.get("channel") != c.get("channel") and not (
            p.get("situation") == "ASLEEP" and p.get("priority") == "CRITICAL"):
        return f"{p.get('situation')}/{p.get('priority')} : {p.get('channel')} -> {c.get('channel')}"
    return None


@relation("PRIORITY_RAISE_NEVER_LESS_INTRUSIVE", "hard",
          "D2026-09-23-5 (properties.DECISIONS) ; context/policy.py table (monotone par ligne)",
          lambda m: m["path"] == "stimulus.priority")
def _r_monotone(p, c, s):
    a, b = PRIO_ORDER.index(p.get("priority")), PRIO_ORDER.index(c.get("priority"))
    lo, hi = (p, c) if a < b else (c, p)
    if INTRUSION[hi["channel"]] < INTRUSION[lo["channel"]] and not any(
            k in hi.get("reason", "") for k in MONOTONIC_EXCEPTIONS):
        return (f"{hi['priority']} -> {hi['channel']} moins intrusif que "
                f"{lo['priority']} -> {lo['channel']} ({hi.get('reason', '')})")
    return None


_OUTCOME = ("situation", "channel", "route", "kind", "device", "rule")


def frontier(s: dict, rng: random.Random, tries: int = 40) -> list[dict]:
    """One-variable neighbours of `s` whose outcome differs: where the line is.

    Deterministic given `rng`. Each entry names the variable, its two values,
    and what changed in the answer — the frontier is observed, not asserted.
    """
    from .runner import run_one
    base = run_one(s).get("trace") or {}
    out, seen = [], set()
    for _ in range(tries):
        c = mutate(s, rng)
        if c is None or c["id"] in seen:
            continue
        seen.add(c["id"])
        t = run_one(c).get("trace") or {}
        changed = {k: [base.get(k), t.get(k)] for k in _OUTCOME if base.get(k) != t.get(k)}
        if s["surface"] == "sequence":
            lost_a = any(st.get("lost") for st in base.get("steps") or [])
            lost_b = any(st.get("lost") for st in t.get("steps") or [])
            if lost_a != lost_b:
                changed["lost"] = [lost_a, lost_b]
        if changed:
            out.append({"mutation": c["lineage"]["mutations"][-1], "changed": changed, "child": c["id"]})
    return out


def check_relations(parent_trace: dict, child_trace: dict, child: dict) -> list[dict]:
    muts = (child.get("lineage") or {}).get("mutations") or []
    if not muts or parent_trace is None or child_trace is None:
        return []
    last = muts[-1]
    out = []
    for r in RELATIONS:
        if r.applies(last):
            msg = r.check(parent_trace, child_trace, child)
            if msg:
                out.append({"kind": "relation" if r.strength == "hard" else "warning",
                            "property": r.name, "got": msg, "source": r.source,
                            "mutation": last, "parent": child["lineage"]["parent"]})
    return out
