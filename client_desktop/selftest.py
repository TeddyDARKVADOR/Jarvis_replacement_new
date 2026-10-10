"""
Offline checks. No Gemini, no microphone, no display, no network, no server.

Modelled on `server/selftest.py`, including the part that matters most: it fails
if this package has drifted into touching something it must not. Three of the
checks here are not tests of behaviour at all but of *architecture*, and they
are a large part of why this file exists:

* **14** fails if anything under `client_desktop/` imports `main` or a Gemini
  SDK. That is the "no second session" promise made mechanical: it cannot be
  quietly undone by someone reaching for `JarvisLive` to save an afternoon.

* **15** fails if anything reaches into `core/` beyond the two modules this
  client is allowed to reuse, so a second brain cannot arrive by the back door.

* **19** fails if `git status` reports an uncommitted modification to
  `main.py`, `dashboard/`, `core/`, `memory/`, `actions/`, `plugins/` or
  `requirements.txt` — the brain is changed deliberately, in its own commit,
  never as a side effect of working on this client.

Checks 16-17 are the device capabilities: a capability is a promise the router
acts on, so every declared name must be a real action that actually imported.

Checks 20-24 are the arrival of an `avatar` directive - the face JARVIS chose
for what he is saying - from the wire to the HUD's face driver. They cover the
one thing that is genuinely easy to get wrong: `/ws` replays its last 50 events
to a client that connects, and a face replayed at noon is a reaction to nothing.

Checks 25-33 are the HUD: the controller's contract with the window, the page
and its files, the settings page, and `main.py`'s launcher block.

Run it with `python -m client_desktop --selftest`.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from dataclasses import replace as dataclass_replace
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PASS = "  ok  "
_FAIL = " FAIL "

#: The paths `server/selftest.py` guards. Kept in sync by hand, which is fine:
#: the list has not changed since the headless layer was written, and a copy
#: that is wrong fails loudly rather than silently passing.
GUARDED = (
    "main.py", "dashboard", "core", "memory", "actions", "plugins",
    "requirements.txt",
)

#: Importing any of these would mean this client had grown a brain.
FORBIDDEN_IMPORTS = ("main", "google.genai", "google_genai")


def _is_core_module(name: str) -> bool:
    """True only for the repository's `core` package - never for a module that
    merely begins with those four letters."""
    return name == "core" or name.startswith("core.")


class _Report:
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

    def fail(self, name: str, detail: str) -> None:
        self.check(name, False, detail)


def run() -> bool:  # noqa: C901
    report = _Report()
    print("JARVIS Desktop - selftest\n")

    # -- 1. every module imports ---------------------------------------------
    try:
        from client_desktop import (  # noqa: F401
            app, audio, autostart, config, device, net, protocol,
            reconnect, single_instance, state, wake,
        )
        from client_desktop.hud import (  # noqa: F401
            boot, face, metrics, scheme, settings, window,
        )
        from client_desktop.ui import (  # noqa: F401
            avatar_scheme, debug, theme, tray,
        )

        report.check("1  every module imports", True)
    except Exception as exc:
        report.fail("1  every module imports", repr(exc))
        print("\nNothing downstream is worth trying.")
        return False

    from client_desktop import audio as audio_mod
    from client_desktop import autostart as autostart_mod
    from client_desktop import config as config_mod
    from client_desktop import protocol as P
    from client_desktop.reconnect import BACKOFF_STEPS, backoff_seconds
    from client_desktop.state import (
        AssistantState, JarvisStore, LinkState, Message, Snapshot,
    )
    from client_desktop.wake import Gate

    # -- 2. the endpoints are the documented ones ----------------------------
    endpoint = P.ServerEndpoint(host="vps.tailnet-abc.ts.net", port=8000)
    urls_ok = (
        endpoint.device_login.endswith("/api/device-login")
        and endpoint.health.endswith("/health")
        and endpoint.events("t").startswith("ws://")
        and "/ws?token=t" in endpoint.events("t")
        and "/ws/phone-audio?token=" in endpoint.mic_uplink("t")
        and "/ws/phone-out?token=" in endpoint.audio_downlink("t")
    )
    report.check("2  endpoint URLs match PROTOCOL.md", urls_ok)

    # A bearer is base64-ish and can contain '+' and '/'. Unescaped, those
    # become a space and a path separator, and the socket is refused with a
    # message that blames the token.
    encoded = endpoint.events("a+b/c=d")
    report.check(
        "3  the bearer is URL-encoded in the query",
        "a%2Bb%2Fc%3Dd" in encoded,
        encoded,
    )

    # -- 4. the audio format is the server's, not ours -----------------------
    report.check(
        "4  uplink is 16 kHz / 2048-byte frames (main.py CHUNK_SIZE)",
        P.UPLINK_SAMPLE_RATE == 16_000 and P.UPLINK_FRAME_BYTES == 2048,
    )

    # -- 5. backoff: shape and bounds ----------------------------------------
    import random

    rng = random.Random(0)
    bounds_ok = True
    for attempt in range(1, 12):
        step = BACKOFF_STEPS[min(attempt - 1, len(BACKOFF_STEPS) - 1)]
        for _ in range(50):
            value = backoff_seconds(attempt, rng)
            if not (1 <= value <= step * 1.2 + 1):
                bounds_ok = False
    report.check("5  backoff stays within its step +/-20 %", bounds_ok)
    report.check(
        "6  backoff caps the delay, never the attempts",
        backoff_seconds(500, rng) <= 37,
    )

    # -- 7. the eight states the UI names ------------------------------------
    expected = {
        (LinkState.DISCONNECTED, AssistantState.UNKNOWN): "OFFLINE",
        (LinkState.CONNECTING, AssistantState.UNKNOWN): "CONNECTING",
        (LinkState.RECONNECTING, AssistantState.UNKNOWN): "RECONNECTING",
        (LinkState.ERROR, AssistantState.UNKNOWN): "ERROR",
        (LinkState.CONNECTED, AssistantState.UNKNOWN): "CONNECTED",
        (LinkState.CONNECTED, AssistantState.LISTENING): "LISTENING",
        (LinkState.CONNECTED, AssistantState.THINKING): "THINKING",
        (LinkState.CONNECTED, AssistantState.SPEAKING): "SPEAKING",
    }
    states_ok = all(
        Snapshot(link=link, assistant=assistant).display_state == name
        for (link, assistant), name in expected.items()
    )
    report.check("7  all eight display states map correctly", states_ok)

    # A disconnected client must not show what MARK LIII was last doing.
    report.check(
        "8  the link outranks the assistant state",
        Snapshot(
            link=LinkState.RECONNECTING, assistant=AssistantState.SPEAKING
        ).display_state == "RECONNECTING",
    )

    # -- 9. the store --------------------------------------------------------
    store = JarvisStore()
    store.add_message(Message(from_jarvis=False, text="bonjour"))
    store.add_message(Message(from_jarvis=True, text="Je vous ecoute."))
    store.count_sent(2048)
    store.count_received(9600)
    store.set_confirmation("abc", "Supprimer", "3 fichiers", 90)
    snapshot = store.snapshot()
    store_ok = (
        snapshot.last_spoken_line == "Je vous ecoute."
        and snapshot.frames_sent == 1
        and snapshot.bytes_received == 9600
        and snapshot.awaiting_confirmation
        and snapshot.confirmation_deadline > 0
    )
    store.clear_confirmation()
    report.check(
        "9  the store records turns, counters and the confirmation",
        store_ok and not store.snapshot().awaiting_confirmation,
    )

    # -- 10-11. levels and the gate ------------------------------------------
    import numpy as np

    silence = np.zeros(1024, dtype=np.int16)
    loud = np.ones(1024, dtype=np.int16) * 6000
    report.check(
        "10 pcm_level floors silence and saturates on a shout",
        audio_mod.pcm_level(silence) == 0.0 and audio_mod.pcm_level(loud) == 1.0,
    )

    import time as _time

    gate = Gate(seconds=0.01)
    gate.open()
    was_open = gate.is_open
    _time.sleep(0.03)
    gate.tick()
    closed_on_its_own = not gate.is_open
    gate.hold(True)
    held = gate.is_open
    _time.sleep(0.03)
    gate.tick()
    still_held = gate.is_open
    gate.close()
    report.check(
        "11 the gate opens, expires on its own, and a held gate does not",
        was_open and closed_on_its_own and held and still_held and not gate.is_open,
    )

    # -- 12. settings round-trip ---------------------------------------------
    with tempfile.TemporaryDirectory() as temp:
        saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = temp
        try:
            settings = config_mod.Settings(
                host="h.ts.net", device_token="tok", port=8123
            )
            written = config_mod.save(settings)
            reloaded = config_mod.load()
            round_trip = (
                written
                and reloaded.host == "h.ts.net"
                and reloaded.port == 8123
                and reloaded.is_configured
            )
            # A corrupt file must yield defaults, not an exception: this client
            # comes up at login with nobody present to read a traceback.
            config_mod.settings_path().write_text("{ not json", encoding="utf-8")
            survives_corruption = config_mod.load().host == ""
        finally:
            if saved is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = saved
    report.check("12 settings round-trip and survive corruption",
                 round_trip and survives_corruption)

    # -- 13. autostart, without writing anything -----------------------------
    command = autostart_mod.command()
    report.check(
        "13 the startup command points at launch.pyw with pythonw",
        command.count('"') == 4
        and "launch.pyw" in command
        and autostart_mod.launcher_path().is_file(),
        command,
    )

    # -- 14-15. the architecture checks --------------------------------------
    offenders: list[str] = []
    core_imports: set[str] = set()
    for path in sorted((_REPO_ROOT / "client_desktop").rglob("*.py")):
        # Skipped before parsing, not after: this file names the very modules
        # it forbids, and a parse failure here would be reported as an import.
        if path.name == "selftest.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            offenders.append(f"{path.name}: {exc}")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                # `node.level == 0` matters: `from .core_widget import ...` is
                # this package's own UI, not the repository's `core/`.
                names = [node.module or ""]
            for name in names:
                if name in FORBIDDEN_IMPORTS or name.split(".")[0] == "main":
                    offenders.append(f"{path.name} imports {name}")
                if _is_core_module(name):
                    core_imports.add(name)

    report.check(
        "14 nothing here imports main.py or a Gemini SDK",
        not offenders,
        "; ".join(offenders),
    )
    # `core.action_loader` joined `core.wake_word` when device routing landed,
    # and the reason is worth stating: a routed action must behave identically
    # whether Oracle ran it or this client did, which means running *the same
    # registry over the same files*. Copying an action into `client_desktop/`
    # would be the alternative, and two divergent copies of `computer_control`
    # is a far worse outcome than one extra import.
    #
    # The list stays closed on purpose. Anything else from core/ - a model
    # client, a speech engine - would be a second brain arriving by the back
    # door, and this is what refuses it.
    ALLOWED_CORE = {"core.wake_word", "core.action_loader"}
    report.check(
        "15 only wake_word and action_loader are reached into core/",
        core_imports <= ALLOWED_CORE,
        f"also imports {sorted(core_imports - ALLOWED_CORE)}",
    )

    # -- 16a. capabilities are proven, never asserted ------------------------
    #
    # A capability is a promise the router acts on: a device that declares
    # `computer_control` will be sent mouse commands. So the declared set must
    # be a subset of what actually imported here, and every name must be a real
    # action — never an invented string like "browser.open".
    from client_desktop.device import DEVICE_BOUND_ACTIONS, discover_capabilities

    declared = set(discover_capabilities(logger=lambda _l: None))
    try:
        from core.action_loader import discover_actions

        real_actions = discover_actions(
            _REPO_ROOT / "actions", logger=lambda _l: None
        ).names()
    except Exception:
        real_actions = set()

    report.check(
        "16 toute capacite declaree est une action reelle et chargee",
        declared <= real_actions and declared <= set(DEVICE_BOUND_ACTIONS),
        f"declarees={sorted(declared)} inconnues={sorted(declared - real_actions)}",
    )

    # Network-only actions must never be device capabilities: declaring
    # `web_search` on two devices invents an ambiguity where none exists.
    report.check(
        "17 les actions non liees a l'appareil ne sont pas declarees",
        not ({"web_search", "weather_report", "flight_finder", "reminder"} & declared),
        f"declarees={sorted(declared)}",
    )

    # -- 18. shutdown must not write the settings file -----------------------
    #
    # A regression guard for a bug that cost a working pairing: `shutdown()`
    # used to call `save(self.settings)`, so any process holding an in-memory
    # Settings - a UI test, a future headless mode - overwrote the user's
    # configured file on the way out, and the next launch came up unpaired with
    # nothing on screen to explain it. Settings are written when changed.
    shutdown_saves = False
    try:
        app_tree = ast.parse(
            (_REPO_ROOT / "client_desktop" / "app.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "shutdown":
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "save"
                    ):
                        shutdown_saves = True
    except Exception as exc:
        report.fail("18 shutdown does not write settings", repr(exc))
    else:
        report.check(
            "18 shutdown does not overwrite the settings file",
            not shutdown_saves,
            "shutdown() calls save() - an unpaired process would wipe the pairing",
        )

    # -- 19. non-regression: the guarded tree is untouched -------------------
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--"] + list(GUARDED),
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        dirty = [line for line in result.stdout.splitlines() if line.strip()]
        report.check(
            "19 MARK LIII is untouched (git status on the guarded paths)",
            result.returncode == 0 and not dirty,
            " | ".join(dirty[:5]),
        )
    except Exception as exc:
        report.fail("19 MARK LIII is untouched", f"git unavailable: {exc}")

    # -- 20-22. the face JARVIS chose, arriving over the wire ----------------
    #
    # `presence/` resolves the reflex locally — listening, thinking, speaking —
    # and needs nothing from the server to do it. What the server sends is the
    # *intent*, and these three checks cover the whole of its arrival: it is
    # read, it is dropped when late, and it only ever reaches a body.

    try:
        import json as _json
        import time as _time

        from client_desktop.net import JarvisClient
        from client_desktop.state import JarvisStore

        store = JarvisStore()
        seen: list[object] = []
        store.subscribe(
            lambda kind, payload: seen.append(payload) if kind == "avatar" else None
        )
        client = JarvisClient(
            config_mod.Settings(host="h.ts.net", device_token="tok"),
            store,
            speaker=None,
            on_connected=lambda: None,
            on_disconnected=lambda _r, _f: None,
        )
        directive = {"valence": 0.45, "attention": 0.9, "gesture": "nod"}

        client._handle_event(
            _json.dumps({"type": P.EV_AVATAR, "ts": _time.time(),
                         "directive": directive})
        )
        fresh_arrived = (len(seen) == 1 and isinstance(seen[0], dict)
                         and seen[0].get("directive") == directive)

        # Late, and therefore not a face any more. `/ws` replays its last 50
        # events to every client that connects, so this is not a hypothetical:
        # it is what a laptop that reconnects at noon is handed.
        client._handle_event(
            _json.dumps({"type": P.EV_AVATAR,
                         "ts": _time.time() - P.AVATAR_FRESH_SECONDS - 5,
                         "directive": directive})
        )
        stale_dropped = len(seen) == 1

        # Malformed, and from a server that may be newer than this client.
        # Ignored in silence, never raised: the socket carrying this event is
        # also carrying JARVIS's voice.
        for bad in ('{"type":"avatar"}',
                    '{"type":"avatar","directive":"amused"}',
                    '{"type":"avatar","ts":"soon","directive":{"gesture":"nod"}}'):
            client._handle_event(bad)
        malformed_survived = len(seen) == 2   # le troisieme a un ts illisible : livre

        report.check(
            "20 an avatar directive arrives, and a stale one does not",
            fresh_arrived and stale_dropped and malformed_survived,
            f"fresh={fresh_arrived} stale_dropped={stale_dropped} "
            f"malformed={malformed_survived}",
        )
    except Exception as exc:
        report.fail("20 an avatar directive arrives", repr(exc))

    # -- 21. the client's copy of the window matches the server's ------------
    try:
        server_window = None
        source = (_REPO_ROOT / "plugins" / "presence.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith("FRESH_S"):
                server_window = float(line.split("=", 1)[1].strip())
                break
        report.check(
            "21 the freshness window is the server's number, not a guess",
            server_window == P.AVATAR_FRESH_SECONDS,
            f"client {P.AVATAR_FRESH_SECONDS} vs tool {server_window}",
        )
    except Exception as exc:
        report.fail("21 the freshness window matches the server", repr(exc))

    # -- 22. the intent reaches the HUD, and only a face takes it -----------
    #
    # Read from the source rather than built, because building it needs Qt, a
    # display and a WebEngine process — none of which this file is allowed to
    # require. Structural on purpose: the HUD hands the intent to its face
    # driver only when there is one, and the controller forwards it above the
    # notifications setting — an expression makes no sound and raises no
    # toast, so switching notifications off must not take JARVIS's face away.
    try:
        window_source = (
            _REPO_ROOT / "client_desktop" / "hud" / "window.py"
        ).read_text(encoding="utf-8")
        app_source = (
            _REPO_ROOT / "client_desktop" / "app.py"
        ).read_text(encoding="utf-8")
        asks_first = (
            "def set_avatar_intent" in window_source
            and "if self._face is not None:" in window_source
        )
        above_notifications = app_source.index('kind == "avatar"') < app_source.index(
            "if not self.settings.notifications:"
        )
        report.check(
            "22 an intent reaches the HUD's face, whatever the notifications",
            asks_first and above_notifications,
            f"asks_first={asks_first} above_notifications={above_notifications}",
        )
    except Exception as exc:
        report.fail("22 an intent reaches the HUD's face", repr(exc))

    # -- 23. the intent reaches the Director, through the real hand-off ------
    #
    # Checks 20 and 22 each prove their half: the store emits, the HUD asks
    # its face. Nothing in them proves the two halves speak the same shape —
    # and once they did not: the store emitted the bare directive, the face
    # looked for a `directive` key inside it, and every intent JARVIS sent was
    # dropped right here. This runs the real FaceDriver on what the store
    # actually emits, and follows the word to the JSON posted to the page.
    try:
        import json as _json
        import time as _time

        from client_desktop.hud.face import FaceDriver
        from client_desktop.net import JarvisClient
        from client_desktop.state import JarvisStore

        class _Body:
            """What the HUD page would receive from the real FaceDriver."""

            def __init__(self) -> None:
                self.pushed: list[dict] = []
                self.driver = FaceDriver(self._collect)
                self.driver.enable(True)
                self.driver.frame_ready()
                self.driver._last_state = "SPEAKING"
                self.driver._last_speech = 0.3

            def _collect(self, message: dict) -> None:
                if message.get("type") == "performance":
                    self.pushed.append(message["payload"])

        body = _Body()
        store = JarvisStore()
        store.subscribe(
            lambda kind, payload: body.driver.set_intent(payload) if kind == "avatar" else None)
        client = JarvisClient(
            config_mod.Settings(host="h.ts.net", device_token="tok"),
            store, speaker=None,
            on_connected=lambda: None, on_disconnected=lambda _r, _f: None,
        )
        client._handle_event(_json.dumps({
            "type": P.EV_AVATAR, "ts": _time.time(),
            "directive": {"intent": "investigate", "reason": "le log"}}))

        pushed = body.pushed[-1] if body.pushed else {}
        reached = (
            pushed.get("intent") == "investigate"
            and pushed.get("gaze") == "screen"
            and pushed.get("expression") == "thinking"
            and str(pushed.get("gesture_id", "")).startswith("intent#")
            and pushed.get("speech_level") == 0.3
        )
        report.check(
            "23 an intent from the wire reaches the Director and the page",
            reached,
            f"pushed={len(body.pushed)} intent={pushed.get('intent')} "
            f"gaze={pushed.get('gaze')} gesture_id={pushed.get('gesture_id')}",
        )
    except Exception as exc:
        report.fail("23 an intent reaches the Director", repr(exc))

    # -- 24. a reconnection does not replay a face, and age is honoured -------
    #
    # `/ws` replays its last 50 events to whoever connects. The 25 s window
    # stops yesterday's faces, not the one from ten seconds ago: a quick
    # reconnect used to hand the last directive back, and JARVIS nodded again
    # for a sentence that had finished. Order is decided on the SERVER's `ts`,
    # compared only with other stamps from the same server.
    try:
        import json as _json
        import time as _time

        from client_desktop.net import JarvisClient
        from client_desktop.state import JarvisStore
        from presence import Director, parse

        store = JarvisStore()
        seen: list[dict] = []
        store.subscribe(
            lambda kind, payload: seen.append(payload) if kind == "avatar" else None)
        client = JarvisClient(
            config_mod.Settings(host="h.ts.net", device_token="tok"),
            store, speaker=None,
            on_connected=lambda: None, on_disconnected=lambda _r, _f: None,
        )
        now = _time.time()
        first = _json.dumps({"type": P.EV_AVATAR, "ts": now - 10,
                             "directive": {"intent": "agree"}})
        second = _json.dumps({"type": P.EV_AVATAR, "ts": now - 2,
                              "directive": {"intent": "warn"}})
        client._handle_event(first)
        client._handle_event(second)
        # The reconnection: the server replays both, in its own order.
        client._handle_event(first)
        client._handle_event(second)
        # And an older one arriving late, out of order.
        client._handle_event(_json.dumps({"type": P.EV_AVATAR, "ts": now - 5,
                                          "directive": {"intent": "amuse"}}))
        words = [p["directive"].get("intent") for p in seen]
        no_replay = words == ["agree", "warn"]

        # A decision already 20 s old on arrival has 5 s left, not 25.
        director = Director()
        director.set_intent(parse('{"intent": "warn"}'), now=100.0 - 20.0)
        backdated = (director.resolve("SPEAKING", now=100.0 + 4.0).intent is not None
                     and director.resolve("SPEAKING", now=100.0 + 6.0).intent is None)
        ages_carried = all(0.0 <= p.get("age", -1) < 11.0 for p in seen)

        report.check(
            "24 a reconnection never replays a face, and a late one dies on time",
            no_replay and backdated and ages_carried,
            f"played={words} backdated={backdated} ages={ages_carried}",
        )
    except Exception as exc:
        report.fail("24 a reconnection never replays a face", repr(exc))

    _hud_checks(report)

    total = report.passed + report.failed
    print(f"\n{report.passed}/{total}")
    if report.failed:
        print("\nFailures above. Nothing downstream is worth trying.")
    return report.failed == 0



def _hud_checks(report: "_Report") -> None:  # noqa: C901
    """25-33: the HUD, its page, its settings page, and main.py's launcher."""
    import json as _json
    import posixpath
    import re

    from client_desktop import config as config_mod
    from client_desktop.hud import face, metrics, scheme
    from client_desktop.hud import settings as hs
    from client_desktop.hud.window import HudWindow, PREF_FIELDS

    # -- 25. the controller's contract with the window ----------------------
    app_src = (_REPO_ROOT / "client_desktop" / "app.py").read_text(encoding="utf-8")
    local_src = (_REPO_ROOT / "local_hud.py").read_text(encoding="utf-8")
    used = set(re.findall(r"self\.hud\.([A-Za-z_]\w*)", app_src))
    used |= set(re.findall(r"\bhud\.([A-Za-z_]\w*)", local_src))
    instance_attrs = {"_mini", "local_mode", "backend", "settings_overrides"}
    absent = {n for n in used if not hasattr(HudWindow, n) and n not in instance_attrs}
    report.check(f"25 every one of the {len(used)} HUD members the two hosts use exists",
                 not absent, f"missing {sorted(absent)}")

    # -- 26. the scheme ------------------------------------------------------
    ok_index = scheme.resolve("/index.html") is not None
    ok_three = scheme.resolve("/vendor/three.module.min.js") is not None
    bad = ["/../ui/debug.py", "/vendor/../../client_desktop/config.py",
           "/..%2f..%2fmain.py", "/css/../../window.py"]
    refused = all(scheme.resolve(x) is None for x in bad)
    report.check("26 the HUD scheme serves the page and three.js, refuses traversal",
                 ok_index and ok_three and refused,
                 f"index={ok_index} three={ok_three} refused={refused}")

    # -- 27. every file the page references exists ---------------------------
    web = _REPO_ROOT / "client_desktop" / "hud" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="((?:css|js)/[^"]+)"', html)
    js_refs = []
    for js in (web / "js").glob("*.js"):
        for m in re.findall(r"""(?:from|import\()\s*['"](\.{1,2}/[^'"]+)['"]""",
                            js.read_text(encoding="utf-8")):
            js_refs.append(posixpath.normpath("/js/" + m))
    missing = [x for x in refs if scheme.resolve("/" + x) is None]
    missing += [x for x in js_refs if scheme.resolve(x) is None]
    report.check(f"27 all {len(refs) + len(js_refs)} page references resolve",
                 not missing, f"missing {missing}")

    # -- 28. one settings file: the HUD's look lives in Settings ------------
    with tempfile.TemporaryDirectory() as temp:
        saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = temp
        try:
            folder = config_mod.settings_path().parent
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "hud.json").write_text(_json.dumps(
                {"accent": "#F43F5E", "face": True, "quality": 3}), encoding="utf-8")
            migrated = config_mod.load()
            fields_exist = all(hasattr(migrated, attr) for attr in PREF_FIELDS.values())
            ok = (fields_exist and migrated.accent == "#F43F5E"
                  and migrated.avatar_enabled is True and migrated.quality == 3)
        finally:
            if saved is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = saved
    report.check("28 the HUD's look is in settings.json, and hud.json migrates", ok)

    # -- 29. the face driver -------------------------------------------------
    try:
        from client_desktop.state import AssistantState, LinkState, Snapshot
        posted: list[dict] = []
        d = face.FaceDriver(posted.append)
        snap = Snapshot(link=LinkState.CONNECTED, assistant=AssistantState.SPEAKING,
                        speaker_level=.6)
        d.set_snapshot(snap)
        silent = not posted
        d.enable(True)
        d.frame_ready()
        d.set_snapshot(snap)
        kinds = {m["type"] for m in posted}
        report.check("29 the face is silent until enabled, then performs and speaks",
                     silent and {"performance", "speak"} <= kinds,
                     f"silent={silent} kinds={kinds}")
    except Exception as exc:
        report.fail("29 the face driver", repr(exc))

    # -- 30. machine metrics -------------------------------------------------
    sample = metrics.Metrics().sample()
    report.check("30 machine metrics produce numbers",
                 not sample.get("available") or isinstance(sample.get("cpu"), (int, float)),
                 str(sample)[:80])

    # -- 31. the settings page cleans what is pasted -------------------------
    st = config_mod.Settings()
    changed = hs.apply(st, {"host": " https://jarvis-vps.tailnet.ts.net:8000/ ", "port": 0,
                            "device_token": "  abc  ", "wake_word_threshold": 7,
                            "gate_seconds": 1, "device_name": "", "evil": "x"})
    ok = (st.host == "jarvis-vps.tailnet.ts.net" and st.port == 8000
          and st.device_token == "abc" and st.wake_word_threshold == 0.95
          and st.gate_seconds == 5.0 and "device_name" not in changed
          and not hasattr(st, "evil"))
    report.check("31 the settings page cleans what is pasted and clamps the rest", ok,
                 f"host={st.host!r} port={st.port} th={st.wake_word_threshold}")

    # -- 32. one start-up entry, the HUD's ------------------------------------
    launcher = _REPO_ROOT / "client_desktop" / "launch.pyw"
    launcher_src = launcher.read_text(encoding="utf-8")
    report.check("32 Windows start-up opens the HUD, through the one launcher",
                 "client_desktop.__main__" in launcher_src
                 and "HudWindow" in app_src and "JarvisPanel" not in app_src)

    # -- 33. main.py's launcher block ----------------------------------------
    head = (_REPO_ROOT / "main.py").read_text(encoding="utf-8").split("import platform", 1)[0]
    report.check("33 python main.py opens the HUD, --local the HUD on the local brain",
                 'if __name__ == "__main__":' in head
                 and "client_desktop.__main__" in head and "local_hud" in head)


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
