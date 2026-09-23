"""
server/routing.py — who runs the tool Gemini just called.

HOW THIS REACHES `main.py` WITHOUT TOUCHING IT
    `main.py` builds `self._action_registry` at line 444 and uses it in exactly
    three places: `get_tool_declarations()` when it configures the Live session,
    then `has(name)` and `run(name, args, ctx)` when a tool call arrives. Every
    one of those goes through the object, so replacing the object replaces the
    behaviour.

    `server/run_headless.py` already substitutes `sys.modules["ui"]` and
    `sys.modules["sounddevice"]` for exactly this reason. This is the same move
    one level down: after `build()` and before `run()`, `jarvis._action_registry`
    is swapped for a wrapper that delegates everything and interposes on `run`.

    Nothing in `main.py`, `core/` or `actions/` changes. `ActionRouter` is a
    drop-in for `ActionRegistry` and fails back to it whenever routing has
    nothing to say.

THE RULE THAT KEEPS THIS ADDITIVE
    If no connected device declares the action as a capability, the wrapped
    registry runs it locally — which is precisely what happens today. Routing
    only engages once a device has said "I can do this", so a server with no
    registered clients behaves exactly as it did before this file existed.

WHAT THE ROUTER DECIDES, AND WHAT IT DOES NOT
    It decides **which device**. It never decides what an action does, never
    reimplements one, and never reaches inside `actions/`. The business logic
    stays where it is; only the address changes.
"""

from __future__ import annotations

import asyncio
import copy

from .device_api import DeviceHub
from .devices import DeviceInfo, DeviceType
from .targeting import TargetKind, requested_hint, resolve

#: The optional parameter added to every tool declaration so the model can pass
#: on a device the user named. It is a *hint*: `targeting.resolve` validates it
#: against the same rules as a phrase in the text, and an offline or incapable
#: device is refused however confidently the model asked for it.
TARGET_PARAM = "target_device"

_TARGET_PARAM_SCHEMA = {
    "type": "STRING",
    "description": (
        "Optional. Set ONLY when the user explicitly named a device: "
        "'pc' (mon PC, mon ordinateur), 'android' (mon téléphone, mon portable), "
        "'here' (ici, cet appareil), 'other' (l'autre appareil). "
        "Leave unset when the user did not say where — the command then runs on "
        "the device it came from."
    ),
}


class ActionRouter:
    """A drop-in for `ActionRegistry` that first decides where to run."""

    def __init__(self, inner, hub: DeviceHub, log=print) -> None:
        self._inner = inner
        self._hub = hub
        self._log = log

    # ── the ActionRegistry surface, delegated ────────────────────────────────

    def has(self, name: str) -> bool:
        return self._inner.has(name)

    def names(self) -> set[str]:
        return self._inner.names()

    def get_tool_declarations(self) -> list[dict]:
        """The same declarations, each with one optional parameter added.

        Deep-copied: the records belong to `ActionRegistry` and to the action
        modules behind it, and mutating a schema in place would leave a
        `target_device` property in `actions/*.py`'s own TOOL dict for the rest
        of the process.
        """
        declarations = []
        for decl in self._inner.get_tool_declarations():
            decl = copy.deepcopy(decl)
            params = decl.setdefault("parameters", {"type": "OBJECT", "properties": {}})
            props = params.setdefault("properties", {})
            props.setdefault(TARGET_PARAM, copy.deepcopy(_TARGET_PARAM_SCHEMA))
            declarations.append(decl)
        return declarations

    # ── the one method that does something ───────────────────────────────────

    def run(self, name: str, parameters: dict, ctx: dict | None = None) -> str:
        """Called by `main.py` on a worker thread. Never raises.

        The threading matters: `main.py` invokes this through
        `run_in_executor`, so anything touching the event loop has to be
        marshalled back with `run_coroutine_threadsafe`.
        """
        parameters = dict(parameters or {})
        model_hint = str(parameters.pop(TARGET_PARAM, "") or "")

        registry = self._hub.registry
        turn = self._hub.turn
        # Nobody has claimed this capability: behave exactly as before — unless
        # the user named a device. A named target is never replaced, not even
        # by running it here on the server: the resolver below then refuses it
        # in words ("le téléphone ne sait pas faire …"). V1 is untouched for
        # every command that names no device.
        if not registry.with_capability(name, online_only=False) and \
                requested_hint(turn.current_text(), model_hint) is None:
            return self._inner.run(name, parameters, ctx)

        origin = turn.origin(registry)
        decision = resolve(
            registry,
            text=turn.current_text(),
            origin=origin,
            capability=name,
            model_hint=model_hint,
        )

        self._log(
            "[Route] "
            f"origin={_describe(origin)} "
            f"target={_describe(decision.device)} "
            f"capability={name} rule={decision.rule or decision.kind.value}"
        )

        if decision.kind is TargetKind.CLARIFY:
            # Handed back as the tool's result, so Gemini asks the question in
            # its own voice and the turn continues. Nothing has run.
            return decision.question or "Sur quel appareil ?"

        if decision.kind is TargetKind.UNAVAILABLE:
            # Deliberately NOT a fallback to whatever else is connected.
            return decision.detail or "Cet appareil n'est pas disponible."

        device = decision.device
        assert device is not None

        channel = self._hub.channel(device.device_id)
        if channel is None:
            # Registered, believed online, but no control socket. Saying so is
            # the honest answer; running it here instead would be the silent
            # wrong-device execution this whole file exists to prevent.
            return (
                f"{device.label} est déclaré mais son canal de commande n'est "
                "pas ouvert."
            )

        loop = self._hub.loop
        if loop is None:
            return "Le canal appareil n'est pas encore initialisé."

        try:
            future = asyncio.run_coroutine_threadsafe(
                channel.request(name, parameters), loop
            )
            result = future.result(timeout=60)
        except Exception as exc:
            self._log(f"[Route] {device.device_id} failed: {type(exc).__name__}: {exc}")
            return f"L'exécution sur {device.label} a échoué : {exc}"

        # The device is named in the result so the model can say where it
        # happened, and so a confirmation that follows cannot be about a machine
        # the user never had in mind.
        return f"[{device.label}] {result}"


def _describe(device: DeviceInfo | None) -> str:
    if device is None:
        return "unknown"
    if device.device_type is DeviceType.UNKNOWN:
        return device.device_id
    return device.device_type.value


def install(jarvis, hub: DeviceHub, log=print) -> ActionRouter:
    """Swap `jarvis._action_registry` for a router around it.

    Call after `build()` and before `run()`: the declarations are read when the
    Live session is configured, which happens inside `run()`.
    """
    inner = getattr(jarvis, "_action_registry", None)
    if inner is None:
        raise RuntimeError(
            "This JarvisLive has no _action_registry — main.py changed shape "
            "and the router has nothing to wrap."
        )
    router = ActionRouter(inner, hub, log=log)
    jarvis._action_registry = router
    log("[Route] action registry wrapped — device routing active")
    return router
