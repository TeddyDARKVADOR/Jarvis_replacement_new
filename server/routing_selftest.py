"""
Routing checks. No Gemini, no network, no phone, no real token.

Covers the fourteen cases in the brief plus the ones that came out of writing
the resolver. Every device here is synthetic and every token is the string
"test-token" — see the note at the bottom of this file.

Run with `python -m server.routing_selftest`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from server.devices import DeviceRegistry, DeviceType  # noqa: E402
from server.targeting import TargetKind, detect_hint, resolve  # noqa: E402

_PASS = "  ok  "
_FAIL = " FAIL "

#: Capabilities are action names, never invented strings.
#:
#: `open_app` and `computer_control` are real device-bound actions. `web_search`
#: is deliberately absent from both lists, and that absence is the point: it
#: runs on the VPS and means nothing in particular on either device, so nothing
#: declares it and check 22 proves it still executes locally exactly as before.
#:
#: `phone_camera` stands in for a future Android capability. It is declared by a
#: synthetic device here and by nothing in the real client — the brief is
#: explicit that unimplemented capabilities are not to be announced.
PC_CAPS = ("open_app", "computer_control")
PHONE_CAPS = ("open_app", "phone_camera")


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"[{_PASS}] {name}")
        else:
            self.failed += 1
            print(f"[{_FAIL}] {name}" + (f" - {detail}" if detail else ""))


def build(pc_online: bool = True, phone_online: bool = True) -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        "desktop-01", DeviceType.PC, "PC de travail", PC_CAPS, token="test-token-pc"
    )
    registry.register(
        "phone-01", DeviceType.ANDROID, "Redmi", PHONE_CAPS, token="test-token-phone"
    )
    registry.set_connected("desktop-01", pc_online)
    registry.set_connected("phone-01", phone_online)
    return registry


def run() -> bool:  # noqa: C901
    report = Report()
    print("MARK LIII - routing selftest\n")

    reg = build()
    pc = reg.get("desktop-01")
    phone = reg.get("phone-01")

    # ── 1-2. a generic command belongs to the device that issued it ──────────
    r = resolve(reg, text="ouvre le navigateur", origin=phone, capability="open_app")
    report.check(
        "1  origin=android, commande generique -> android",
        r.ok and r.device.device_id == "phone-01" and r.rule == "origin",
        f"{r.kind} {r.rule} {r.device}",
    )

    r = resolve(reg, text="ouvre le navigateur", origin=pc, capability="open_app")
    report.check(
        "2  origin=pc, commande generique -> pc",
        r.ok and r.device.device_id == "desktop-01" and r.rule == "origin",
        f"{r.kind} {r.rule}",
    )

    # ── 3-4. an explicit target wins over the origin ────────────────────────
    r = resolve(reg, text="ouvre Chrome sur mon PC", origin=phone, capability="open_app")
    report.check(
        "3  origin=android + « sur mon PC » -> pc",
        r.ok and r.device.device_id == "desktop-01" and r.rule == "explicit",
        f"{r.kind} {r.rule}",
    )

    r = resolve(reg, text="ouvre Chrome sur mon telephone", origin=pc, capability="open_app")
    report.check(
        "4  origin=pc + « sur mon telephone » -> android",
        r.ok and r.device.device_id == "phone-01" and r.rule == "explicit",
        f"{r.kind} {r.rule}",
    )

    # ── 5-6. a capability only one device has decides on its own ────────────
    r = resolve(reg, text="controle la souris", origin=phone, capability="computer_control")
    report.check(
        "5  origin=android, capacite PC uniquement -> pc",
        r.ok and r.device.device_id == "desktop-01" and r.rule == "capability-only",
        f"{r.kind} {r.rule}",
    )

    r = resolve(reg, text="prends une photo", origin=pc, capability="phone_camera")
    report.check(
        "6  origin=pc, capacite Android uniquement -> android",
        r.ok and r.device.device_id == "phone-01" and r.rule == "capability-only",
        f"{r.kind} {r.rule}",
    )

    # ── 7-8. an offline named target is never replaced ──────────────────────
    offline_pc = build(pc_online=False)
    r = resolve(
        offline_pc, text="ouvre Chrome sur mon PC",
        origin=offline_pc.get("phone-01"), capability="open_app",
    )
    report.check(
        "7  cible=pc hors ligne -> erreur, JAMAIS de repli sur android",
        r.kind is TargetKind.UNAVAILABLE and r.device is None,
        f"{r.kind} {r.rule} {r.detail}",
    )

    offline_phone = build(phone_online=False)
    r = resolve(
        offline_phone, text="ouvre Chrome sur mon telephone",
        origin=offline_phone.get("desktop-01"), capability="open_app",
    )
    report.check(
        "8  cible=android hors ligne -> erreur, JAMAIS de repli sur pc",
        r.kind is TargetKind.UNAVAILABLE and r.device is None,
        f"{r.kind} {r.rule} {r.detail}",
    )

    # ── 9. both capable, nothing named, no identifiable origin -> ask ───────
    #
    # This is the genuinely ambiguous case, and it took a failing test to state
    # it correctly. A command from a device that *is* identified and *can* do
    # the thing is not ambiguous — rule 3 owns it, and rule 3 comes before
    # rule 4 by design. Ambiguity is when nothing points at a device: a voice
    # turn with no origin, or an origin that cannot perform the action.
    reg_unknown = build()
    reg_unknown.register("mystery", DeviceType.UNKNOWN, "?", ("open_app",))

    r = resolve(reg, text="ouvre le navigateur", origin=None, capability="open_app")
    report.check(
        "9a deux appareils capables + aucune origine -> clarification",
        r.kind is TargetKind.CLARIFY and r.rule == "ambiguous" and bool(r.question),
        f"{r.kind} {r.rule}",
    )

    # An origin that exists but cannot do it does not get the job either.
    reg_incapable = build()
    reg_incapable.register("tablet-01", DeviceType.UNKNOWN, "Tablette", ())
    r = resolve(
        reg_incapable, text="ouvre le navigateur",
        origin=reg_incapable.get("tablet-01"), capability="open_app",
    )
    report.check(
        "9b origine identifiee mais incapable -> clarification",
        r.kind is TargetKind.CLARIFY and r.rule == "ambiguous",
        f"{r.kind} {r.rule}",
    )

    # And the converse, stated so it cannot regress: an identified origin that
    # CAN do it keeps the job even when others could too.
    r = resolve(
        reg_unknown, text="ouvre le navigateur",
        origin=reg_unknown.get("mystery"), capability="open_app",
    )
    report.check(
        "9c origine capable -> l'origine garde la main (regle 3 avant regle 4)",
        r.ok and r.device.device_id == "mystery" and r.rule == "origin",
        f"{r.kind} {r.rule}",
    )

    # ── 10-11. "ici" is the origin, whichever it is ─────────────────────────
    r = resolve(reg, text="ouvre le navigateur ici", origin=phone, capability="open_app")
    report.check(
        "10 « ici » depuis android -> android",
        r.ok and r.device.device_id == "phone-01",
        f"{r.kind} {r.rule}",
    )
    r = resolve(reg, text="ouvre le navigateur ici", origin=pc, capability="open_app")
    report.check(
        "11 « ici » depuis pc -> pc",
        r.ok and r.device.device_id == "desktop-01",
        f"{r.kind} {r.rule}",
    )

    # ── 12. "the other device" ──────────────────────────────────────────────
    r = resolve(
        reg, text="ouvre le navigateur sur l'autre appareil",
        origin=phone, capability="open_app",
    )
    report.check(
        "12 « sur l'autre appareil » depuis android -> pc",
        r.ok and r.device.device_id == "desktop-01",
        f"{r.kind} {r.rule}",
    )

    # ── the ones that came out of writing it ────────────────────────────────

    # "ordinateur portable" is a laptop; "portable" alone is the phone. The PC
    # patterns are checked first precisely so the first does not fall into the
    # second.
    report.check(
        "13 « ordinateur portable » -> pc, « portable » seul -> android",
        detect_hint("ouvre ca sur mon ordinateur portable").value == "pc"
        and detect_hint("ouvre ca sur mon portable").value == "android",
        f"{detect_hint('sur mon ordinateur portable')} / {detect_hint('sur mon portable')}",
    )

    # Accents must not decide anything.
    report.check(
        "14 « telephone » et « téléphone » se valent",
        detect_hint("sur mon téléphone") == detect_hint("sur mon telephone"),
    )

    # A named target that is connected but cannot do the thing is an error, not
    # a redirection to the device that can.
    r = resolve(reg, text="prends une photo sur mon PC", origin=phone,
                capability="phone_camera")
    report.check(
        "15 cible nommee mais incapable -> erreur, pas de redirection",
        r.kind is TargetKind.UNAVAILABLE and r.rule == "explicit-incapable",
        f"{r.kind} {r.rule}",
    )

    # Nobody at all can do it.
    r = resolve(reg, text="lance la fusee", origin=pc, capability="launch_rocket")
    report.check(
        "16 capacite que personne ne declare -> erreur claire",
        r.kind is TargetKind.UNAVAILABLE and r.rule == "capability-none",
        f"{r.kind} {r.rule}",
    )

    # "the other device" with only one device online is not resolvable.
    solo = DeviceRegistry()
    solo.register("phone-01", DeviceType.ANDROID, "Redmi", PHONE_CAPS)
    r = resolve(solo, text="ouvre ca sur l'autre appareil",
                origin=solo.get("phone-01"), capability="open_app")
    report.check(
        "17 « l'autre appareil » sans autre appareil -> clarification",
        r.kind is TargetKind.CLARIFY,
        f"{r.kind} {r.rule}",
    )

    # A phrase in the model's suggestion is accepted, but validated the same way
    # — here the named device is offline, so it is refused rather than swapped.
    r = resolve(
        offline_pc, text="ouvre Chrome",
        origin=offline_pc.get("phone-01"), capability="open_app",
        model_hint="pc",
    )
    report.check(
        "18 suggestion du modele validee, pas obeie aveuglement",
        r.kind is TargetKind.UNAVAILABLE,
        f"{r.kind} {r.rule}",
    )

    # The registry must never expose a token.
    reg_pub = build()
    public_blob = repr(reg_pub.public()) + reg_pub.summary()
    report.check(
        "19 aucun jeton ne sort du registre",
        "test-token" not in public_blob,
    )

    # An unknown origin is never silently defaulted, even with no capability.
    r = resolve(reg_unknown, text="ouvre le navigateur",
                origin=reg_unknown.get("mystery"))
    report.check(
        "20 origine inconnue, aucune capacite -> clarification",
        r.kind is TargetKind.CLARIFY and r.rule == "unknown-origin",
        f"{r.kind} {r.rule}",
    )

    # ── the router, with its real threading model ───────────────────────────
    #
    # `main.py` calls `run()` from `run_in_executor`, so the router marshals a
    # remote call back to the server's loop with `run_coroutine_threadsafe` and
    # blocks the worker thread on the answer. Testing that on a loop running in
    # another thread is the only way to exercise the arrangement that actually
    # ships; a single-threaded test would pass while deadlocking in production.
    _router_checks(report)

    total = report.passed + report.failed
    print(f"\n{report.passed}/{total}")
    return report.failed == 0


class _FakeRegistry:
    """Stands in for `core.action_loader.ActionRegistry`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def has(self, name: str) -> bool:
        return True

    def names(self) -> set[str]:
        return {"open_app", "web_search", "computer_control"}

    def get_tool_declarations(self) -> list[dict]:
        return [{
            "name": "open_app",
            "description": "Open an application.",
            "parameters": {"type": "OBJECT", "properties": {
                "app": {"type": "STRING"},
            }},
        }]

    def run(self, name: str, parameters: dict, ctx=None) -> str:
        self.calls.append((name, dict(parameters)))
        return f"local:{name}"


class _FakeChannel:
    """A device that answers instantly, recording what it was asked."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.requests: list[tuple[str, dict]] = []

    async def request(self, action: str, parameters: dict, timeout: float = 45.0) -> str:
        self.requests.append((action, dict(parameters)))
        return f"remote:{action}"

    def fail_all(self, reason: str) -> None:
        pass


def _router_checks(report: "Report") -> None:  # noqa: C901
    import asyncio
    import threading

    from server.device_api import DeviceHub
    from server.routing import TARGET_PARAM, ActionRouter

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    try:
        hub = DeviceHub()
        hub.loop = loop
        hub.registry.register("desktop-01", DeviceType.PC, "PC", PC_CAPS)
        hub.registry.register("phone-01", DeviceType.ANDROID, "Redmi", PHONE_CAPS)

        inner = _FakeRegistry()
        router = ActionRouter(inner, hub, log=lambda _m: None)

        # The declarations gain the optional hint without the action files ever
        # seeing it.
        decls = router.get_tool_declarations()
        original = inner.get_tool_declarations()[0]
        report.check(
            "21 target_device est ajoute aux declarations, sans muter l'original",
            TARGET_PARAM in decls[0]["parameters"]["properties"]
            and TARGET_PARAM not in original["parameters"]["properties"],
        )

        # Nobody claims `web_search`: the wrapped registry runs it, exactly as
        # before this file existed. This is the non-regression that keeps the
        # whole feature additive.
        inner.calls.clear()
        result = router.run("web_search", {"q": "meteo"})
        report.check(
            "22 capacite non revendiquee -> execution locale inchangee",
            result == "local:web_search" and inner.calls == [("web_search", {"q": "meteo"})],
            result,
        )

        # A claimed capability with a reachable device goes there instead.
        channel = _FakeChannel("desktop-01")
        hub.channels["desktop-01"] = channel
        hub.registry.set_connected("desktop-01", True)
        hub.registry.set_connected("phone-01", False)
        hub.turn.set("desktop-01", "ouvre le navigateur")

        inner.calls.clear()
        result = router.run("open_app", {"app": "chrome"})
        report.check(
            "23 capacite revendiquee -> execution distante, pas locale",
            result.startswith("[PC] remote:open_app")
            and channel.requests == [("open_app", {"app": "chrome"})]
            and inner.calls == [],
            f"{result} / {channel.requests}",
        )

        # The hint is consumed by the router and never reaches the action.
        channel.requests.clear()
        router.run("open_app", {"app": "chrome", TARGET_PARAM: "pc"})
        report.check(
            "24 target_device est consomme, jamais transmis a l'action",
            channel.requests == [("open_app", {"app": "chrome"})],
            str(channel.requests),
        )

        # A named target that is offline is refused, and nothing runs anywhere.
        channel.requests.clear()
        inner.calls.clear()
        result = router.run("open_app", {"app": "chrome", TARGET_PARAM: "android"})
        report.check(
            "25 cible nommee hors ligne -> refus, aucune execution nulle part",
            "connect" in result.lower()
            and channel.requests == []
            and inner.calls == [],
            result,
        )

        # Declared, believed online, but no socket: say so rather than run it
        # here, which would be the silent wrong-device execution.
        hub.registry.set_connected("phone-01", True)
        channel.requests.clear()
        inner.calls.clear()
        result = router.run("phone_camera", {})
        report.check(
            "26 appareil declare mais canal absent -> refus explicite",
            "canal" in result.lower() and inner.calls == [],
            result,
        )

    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    # ── the client's own barrier ────────────────────────────────────────────
    #
    # The server already resolved a target. These exist for the case where that
    # resolution was wrong or a message was misdelivered: two independent
    # refusals mean a wrong-device execution needs two simultaneous faults.
    sys.path.insert(0, str(_REPO_ROOT))
    from client_desktop.device import LocalExecutor

    executor = LocalExecutor("desktop-01", {"open_app"}, logger=lambda _m: None)

    result = executor.execute("open_app", {}, target_device_id="phone-01")
    report.check(
        "27 le client refuse une commande adressee a un autre appareil",
        result.startswith("Refusé"),
        result,
    )

    result = executor.execute("computer_control", {}, target_device_id="desktop-01")
    report.check(
        "28 le client refuse une action hors de ses capacites declarees",
        result.startswith("Refusé"),
        result,
    )

    report.check(
        "29 une commande correctement adressee n'est pas refusee d'emblee",
        not executor.execute(
            "open_app", {"app": "__jarvis_selftest_nonexistent__"},
            target_device_id="desktop-01",
        ).startswith("Refusé"),
    )


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
