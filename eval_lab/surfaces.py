"""
eval_lab/surfaces.py — a scenario in, the real code's answer out.

Each surface builds the objects the scenario describes, calls the REAL code
through `world.sut()`, and returns a trace: plain JSON, nothing the lab
computed on JARVIS's behalf. No surface decides whether the answer is right —
that is oracle.py's job, and keeping the two apart is what stops a simulator
from quietly agreeing with whatever it simulates.

    situation   context.situation.derive           facts -> Situation, Route
    policy      derive + ProactivityPolicy.decide  priority -> Channel
    sequence    policy + DeferralQueue + server.alerts over simulated time
    routing     server.targeting.resolve           which device runs this
    face        avatar/ engine under Node          see face.py
"""
from __future__ import annotations

from dataclasses import replace

from .world import SimClock, isolated, sut

# ── context/ ─────────────────────────────────────────────────────────────────


def _device_state(phone: dict | None, clock: SimClock):
    """`age_s: None` (or no phone at all) = never reported: reported_at stays 0."""
    from context.model import DeviceState
    dev = DeviceState()
    if phone:
        _apply_phone(dev, phone)
        if phone.get("age_s") is not None:
            dev.reported_at = clock.epoch - float(phone["age_s"])
    return dev


def _apply_phone(dev, phone: dict) -> None:
    from context.model import Activity, Ringer
    for key, value in phone.items():
        if key == "age_s":
            continue
        if key == "ringer":
            value = Ringer(value)
        elif key == "activity":
            value = Activity(value)
        elif key == "bluetooth_devices":
            value = list(value)
        setattr(dev, key, value)


def _thresholds(world: dict):
    from context.situation import Thresholds
    th = Thresholds()
    for key, value in (world.get("thresholds") or {}).items():
        setattr(th, key, value)
    return th


def _policy(world: dict):
    from context.model import Channel, Priority, Situation
    from context.policy import PolicyConfig, ProactivityPolicy
    raw = world.get("policy") or {}
    overrides = {
        Situation(sit): {Priority(p): Channel(ch) for p, ch in row.items()}
        for sit, row in (raw.get("overrides") or {}).items()
    }
    cfg = PolicyConfig(wake_for_critical=raw.get("wake_for_critical", True), overrides=overrides)
    if "cooldowns" in raw:
        cfg.cooldowns = {Priority(p): float(v) for p, v in raw["cooldowns"].items()}
    return ProactivityPolicy(cfg)


def _snapshot(phone: dict | None, world: dict, clock: SimClock, force: dict | None):
    from context.model import Route, Situation, SystemState
    from context.situation import derive, time_state
    th = _thresholds(world)
    dev = _device_state(phone, clock)
    tstate = sut("context.time_state", time_state, clock.local, th)
    snap = sut("context.derive", derive, dev, tstate,
               SystemState(facts=dict(world.get("system") or {})), th, clock.epoch)
    if force:
        if force.get("situation"):
            snap.situation = Situation(force["situation"])
        if force.get("route"):
            snap.route = Route(force["route"])
    return snap


def _snap_trace(snap) -> dict:
    return {
        "situation": snap.situation.value,
        "route": snap.route.value,
        "reasons": list(snap.reasons),
        "reason": " ; ".join(snap.reasons),
        "quiet_hours": snap.time.quiet_hours,
        "period": snap.time.period,
    }


def _decision_trace(d) -> dict:
    return {
        "channel": d.channel.value, "route": d.route.value,
        "situation": d.situation.value, "priority": d.priority.value,
        "speaks": d.speaks, "silent": d.silent, "reason": d.reason,
    }


def run_situation(s: dict) -> dict:
    world = s["world"]
    clock = SimClock((world.get("time") or {}).get("local") or _default_time())
    with isolated(clock):
        return _snap_trace(_snapshot(world.get("phone"), world, clock, world.get("force")))


def run_policy(s: dict) -> dict:
    from context.model import Priority
    world = s["world"]
    clock = SimClock((world.get("time") or {}).get("local") or _default_time())
    with isolated(clock):
        snap = _snapshot(world.get("phone"), world, clock, world.get("force"))
        policy = _policy(world)
        for h in world.get("delivered") or []:
            sut("policy.note_delivered", policy.note_delivered,
                Priority(h["priority"]), clock.epoch - float(h["ago_s"]))
        d = sut("policy.decide", policy.decide, Priority(s["stimulus"]["priority"]), snap, clock.epoch)
        out = _snap_trace(snap)
        out.update(_decision_trace(d))
        # The same world, all four priorities, each on a fresh policy with the
        # same history: what "raising the priority alone" does (decision 5).
        ladder = {}
        for p in Priority:
            fresh = _policy(world)
            for h in world.get("delivered") or []:
                fresh.note_delivered(Priority(h["priority"]), clock.epoch - float(h["ago_s"]))
            dp = sut("policy.decide", fresh.decide, p, snap, clock.epoch)
            ladder[p.value] = {"channel": dp.channel.value, "reason": dp.reason}
        out["ladder"] = ladder
        return out


def _default_time() -> str:
    from .scenario import DEFAULT_TIME
    return DEFAULT_TIME


# ── sequences ────────────────────────────────────────────────────────────────


class _FakeDash:
    """Stands in for the dashboard `server/alerts.py` hands to the notify hub.
    It only has to exist: with no running loop the hub records and does not
    send, which is observable through the hub's own `_recent`."""

    async def broadcast(self, _event):   # pragma: no cover - never awaited here
        return None


def _payload_repr(p):
    return p if isinstance(p, (str, int, float, bool, type(None))) else dict(p) if isinstance(p, dict) else repr(p)


def run_sequence(s: dict) -> dict:
    """Interpret `events` against one policy and one queue, on simulated time.

    `alerts` runs the REAL `server.alerts.route_monitor_alerts`, with the
    context singletons pointed at this sequence's own store, policy and queue,
    so what it releases and what it holds is what production would.
    """
    from context.model import Priority
    from context.policy import DeferralQueue

    world = s["world"]
    clock = SimClock((world.get("time") or {}).get("local") or _default_time())
    steps: list[dict] = []
    with isolated(clock):
        phone = dict(world["phone"]) if world.get("phone") is not None else None
        phone_at = clock.epoch - float(phone["age_s"]) if phone and phone.get("age_s") is not None else None
        force = dict(world.get("force") or {})
        system = dict(world.get("system") or {})
        policy = _policy(world)
        queue = DeferralQueue(**(world.get("queue") or {}))
        for h in world.get("delivered") or []:
            policy.note_delivered(Priority(h["priority"]), now=clock.epoch - float(h["ago_s"]))

        def current_phone():
            if phone is None:
                return None
            p = dict(phone)
            p["age_s"] = None if phone_at is None else max(0.0, clock.epoch - phone_at)
            return p

        def snap_now():
            return _snapshot(current_phone(), {**world, "system": system}, clock, force)

        for ev in s["events"]:
            op = ev["op"]
            step: dict = {"op": op}
            if op == "world":
                if "time" in ev:
                    clock.set_local(ev["time"]["local"])
                if "at" in ev:
                    clock.next_time_of_day(ev["at"])
                if "phone" in ev:
                    if ev["phone"] is None:
                        phone, phone_at = None, None
                    else:
                        phone = {**(phone or {}), **ev["phone"]}
                        age = ev["phone"].get("age_s", 0.0)
                        phone_at = None if age is None else clock.epoch - float(age)
                if "force" in ev:
                    force = dict(ev["force"] or {})
                if "system" in ev:
                    system = {**system, **ev["system"]}
                step.update(_snap_trace(snap_now()))
            elif op == "advance":
                clock.advance(float(ev["s"]))
                step["t"] = clock.local.isoformat()
            elif op == "decide":
                snap = snap_now()
                d = sut("policy.decide", policy.decide, Priority(ev["priority"]), snap, clock.epoch)
                step.update(_decision_trace(d))
                if ev.get("deliver_if_speaks") and d.speaks:
                    # main.py:1575 — a check-in that spoke starts the cooldown.
                    sut("policy.note_delivered", policy.note_delivered, Priority(ev["priority"]), clock.epoch)
                    step["delivered"] = True
                if ev.get("push_if_deferred") and d.channel.value == "DEFER":
                    queue.push(ev.get("payload", ev["priority"]), Priority(ev["priority"]),
                               d.reason, now=clock.epoch)
                    step["pushed"] = True
            elif op == "deliver":
                sut("policy.note_delivered", policy.note_delivered, Priority(ev["priority"]), clock.epoch)
            elif op == "push":
                queue.push(ev.get("payload", ev["priority"]), Priority(ev["priority"]),
                           ev.get("reason", ""), now=clock.epoch)
            elif op == "release":
                ready = sut("queue.release", queue.release, snap_now(), policy, clock.epoch)
                step["released"] = [_payload_repr(r.payload) for r in ready]
                step["priorities"] = [r.priority.value for r in ready]
            elif op == "alerts":
                step.update(_run_alerts(ev, phone_now=current_phone(), world=world,
                                        system=system, clock=clock, policy=policy, queue=queue,
                                        force=force))
            step["held"] = [_payload_repr(d.payload) for d in queue.peek()]
            steps.append(step)
    return {"steps": steps, "final": {"held": steps[-1]["held"] if steps else []}}


def _run_alerts(ev, *, phone_now, world, system, clock, policy, queue, force) -> dict:
    """One pass of the real monitor-alert path, sharing this sequence's state."""
    import context as ctx
    from context.store import ContextStore
    import server.notify as notify_mod

    if force:
        # server/alerts.py reads the store, which derives its own situation;
        # a forced situation would be a lie it never sees. Refuse, loudly.
        from .scenario import Invalid
        raise Invalid(["un evenement 'alerts' ne peut pas suivre un 'force'"])

    store = ContextStore(thresholds=_thresholds(world))
    if phone_now is not None:
        store._device = _device_state(phone_now, clock)
    facts = dict(system)
    store.bind_system_probe(lambda: facts)
    ctx._store, ctx._policy, ctx._queue = store, policy, queue

    from server.alerts import route_monitor_alerts
    before = [_payload_repr(d.payload) for d in queue.peek()]
    # Past max_age the queue drops an item on purpose ("personne ne veut la
    # meteo d'hier"). That is expiry, not loss, and must not be reported as one.
    expired = [_payload_repr(d.payload) for d in queue.peek()
               if (clock.epoch - d.queued_at) > queue._max_age_s]
    spoken = sut("alerts.route_monitor_alerts", route_monitor_alerts,
                 list(ev.get("alerts") or []), _FakeDash() if ev.get("dashboard", True) else None)
    notes = list(notify_mod.get_hub()._recent)
    after = [_payload_repr(d.payload) for d in queue.peek()]
    notified = [{"priority": n.priority, "title": n.title, "text": n.text} for n in notes]
    left = [p for p in before if p not in after]
    delivered_titles = {n["title"] for n in notified}
    lost = [p for p in left if p not in expired
            and not (isinstance(p, dict) and p.get("title") in delivered_titles)]
    return {"spoken": list(spoken), "notified": notified,
            "left_queue": left, "expired": expired, "lost": lost}


# ── server/targeting ─────────────────────────────────────────────────────────


def run_routing(s: dict) -> dict:
    from server.devices import DeviceRegistry, DeviceType
    from server.targeting import detect_hint, resolve

    world, stim = s["world"], s["stimulus"]
    clock = SimClock((world.get("time") or {}).get("local") or _default_time())
    with isolated(clock):
        registry = DeviceRegistry()
        for d in world.get("devices") or []:
            registry.register(d["id"], DeviceType.parse(d.get("type", "unknown")),
                              d.get("name", ""), d.get("caps", []))
            # register() stamps "connected, seen now". The scenario may say
            # otherwise; write its state directly rather than replaying a
            # history of calls whose only purpose would be to reach it.
            registry._devices[d["id"]] = replace(
                registry._devices[d["id"]],
                connected=bool(d.get("online", True)),
                last_seen=clock.mono - float(d.get("last_seen_ago_s", 0.0)),
            )
        origin_id = (world.get("turn") or {}).get("origin")
        origin = registry.get(origin_id) if origin_id else None
        text = stim.get("text", "")
        r = sut("targeting.resolve", resolve, registry, text=text, origin=origin,
                capability=stim.get("capability", ""), model_hint=stim.get("model_hint", ""))
        hint = sut("targeting.detect_hint", detect_hint, text)
        return {
            "kind": r.kind.value, "rule": r.rule, "ok": r.ok,
            "device": r.device.device_id if r.device else None,
            "device_type": r.device.device_type.value if r.device else None,
            "question": r.question, "detail": r.detail,
            "hint": hint.value if hint else None,
            "online": sorted(d.device_id for d in registry.online()),
        }


def run_router(s: dict) -> dict:
    from .full import run_router as _full
    return _full(s)


RUNNERS = {
    "router": run_router,
    "situation": run_situation,
    "policy": run_policy,
    "sequence": run_sequence,
    "routing": run_routing,
}
