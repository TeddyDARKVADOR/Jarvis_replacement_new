"""
eval_lab/full.py — the FULL tier: the real router, on its real threading model.

FAST calls `targeting.resolve()` — the decision. FULL calls
`server.routing.ActionRouter.run()` — the decision AND everything around it:
the `target_device` hint popped from the model's arguments, the turn context
and its 180 s freshness, the "claimed by nobody -> runs locally" rule, the
control channel that may be missing, the hop from a worker thread back onto
the event loop. That is where the seams are, and FAST cannot see them.

The arrangement is the one server/routing_selftest.py already uses (a loop in
a thread, the router called from another thread, as main.py does through
run_in_executor). Reused, not reinvented: the lab adds the scenario around it.

Nothing here opens a Gemini session, a socket, or a device. The channel is a
recorder; the inner registry is a recorder. What the trace shows is what the
router asked of them.
"""
from __future__ import annotations

import asyncio
import threading

from .world import SimClock, SutCrash, isolated


class _Inner:
    """Stands in for core.action_loader.ActionRegistry: records local runs."""

    def __init__(self, names):
        self.names_ = set(names)
        self.calls: list = []

    def has(self, name):
        return name in self.names_

    def names(self):
        return set(self.names_)

    def get_tool_declarations(self):
        return [{"name": n, "description": n, "parameters": {"type": "OBJECT", "properties": {}}}
                for n in sorted(self.names_)]

    def run(self, name, parameters, ctx=None):
        self.calls.append({"action": name, "parameters": dict(parameters)})
        return f"local:{name}"


class _Channel:
    def __init__(self, device_id):
        self.device_id = device_id
        self.requests: list = []

    async def request(self, action, parameters, timeout=45.0):
        self.requests.append({"action": action, "parameters": dict(parameters)})
        return f"remote:{action}"

    def fail_all(self, reason):
        pass


def run_router(s: dict) -> dict:
    from server.device_api import DeviceHub
    from server.devices import DeviceType
    from server.routing import TARGET_PARAM, ActionRouter

    world, stim = s["world"], s["stimulus"]
    clock = SimClock((world.get("time") or {}).get("local") or "2026-09-23T14:00:00")
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        with isolated(clock):
            hub = DeviceHub()
            hub.loop = loop
            channels = {}
            for d in world.get("devices") or []:
                hub.registry.register(d["id"], DeviceType.parse(d.get("type", "unknown")),
                                      d.get("name", d["id"]), d.get("caps", []))
                from dataclasses import replace
                hub.registry._devices[d["id"]] = replace(
                    hub.registry._devices[d["id"]], connected=bool(d.get("online", True)),
                    last_seen=clock.mono - float(d.get("last_seen_ago_s", 0.0)))
                if d.get("channel", True):
                    channels[d["id"]] = hub.channels[d["id"]] = _Channel(d["id"])
            turn = world.get("turn") or {}
            if turn.get("origin"):
                clock.mono -= float(turn.get("ago_s", 0.0))
                hub.turn.set(turn["origin"], turn.get("text", ""))
                clock.mono += float(turn.get("ago_s", 0.0))
            inner = _Inner(world.get("local_tools") or [stim["tool"]])
            logs: list[str] = []
            router = ActionRouter(inner, hub, log=logs.append)
            params = dict(stim.get("parameters") or {})
            if stim.get("target_device"):
                params[TARGET_PARAM] = stim["target_device"]
            box: dict = {}

            def worker():                       # main.py: run_in_executor
                try:
                    box["result"] = router.run(stim["tool"], params, None)
                except Exception as e:          # noqa: BLE001
                    box["error"] = e

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=10)
            if t.is_alive():
                raise RuntimeError("le routeur n'a pas rendu la main en 10 s (interblocage ?)")
            if "error" in box:          # raised inside the router, on the worker thread
                raise SutCrash("routing.ActionRouter.run", box["error"])
            remote = {k: c.requests for k, c in channels.items() if c.requests}
            executed = (["local"] if inner.calls else []) + sorted(remote)
            return {
                "result": box.get("result"), "executed_on": executed,
                "local_calls": inner.calls, "remote_calls": remote,
                "route_log": [l for l in logs if l.startswith("[Route] origin")],
                "hint_leaked": any(TARGET_PARAM in c["parameters"] for c in inner.calls)
                or any(TARGET_PARAM in r["parameters"] for rs in remote.values() for r in rs),
                "online": sorted(d.device_id for d in hub.registry.online()),
            }
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()
