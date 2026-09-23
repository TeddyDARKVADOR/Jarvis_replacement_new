"""
eval_lab/legacy.py — the existing tests, as canonical scenarios.

THE EXIT CONDITION
    old test PASS  <=>  new runner PASS, for every imported case. Checked by
    eval_lab/selftest.py, which runs both.

WHAT IS IMPORTED, AND HOW
    face     avatar/checks/scenario_matrix.mjs — the 1 632 situations. The
             dimensions are read FROM THE NODE MODULE (its MATRIX export), not
             copied, and the judge is the module's own `violations()`.
    context  context/selftest.py — every assertion whose oracle is a value,
             transcribed one scenario per assertion. `legacy_ref` names the
             check each one came from.
    routing  server/routing_selftest.py checks 1-18 and 20, same treatment.

WHAT IS NOT, AND WHY — documented, not forgotten
    context "imports nothing from the core", "memory imported lazily"
        oracle is an AST scan of source files; not a situation.
    context store checks (garbage, merge, copies, forget, probe, describe,
        explain, Android payload, heartbeat)
        oracle is about ContextStore's API contract, not a decision. They
        stay in context/selftest.py, which the lab runs as-is.
    context "the headset decides the route, never the priority" — the
        route half is imported; the "situation unchanged" half is RELATIONAL
        (two scenarios compared), i.e. a metamorphic relation. It lives in
        eval_lab/mutate.py as HEADSET_DOES_NOT_CHANGE_SITUATION.
    routing 19 (no token leaks) — a property of `public()`, not a decision.
    routing 21-29 — the router's threading and the client's refusals; they
        need a live loop and a socket, i.e. the FULL tier.
    scenario_matrix "intentions indiscernables" — a property of the whole
        collection (pairs of intents in a context), printed as WARN, never a
        failure. Not a per-scenario oracle.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .scenario import make

REPO = Path(__file__).resolve().parent.parent
CORPUS = Path(__file__).resolve().parent / "corpus" / "legacy"


def _ctx(check: str) -> dict:
    return {"legacy_ref": f"context/selftest.py :: {check}"}


def _rt(check: str) -> dict:
    return {"legacy_ref": f"server/routing_selftest.py :: {check}"}


def _t(hour: int, minute: int = 0, day: int = 5) -> dict:
    return {"local": f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:00"}


def _L(surface, check_ref, **kw):
    return make(surface, source="legacy", lineage=check_ref, **kw)


# ── context/selftest.py ──────────────────────────────────────────────────────

def context_scenarios() -> list[dict]:
    out: list[dict] = []
    fresh = {"age_s": 0}

    for hour, want in {23: True, 0: True, 3: True, 6: True, 7: False, 12: False, 22: False}.items():
        out.append(_L("situation", _ctx("quiet hours wrap midnight"), family="time.quiet_hours",
                      world={"time": _t(hour, 30)}, expected={"quiet_hours": want}))

    out.append(_L("situation", _ctx("no phone data -> UNKNOWN, never a guess"), family="situation.unknown",
                  world={}, expected={"situation": "UNKNOWN"}))
    out.append(_L("situation", _ctx("no phone data -> UNKNOWN, never a guess"), family="situation.stale",
                  world={"phone": {"age_s": 10_000, "screen_on": True}},
                  expected={"situation": "UNKNOWN", "reason": {"$contains": "perimees"}}))

    night = _t(3)
    out.append(_L("situation", _ctx("driving at 3 a.m. is DRIVING, not ASLEEP"), family="situation.precedence",
                  world={"time": night, "phone": {**fresh, "screen_on": False, "idle_seconds": 7200,
                                                   "activity": "IN_VEHICLE", "activity_confidence": 95}},
                  expected={"situation": "DRIVING"}))
    out.append(_L("situation", _ctx("DND at 3 a.m. is ASLEEP, not MEETING"), family="situation.precedence",
                  world={"time": night, "phone": {**fresh, "screen_on": False, "idle_seconds": 7200, "dnd": True}},
                  expected={"situation": "ASLEEP"}))
    out.append(_L("situation", _ctx("sleep needs three signals, not just the clock"), family="situation.sleep",
                  world={"time": night, "phone": {**fresh, "screen_on": True, "idle_seconds": 5}},
                  expected={"situation": "ACTIVE"}))
    out.append(_L("situation", _ctx("sleep needs three signals, not just the clock"), family="situation.sleep",
                  world={"time": night, "phone": {**fresh, "screen_on": False, "idle_seconds": 60}},
                  forbidden={"situation": "ASLEEP"}))
    out.append(_L("situation", _ctx("a car Bluetooth name stands in for activity recognition"),
                  family="situation.car_bluetooth",
                  world={"phone": {**fresh, "screen_on": False, "idle_seconds": 100,
                                   "bluetooth_devices": ["Peugeot CarKit", "Mi Band"]}},
                  expected={"situation": "DRIVING"}))
    out.append(_L("situation", _ctx("a car Bluetooth name stands in for activity recognition"),
                  family="situation.car_bluetooth",
                  world={"phone": {**fresh, "screen_on": False, "idle_seconds": 100,
                                   "bluetooth_devices": ["Mi Band"]}},
                  forbidden={"situation": "DRIVING"}))
    day = _t(14)
    for headset, route in ((True, "HEADSET"), (False, "PHONE")):
        out.append(_L("situation", _ctx("the headset decides the route, never the priority"),
                      family="route.headset",
                      world={"time": day, "phone": {**fresh, "screen_on": True, "headset": headset}},
                      expected={"route": route}))

    matrix = {
        "DRIVING": ["INTERRUPT", "VOICE", "DEFER", "DROP"],
        "MEETING": ["NOTIFY", "NOTIFY_SILENT", "DEFER", "DROP"],
        "ASLEEP":  ["INTERRUPT", "DEFER", "DEFER", "DROP"],
        "ACTIVE":  ["INTERRUPT", "VOICE", "NOTIFY_SILENT", "DROP"],
        "IDLE":    ["INTERRUPT", "NOTIFY", "NOTIFY_SILENT", "DROP"],
        "UNKNOWN": ["INTERRUPT", "NOTIFY", "DEFER", "DROP"],
    }
    for sit, row in matrix.items():
        for prio, want in zip(("CRITICAL", "IMPORTANT", "USEFUL", "TRIVIAL"), row):
            out.append(_L("policy", _ctx("all 24 cells of priority x situation"), family="policy.matrix",
                          world={"phone": {**fresh, "screen_on": True, "headset": True},
                                 "force": {"situation": sit, "route": "HEADSET"}},
                          stimulus={"priority": prio}, expected={"channel": want}))

    for prio in ("CRITICAL", "IMPORTANT", "USEFUL", "TRIVIAL"):
        out.append(_L("policy", _ctx("JARVIS never speaks in a meeting, at any priority"),
                      family="policy.meeting",
                      world={"phone": {**fresh, "screen_on": True, "dnd": True}},
                      stimulus={"priority": prio},
                      expected={"situation": "MEETING"}, forbidden={"speaks": True}))

    asleep = {"time": night, "phone": {**fresh, "screen_on": False, "idle_seconds": 7200}}
    out.append(_L("policy", _ctx("wake_for_critical=False silences even CRITICAL while asleep"),
                  family="policy.wake_toggle", world=asleep, stimulus={"priority": "CRITICAL"},
                  expected={"situation": "ASLEEP", "channel": "INTERRUPT"}))
    out.append(_L("policy", _ctx("wake_for_critical=False silences even CRITICAL while asleep"),
                  family="policy.wake_toggle", world={**asleep, "policy": {"wake_for_critical": False}},
                  stimulus={"priority": "CRITICAL"}, expected={"situation": "ASLEEP", "channel": "DEFER"}))

    out.append(_L("policy", _ctx("no audio output -> notification instead of speech"),
                  family="policy.reality",
                  world={"phone": {**fresh, "screen_on": True},
                         "force": {"situation": "ACTIVE", "route": "NONE"}},
                  stimulus={"priority": "IMPORTANT"},
                  expected={"channel": "NOTIFY", "reason": {"$contains": "sortie audio"}}))
    out.append(_L("policy", _ctx("no reachable client -> deferred, not dropped"), family="policy.reality",
                  world={}, stimulus={"priority": "USEFUL"},
                  expected={"route": "NONE", "channel": "DEFER"}))

    # Sequences: the checks that are stories, not snapshots.
    active_hs = {**fresh, "screen_on": True, "headset": True}
    out.append(_L("sequence", _ctx("a delivered level goes quiet, and the quiet is DEFER"),
                  family="sequence.cooldown", world={"phone": active_hs},
                  # The check stamps the delivery at NOW after asking at NOW+1;
                  # a sequence cannot go back in time, so it delivers at NOW+1.
                  # Same outcome: 59 s later is inside 900 s, 999 s is outside.
                  # The check also reuses ONE snapshot for 1000 s; in simulated
                  # time the phone would go stale (TTL 300 s), so it reports
                  # again before the last decision — found by the lab's first
                  # run, a transcription error and not a JARVIS one.
                  events=[
                      {"op": "decide", "priority": "IMPORTANT"},
                      {"op": "advance", "s": 1}, {"op": "decide", "priority": "IMPORTANT"},
                      {"op": "deliver", "priority": "IMPORTANT"},
                      {"op": "advance", "s": 59}, {"op": "decide", "priority": "IMPORTANT"},
                      {"op": "advance", "s": 940}, {"op": "world", "phone": {"age_s": 0}},
                      {"op": "decide", "priority": "IMPORTANT"},
                  ],
                  expected={"steps.0.channel": "VOICE", "steps.2.channel": "VOICE",
                            "steps.5.channel": "DEFER", "steps.8.channel": "VOICE"}))
    # The legacy check delivers at NOW and asks CRITICAL at NOW+60: same story.
    out.append(_L("sequence", _ctx("a delivered level goes quiet, and the quiet is DEFER"),
                  family="sequence.cooldown", world={"phone": active_hs},
                  events=[{"op": "deliver", "priority": "IMPORTANT"}, {"op": "advance", "s": 60},
                          {"op": "decide", "priority": "CRITICAL"}],
                  expected={"steps.2.channel": "INTERRUPT"}))

    asleep_phone = {**fresh, "screen_on": False, "idle_seconds": 7200}
    out.append(_L("sequence", _ctx("what was held during the night comes back in the morning"),
                  family="sequence.deferral", world={"time": night, "phone": asleep_phone},
                  events=[
                      {"op": "decide", "priority": "USEFUL", "payload": "colis livre", "push_if_deferred": True},
                      {"op": "decide", "priority": "IMPORTANT", "payload": "facture due", "push_if_deferred": True},
                      {"op": "decide", "priority": "TRIVIAL", "payload": "pub", "push_if_deferred": True},
                      {"op": "world", "time": _t(8), "phone": {"age_s": 0, "screen_on": True,
                                                               "headset": True, "idle_seconds": 7200}},
                      {"op": "release"},
                  ],
                  expected={"steps.2.held": {"$len": 2}, "steps.3.situation": "ACTIVE",
                            "steps.4.released": {"$len": 2}, "steps.4.priorities.0": "IMPORTANT",
                            "final.held": {"$len": 0}}))
    out.append(_L("sequence", _ctx("waking up into a meeting keeps the queue shut"),
                  family="sequence.deferral",
                  world={"phone": {**fresh, "screen_on": True, "dnd": True}},
                  events=[{"op": "push", "priority": "USEFUL", "payload": "note"},
                          {"op": "world"}, {"op": "release"}],
                  expected={"steps.1.situation": "MEETING", "steps.2.released": {"$len": 0},
                            "final.held": {"$len": 1}}))
    out.append(_L("sequence", _ctx("expired items are dropped, not delivered late"),
                  family="sequence.deferral", world={"queue": {"max_age_s": 3600}},
                  events=[{"op": "push", "priority": "USEFUL", "payload": "meteo d'hier"},
                          {"op": "advance", "s": 7200},
                          {"op": "world", "phone": {"age_s": 0, "screen_on": True, "headset": True}},
                          {"op": "release"}],
                  expected={"steps.3.released": {"$len": 0}}))
    return out


# ── server/routing_selftest.py ───────────────────────────────────────────────

PC = {"id": "desktop-01", "type": "pc", "name": "PC de travail", "caps": ["open_app", "computer_control"]}
PHONE = {"id": "phone-01", "type": "android", "name": "Redmi", "caps": ["open_app", "phone_camera"]}


def _reg(pc_online=True, phone_online=True, extra=()):
    return [{**PC, "online": pc_online}, {**PHONE, "online": phone_online}, *extra]


def _route(check, family, *, devices, origin, text, capability="", model_hint="", expected=None,
           forbidden=None):
    return _L("routing", _rt(check), family=family,
              world={"devices": devices, "turn": {"origin": origin}},
              stimulus={"text": text, "capability": capability, "model_hint": model_hint},
              expected=expected or {}, forbidden=forbidden or {})


def routing_scenarios() -> list[dict]:
    dev = lambda d, rule: {"kind": "device", "device": d, "rule": rule}   # noqa: E731
    mystery = {"id": "mystery", "type": "unknown", "name": "?", "caps": ["open_app"]}
    tablet = {"id": "tablet-01", "type": "unknown", "name": "Tablette", "caps": []}
    r = [
        _route("1", "routing.origin", devices=_reg(), origin="phone-01", text="ouvre le navigateur",
               capability="open_app", expected=dev("phone-01", "origin")),
        _route("2", "routing.origin", devices=_reg(), origin="desktop-01", text="ouvre le navigateur",
               capability="open_app", expected=dev("desktop-01", "origin")),
        _route("3", "routing.explicit", devices=_reg(), origin="phone-01", text="ouvre Chrome sur mon PC",
               capability="open_app", expected=dev("desktop-01", "explicit")),
        _route("4", "routing.explicit", devices=_reg(), origin="desktop-01",
               text="ouvre Chrome sur mon telephone", capability="open_app",
               expected=dev("phone-01", "explicit")),
        _route("5", "routing.capability", devices=_reg(), origin="phone-01", text="controle la souris",
               capability="computer_control", expected=dev("desktop-01", "capability-only")),
        _route("6", "routing.capability", devices=_reg(), origin="desktop-01", text="prends une photo",
               capability="phone_camera", expected=dev("phone-01", "capability-only")),
        _route("7", "routing.offline", devices=_reg(pc_online=False), origin="phone-01",
               text="ouvre Chrome sur mon PC", capability="open_app",
               expected={"kind": "unavailable", "device": None}),
        _route("8", "routing.offline", devices=_reg(phone_online=False), origin="desktop-01",
               text="ouvre Chrome sur mon telephone", capability="open_app",
               expected={"kind": "unavailable", "device": None}),
        _route("9a", "routing.ambiguous", devices=_reg(), origin=None, text="ouvre le navigateur",
               capability="open_app", expected={"kind": "clarify", "rule": "ambiguous",
                                                "question": {"$ne": ""}}),
        _route("9b", "routing.ambiguous", devices=_reg(extra=[tablet]), origin="tablet-01",
               text="ouvre le navigateur", capability="open_app",
               expected={"kind": "clarify", "rule": "ambiguous"}),
        _route("9c", "routing.origin", devices=_reg(extra=[mystery]), origin="mystery",
               text="ouvre le navigateur", capability="open_app", expected=dev("mystery", "origin")),
        _route("10", "routing.here", devices=_reg(), origin="phone-01", text="ouvre le navigateur ici",
               capability="open_app", expected={"kind": "device", "device": "phone-01"}),
        _route("11", "routing.here", devices=_reg(), origin="desktop-01", text="ouvre le navigateur ici",
               capability="open_app", expected={"kind": "device", "device": "desktop-01"}),
        _route("12", "routing.other", devices=_reg(), origin="phone-01",
               text="ouvre le navigateur sur l'autre appareil", capability="open_app",
               expected={"kind": "device", "device": "desktop-01"}),
        _route("13", "routing.hint", devices=_reg(), origin="phone-01",
               text="ouvre ca sur mon ordinateur portable", expected={"hint": "pc"}),
        _route("13", "routing.hint", devices=_reg(), origin="phone-01",
               text="ouvre ca sur mon portable", expected={"hint": "android"}),
        _route("14", "routing.hint", devices=_reg(), origin="phone-01",
               text="sur mon téléphone", expected={"hint": "android"}),
        _route("14", "routing.hint", devices=_reg(), origin="phone-01",
               text="sur mon telephone", expected={"hint": "android"}),
        _route("15", "routing.explicit", devices=_reg(), origin="phone-01", text="prends une photo sur mon PC",
               capability="phone_camera", expected={"kind": "unavailable", "rule": "explicit-incapable"}),
        _route("16", "routing.capability", devices=_reg(), origin="desktop-01", text="lance la fusee",
               capability="launch_rocket", expected={"kind": "unavailable", "rule": "capability-none"}),
        _route("17", "routing.other", devices=[PHONE], origin="phone-01", text="ouvre ca sur l'autre appareil",
               capability="open_app", expected={"kind": "clarify"}),
        _route("18", "routing.model_hint", devices=_reg(pc_online=False), origin="phone-01",
               text="ouvre Chrome", capability="open_app", model_hint="pc",
               expected={"kind": "unavailable"}),
        _route("20", "routing.unknown_origin", devices=_reg(extra=[mystery]), origin="mystery",
               text="ouvre le navigateur", expected={"kind": "clarify", "rule": "unknown-origin"}),
    ]
    return r


# ── avatar/checks/scenario_matrix.mjs ────────────────────────────────────────

def face_matrix() -> dict:
    """The matrix's own dimensions, read from the Node module."""
    code = ("import('./avatar/checks/scenario_matrix.mjs')"
            ".then(m => process.stdout.write(JSON.stringify(m.MATRIX)))")
    proc = subprocess.run(["node", "-e", code], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"lecture de MATRIX impossible : {proc.stderr[-300:]}")
    return json.loads(proc.stdout)


def face_scenarios() -> list[dict]:
    m = face_matrix()
    out = []
    for intent in m["intents"]:
        for state in m["states"]:
            for speech in m["speeches"]:
                for gaze in m["gazes"]:
                    for urgent in m["urgencies"]:
                        for interrupted in m["interruptions"]:
                            out.append(make(
                                "face", source="legacy",
                                family=f"face.{intent or 'none'}",
                                stimulus={"intent": intent, "state": state, "speech": speech,
                                          "gaze": gaze, "urgent": urgent, "interrupted": interrupted},
                                expected={"violations": {"$len": 0}},
                                lineage={"legacy_ref": "avatar/checks/scenario_matrix.mjs"}))
    return out


SOURCES = {
    "context": context_scenarios,
    "routing": routing_scenarios,
    "face": face_scenarios,
}


def import_all(dest: Path = CORPUS) -> dict[str, int]:
    """Write one JSONL per source. Deterministic: re-importing gives the same bytes."""
    from .scenario import dumps
    dest.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, fn in SOURCES.items():
        items = fn()
        ids = [s["id"] for s in items]
        dupes = len(ids) - len(set(ids))
        if dupes:
            raise RuntimeError(f"{name} : {dupes} scenario(s) en double a l'import")
        (dest / f"{name}.jsonl").write_text("".join(dumps(s) + "\n" for s in items),
                                            encoding="utf-8", newline="\n")
        counts[name] = len(items)
    return counts
