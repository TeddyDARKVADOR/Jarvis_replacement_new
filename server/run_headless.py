"""
server/run_headless.py — MARK LIII on a server, with main.py untouched.

    python -m server.run_headless            # run it
    python -m server.run_headless --pairing  # print the device credential, exit

WHAT IT DOES, IN ORDER, AND WHY THE ORDER MATTERS
    1. Install two stand-ins in sys.modules. This MUST happen before main is
       imported, because main.py binds both at module level:
           main.py:47  import sounddevice as sd
           main.py:51  from ui import JarvisUI
       Afterwards it is too late: main holds direct references.

         "sounddevice" → audio_bridge  — null microphone, speaker to the phone
         "ui"          → headless_ui   — the HUD façade with no screen

       Installing "ui" is also what makes PyQt6 unnecessary on the server: the
       real ui.py is never imported, so its ~100 MB of Qt is never needed.

    2. Neutralise dashboard's firewall helper. _ensure_network_access() shells
       out to pkexec/ufw/iptables at startup (dashboard/server.py:99). On a
       desktop that is a helpful one-time prompt; under systemd there is nobody
       to answer it, and a service should not be reconfiguring the firewall of
       the machine it runs on. The VPS firewall is the deployment's business —
       see deployment/. Pass --firewall to restore the original behaviour.

    3. Subclass DashboardServer so the moment one is constructed (inside
       JarvisLive.run()) we can seed the persistent device credential, graft
       /health, /status and /ws/phone-out onto its app, and hand the UI its
       broadcast channel. main.py's `from dashboard.server import
       DashboardServer` then picks up the subclass — the import happens inside
       run(), after this patch.

    4. Build the same JarvisLive, with the same tools, memory and reconnect
       loop, and run it.

NOT DONE HERE, DELIBERATELY
    No change to main.py, dashboard/server.py, core/, memory/, actions/ or
    plugins/. `git status` after this phase lists new files only.
"""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import confirm as confirm_gate            # noqa: E402
from server import api, auth, device_api, routing    # noqa: E402
from server.device_api import DeviceHub              # noqa: E402
from server.audio_bridge import AudioHub, build_module  # noqa: E402
from server.headless_ui import HeadlessUI           # noqa: E402
from server.runtime_state import RuntimeState, State  # noqa: E402


def _install_stubs(hub: AudioHub, log) -> None:
    if "main" in sys.modules:
        # main is already bound to whatever sounddevice was in place when it was
        # imported. If that is not our stand-in, this process would run with a
        # real (or missing) sound card and there is no way to take it back —
        # so refuse loudly rather than fail later inside the session TaskGroup.
        installed = sys.modules.get("sounddevice")
        if not getattr(installed, "__headless__", False):
            raise RuntimeError(
                "main was imported before the audio/UI stand-ins were installed "
                "— it holds the real sounddevice and the real Qt UI."
            )
        return
    sys.modules["sounddevice"] = build_module(hub, log=log)
    import server.headless_ui as _headless
    sys.modules["ui"] = _headless          # `from ui import JarvisUI`


def _patch_dashboard(on_created, allow_firewall: bool, log) -> None:
    import dashboard.server as dserver

    if not allow_firewall:
        dserver._ensure_network_access = lambda *_a, **_kw: None

    base = dserver.DashboardServer

    class HeadlessDashboardServer(base):      # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            try:
                on_created(self)
            except Exception as e:
                # A failure here must not cost the whole assistant its
                # dashboard: main.py catches exceptions from the DashboardServer
                # constructor by disabling the dashboard entirely, which would
                # take the phone down with it.
                log(f"[Headless] route/pairing setup failed: {e}")

    dserver.DashboardServer = HeadlessDashboardServer


_BUILT: tuple | None = None


def build(echo: bool = True, allow_firewall: bool = False):
    """Wire everything and return (jarvis, ui, state, hub) without running.

    Split out from main() so selftest can exercise the wiring without a Gemini
    key and without opening a socket.

    Idempotent: a process has exactly one JarvisLive, one audio hub and one set
    of sys.modules stand-ins, so a second call hands back the first result
    rather than building a second assistant on top of the first."""
    global _BUILT
    if _BUILT is not None:
        return _BUILT

    state = RuntimeState(label="MARK LIII headless")
    # Who is connected, what each can do, and who spoke last. Created here so
    # both the routes and the action router share one instance — two registries
    # would mean a device that is registered for one and invisible to the other.
    device_hub = DeviceHub()
    ui    = HeadlessUI(state=state, echo=echo)
    hub   = AudioHub()

    _install_stubs(hub, log=lambda m: ui.write_log(m))

    import main  # noqa: E402  — only now is it safe

    def _remote_interrupt() -> None:
        """A client pressed INTERRUPT.

        Two things have to stop, and only the first of them is main.py's.
        `ui.on_interrupt` is the assistant's own `interrupt()` — the same
        callable the desktop HUD button uses — and it drains the queue feeding
        _play_audio. That stops audio being *produced*.

        The hub holds what has already been produced: up to _QUEUE_SLOTS frames,
        ~40 s, because Gemini generates faster than real time. Leave it and the
        listener goes on speaking the abandoned answer long after the user asked
        for silence, which reads as the interrupt having done nothing.

        Order matters: stop the source first, then throw away what it already
        emitted, or the drain races the last writes.
        """
        cb = getattr(ui, "on_interrupt", None)
        if cb is None:
            ui.write_log("SYS: Interrupt ignored — the assistant is not running yet.")
            return
        try:
            cb()
        finally:
            hub.flush()

    def _on_dashboard(dash) -> None:
        ui.bind_dashboard(dash)
        token = auth.seed_dashboard(dash)
        api.attach(
            dash,
            state=state,
            hub=hub,
            sample_rate=main.RECEIVE_SAMPLE_RATE,
            label="MARK LIII",
            log=lambda m: print(m, flush=True),
        )
        # Device identity and the routed-command channel. Two NEW routes on the
        # same app — `/ws` and `/api/device-login` are left exactly as they are,
        # which is what keeps the current Android build working untouched.
        device_api.attach(dash, device_hub, log=lambda m: print(m, flush=True))
        # The two client→server capabilities the headless host adds. Both bind a
        # remote button to a function that already existed — no second command
        # path, no second authority. `main.py` is untouched: it has already put
        # `interrupt` on the UI object (JarvisLive.__init__) and already bound
        # core/confirm.py to that UI, so both ends of each wire are in place and
        # this only joins them.
        dash.set_interrupt_callback(_remote_interrupt)
        dash.set_confirm_callback(
            lambda accepted, cid: confirm_gate.resolve(accepted, cid)
        )

        ui.write_log("SYS: /health, /status and /ws/phone-out are up.")
        ui.write_log(f"SYS: paired device …{token[-6:]} restored from "
                     f"{auth.CRED_PATH.name}.")

    _patch_dashboard(_on_dashboard, allow_firewall, log=print)

    ui.wait_for_api_key()
    jarvis = main.JarvisLive(ui)

    # Wrap the action registry so a tool call is addressed before it is run.
    #
    # After `build()` and before `run()`, because `main.py` reads the tool
    # declarations when it configures the Live session — which happens inside
    # `run()`. The wrapper delegates everything and only interposes on `run`;
    # with no device registered it forwards straight through, so a server with
    # no clients behaves exactly as it did before.
    routing.install(jarvis, device_hub, log=lambda m: print(m, flush=True))

    # The only honest answer to "is the Live session up?" is the object that
    # owns it. Everything /status reports about the link comes from here.
    state.bind_session_probe(lambda: jarvis.session is not None)
    state.bind_extra_probe(lambda: _extras(jarvis, device_hub))

    _BUILT = (jarvis, ui, state, hub)
    return _BUILT


def _extras(jarvis, device_hub: DeviceHub | None = None) -> dict:
    """Facts /status reports that only JarvisLive knows. Read-only, and every
    lookup is defensive: /status must never be the thing that breaks."""
    out: dict = {}
    if device_hub is not None:
        try:
            # No token ever reaches here: DeviceInfo.public() carries identity
            # and capabilities, and the token map is a separate dict that is
            # never exported.
            out["devices"] = device_hub.registry.public()
            out["turn_origin"] = device_hub.turn.origin_device_id or None
        except Exception:
            pass
    try:
        out["tools"] = {
            "actions": len(jarvis._action_registry.names()),
            "plugins": len(jarvis._plugin_registry.get_tool_declarations()),
        }
    except Exception:
        pass
    try:
        out["wake"] = {"enabled": jarvis._wake_enabled, "awake": jarvis._awake}
    except Exception:
        pass
    try:
        # The uplink's liveness, read from the flag main.py already keeps:
        # _relay_phone_audio sets it True on every frame from the phone and
        # False after one second of silence. Without it, /status can see the
        # phone's *downlink* socket but has no idea whether its microphone is
        # still streaming — which is exactly the question a screen-off test asks.
        out["phone_mic_streaming"] = bool(jarvis._phone_active)
    except Exception:
        pass
    try:
        # Not the handle itself — that is a credential for the conversation.
        out["session_resumable"] = jarvis._resume_handle is not None
    except Exception:
        pass
    try:
        from core import confirm as _confirm
        pending = _confirm.pending_title()
        if pending:
            out["pending_confirmation"] = pending
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="server.run_headless",
        description="Run MARK LIII without a screen or a sound card.",
    )
    ap.add_argument("--pairing", action="store_true",
                    help="print the device credential for the Android client and exit")
    ap.add_argument("--quiet", action="store_true",
                    help="do not echo HUD log lines to stdout")
    ap.add_argument("--firewall", action="store_true",
                    help="let dashboard/server.py try to open the port in the "
                         "host firewall (off by default under systemd)")
    args = ap.parse_args()

    if args.pairing:
        import socket
        print(auth.describe(host=socket.gethostname()))
        return 0

    import asyncio

    jarvis, ui, state, _hub = build(echo=not args.quiet,
                                    allow_firewall=args.firewall)

    def _term(_sig, _frm):
        # systemd sends SIGTERM on stop/restart. Raising in the main thread
        # unwinds asyncio.run the same way Ctrl-C does, so main.py's own
        # KeyboardInterrupt path runs and the session summary still gets its
        # chance to be written.
        ui.write_log("SYS: SIGTERM — shutting down.")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)

    # Watch the Live session so /status can report how many times it came back
    # and how long it held — the numbers a 24/7 run is actually judged on.
    async def _watch_link():
        while True:
            try:
                state.note_link(jarvis.session is not None)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    _original_run = jarvis.run

    async def _run_with_watch():
        asyncio.ensure_future(_watch_link())
        await _original_run()

    jarvis.run = _run_with_watch

    state.set(State.CONNECTING)
    print("[Headless] MARK LIII starting — main.py, actions, plugins and "
          "memory unchanged.", flush=True)
    try:
        asyncio.run(jarvis.run())
    except KeyboardInterrupt:
        print("\n[Headless] stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
