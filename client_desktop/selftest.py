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

* **45** fails if `git status` reports a modification to `main.py`,
  `dashboard/`, `core/`, `memory/`, `actions/`, `plugins/` or
  `requirements.txt`. This client is additive or it is broken.

Checks 16-41 are the placement arithmetic. They run on synthetic rectangles
because placement defects live on the third monitor at 150 % scaling behind a
maximised editor, which no real desktop can reproduce on demand.

Checks 42-43 are the device capabilities, and they are the same kind of check
one level up: a capability is a promise the router acts on, so every declared
name must be a real action that actually imported here.

Checks 46-48 are the arrival of an `avatar` directive - the face JARVIS chose
for what he is saying. They cover the one thing that is genuinely easy to get
wrong: `/ws` replays its last 50 events to a client that connects, and a face
replayed at noon is a reaction to nothing.

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
            app, audio, autostart, config, device, net, placement, protocol,
            reconnect, single_instance, state, wake, win_windows,
        )
        from client_desktop.ui import (  # noqa: F401
            core_widget, debug, panel, screens, theme, tray,
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
    from client_desktop.placement import (
        Anchor, PlacementConfig, PlacementMode, Rect,
        apply_snap, compute, compute_screen_anchored, compute_window_aligned,
        ensure_visible, panel_size, screen_for,
    )
    from client_desktop.reconnect import BACKOFF_STEPS, backoff_seconds
    from client_desktop.state import (
        AssistantState, JarvisStore, LinkState, Message, Snapshot,
    )
    from client_desktop.ui.core_widget import mood_of
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
        ).display_state == "RECONNECTING"
        and mood_of(LinkState.RECONNECTING, AssistantState.SPEAKING)
        is not mood_of(LinkState.CONNECTED, AssistantState.SPEAKING),
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
    # The list stays closed on purpose. `core.llm_client` or `core.tts` would be
    # a second brain arriving by the back door, and this is what refuses it.
    ALLOWED_CORE = {"core.wake_word", "core.action_loader"}
    report.check(
        "15 only wake_word and action_loader are reached into core/",
        core_imports <= ALLOWED_CORE,
        f"also imports {sorted(core_imports - ALLOWED_CORE)}",
    )

    # -- 16-41. placement ----------------------------------------------------
    WORK = Rect(0, 0, 1536, 864)          # 1920x1080 at 125 %, this machine
    base = PlacementConfig(margin=8, width_fraction=0.20, height_fraction=0.28)

    def cfg(**kw):
        return dataclass_replace(base, **kw)

    right = compute_screen_anchored(WORK, cfg(anchor=Anchor.RIGHT))
    left = compute_screen_anchored(WORK, cfg(anchor=Anchor.LEFT))
    top = compute_screen_anchored(WORK, cfg(anchor=Anchor.TOP))
    bottom = compute_screen_anchored(WORK, cfg(anchor=Anchor.BOTTOM))

    report.check(
        "16 screen anchor RIGHT sits against the right edge, margin respected",
        right.right == WORK.right - 8 and right.y == 8 and right.w == 307,
        str(right),
    )
    report.check(
        "17 screen anchor LEFT sits against the left edge",
        left.x == 8 and left.y == 8 and left.w == right.w,
        str(left),
    )
    report.check(
        "18 screen anchor TOP is a wide strip at the top",
        top.y == 8 and top.w > top.h and top.h == int(864 * 0.28),
        str(top),
    )
    report.check(
        "19 screen anchor BOTTOM sits against the bottom edge",
        bottom.bottom == WORK.bottom - 8 and bottom.w == top.w and bottom.h == top.h,
        str(bottom),
    )
    report.check(
        "20 vertical anchors make a column, horizontal ones a strip",
        right.h > right.w and left.h > left.w and top.w > top.h and bottom.w > bottom.h,
    )
    report.check(
        "21 every anchored rectangle stays inside the work area",
        all(
            r.x >= WORK.x and r.y >= WORK.y
            and r.right <= WORK.right and r.bottom <= WORK.bottom
            for r in (right, left, top, bottom)
        ),
    )

    # -- window alignment ----------------------------------------------------
    small = Rect(300, 200, 600, 400)
    beside = compute_window_aligned(small, WORK, cfg(anchor=Anchor.RIGHT))
    report.check(
        "22 a small window gets the panel beside it, not overlapping",
        beside.x >= small.right and not beside.overlaps(small),
        str(beside),
    )

    # A window hard against the right edge: the requested side does not fit, so
    # the opposite one must be used rather than half the panel leaving the
    # screen.
    hugging = Rect(1000, 100, 536, 600)
    flipped = compute_window_aligned(hugging, WORK, cfg(anchor=Anchor.RIGHT))
    report.check(
        "23 no room on the requested side -> the opposite side is used",
        flipped.right <= hugging.x and not flipped.overlaps(hugging),
        str(flipped),
    )

    maximised = Rect(0, 0, 1536, 864)
    over = compute_window_aligned(maximised, WORK, cfg(anchor=Anchor.RIGHT))
    report.check(
        "24 a maximised window still gets a panel, on screen, on the right",
        over.right <= WORK.right and over.x >= WORK.x and over.right > WORK.right - 400,
        str(over),
    )

    tiny = Rect(700, 400, 220, 180)
    tiny_panel = compute_window_aligned(tiny, WORK, cfg(anchor=Anchor.RIGHT))
    report.check(
        "25 a tiny window does not produce an unusably tiny panel",
        tiny_panel.w >= 260 and tiny_panel.h >= 170,
        str(tiny_panel),
    )

    moved = compute_window_aligned(
        Rect(small.x + 200, small.y + 100, small.w, small.h), WORK,
        cfg(anchor=Anchor.RIGHT),
    )
    report.check(
        "26 moving the target window moves the panel with it",
        moved.x == beside.x + 200 and moved.y == beside.y + 100,
        f"{beside} -> {moved}",
    )

    switched = compute_window_aligned(
        Rect(50, 50, 400, 300), WORK, cfg(anchor=Anchor.RIGHT)
    )
    report.check(
        "27 switching target window recomputes against the new one",
        switched != beside and switched.x >= 450,
        str(switched),
    )

    # -- snapping ------------------------------------------------------------
    loose = Rect(10, 10, 307, 848)
    snapped = apply_snap(loose, WORK, None, cfg(snap_distance=16))
    far = Rect(300, 300, 307, 400)
    unsnapped = apply_snap(far, WORK, None, cfg(snap_distance=16))
    report.check(
        "28 snap latches inside the threshold and leaves size alone",
        (snapped.x, snapped.y) == (8, 8)
        and (snapped.w, snapped.h) == (loose.w, loose.h),
        str(snapped),
    )
    report.check(
        "29 snap ignores anything beyond the threshold",
        unsnapped == far,
        str(unsnapped),
    )
    report.check(
        "30 snap disabled does nothing at all",
        apply_snap(loose, WORK, None, cfg(snap_enabled=False)) == loose,
    )

    # -- multi-monitor -------------------------------------------------------
    two = [Rect(0, 0, 1536, 864), Rect(1536, 0, 1920, 1080)]
    on_second = Rect(1600, 100, 800, 600)
    report.check(
        "31 screen_for picks by overlapping area, not by corner",
        screen_for(on_second, two) == 1
        and screen_for(Rect(-40, 100, 600, 400), two) == 0,
    )
    placed = compute(
        cfg(mode=PlacementMode.WINDOW, anchor=Anchor.RIGHT), two, target=on_second
    )
    report.check(
        "32 a target on the second screen keeps the panel on that screen",
        screen_for(placed, two) == 1 and placed.x >= 1536,
        str(placed),
    )

    offscreen = Rect(4000, 2000, 307, 848)
    rescued = ensure_visible(offscreen, two)
    report.check(
        "33 a saved position on a monitor that is gone is corrected",
        rescued.intersection(two[0]).area + rescued.intersection(two[1]).area > 0,
        str(rescued),
    )
    kept = Rect(1700, 200, 307, 600)
    report.check(
        "34 a saved position that is still valid is left exactly alone",
        ensure_visible(kept, two) == kept,
    )

    # -- DPI -----------------------------------------------------------------
    # The same physical 1920x1080 panel at two scale factors. Placement works in
    # logical pixels, so the *fraction* must hold while the pixel count does
    # not - that is what makes the panel look the same size on both.
    w125, _ = panel_size(Rect(0, 0, 1536, 864), cfg(anchor=Anchor.RIGHT))
    w100, _ = panel_size(Rect(0, 0, 1920, 1080), cfg(anchor=Anchor.RIGHT))
    report.check(
        "35 a DPI change rescales the panel with the logical work area",
        w125 == 307 and w100 == 384 and w100 > w125,
        f"125%={w125} 100%={w100}",
    )

    # -- FREE, and degrading gracefully --------------------------------------
    free_rect = Rect(400, 300, 320, 500)
    report.check(
        "36 FREE mode returns the remembered rectangle untouched",
        compute(cfg(mode=PlacementMode.FREE), [WORK], free=free_rect) == free_rect,
    )
    report.check(
        "37 FREE with nothing remembered falls back to an anchored position",
        compute(cfg(mode=PlacementMode.FREE), [WORK], free=None) == right,
    )
    report.check(
        "38 WINDOW/FOLLOW with no target degrades to the screen anchor",
        compute(
            cfg(mode=PlacementMode.FOLLOW, anchor=Anchor.RIGHT), [WORK], target=None
        ) == right,
    )

    # -- settings: persistence and migration ---------------------------------
    with tempfile.TemporaryDirectory() as temp:
        saved_env = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = temp
        try:
            st = config_mod.Settings(
                host="h.ts.net", device_token="t",
                placement_mode="follow", anchor="bottom", margin=24,
                snap_enabled=False, snap_distance=32, always_on_top=False,
            )
            st.remember_free(Rect(11, 22, 333, 444), r"\\.\DISPLAY2")
            config_mod.save(st)
            back = config_mod.load()
            restored = (
                back.placement.mode is PlacementMode.FOLLOW
                and back.placement.anchor is Anchor.BOTTOM
                and back.margin == 24
                and back.snap_enabled is False
                and back.snap_distance == 32
                and back.always_on_top is False
                and back.free_rect == Rect(11, 22, 333, 444)
                and back.free_screen == r"\\.\DISPLAY2"
            )

            # An older settings file, from before there were four modes.
            config_mod.settings_path().write_text(
                '{"host":"h","device_token":"t","dock":"left"}', encoding="utf-8"
            )
            migrated = config_mod.load().placement.anchor is Anchor.LEFT

            # And a file written by a future version, or by hand.
            config_mod.settings_path().write_text(
                '{"placement_mode":"diagonal","anchor":"sideways"}', encoding="utf-8"
            )
            nonsense = config_mod.load().placement
            tolerant = (
                nonsense.mode is PlacementMode.SCREEN
                and nonsense.anchor is Anchor.RIGHT
            )
        finally:
            if saved_env is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = saved_env

    report.check("39 every placement setting survives a restart", restored)
    report.check("40 an old `dock` setting migrates to `anchor`", migrated)
    report.check("41 an unknown mode or anchor falls back instead of raising",
                 tolerant)

    # -- 42a. capabilities are proven, never asserted ------------------------
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
        "42 toute capacite declaree est une action reelle et chargee",
        declared <= real_actions and declared <= set(DEVICE_BOUND_ACTIONS),
        f"declarees={sorted(declared)} inconnues={sorted(declared - real_actions)}",
    )

    # Network-only actions must never be device capabilities: declaring
    # `web_search` on two devices invents an ambiguity where none exists.
    report.check(
        "43 les actions non liees a l'appareil ne sont pas declarees",
        not ({"web_search", "weather_report", "flight_finder", "reminder"} & declared),
        f"declarees={sorted(declared)}",
    )

    # -- 44. shutdown must not write the settings file -----------------------
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
        report.fail("44 shutdown does not write settings", repr(exc))
    else:
        report.check(
            "44 shutdown does not overwrite the settings file",
            not shutdown_saves,
            "shutdown() calls save() - an unpaired process would wipe the pairing",
        )

    # -- 45. non-regression: the guarded tree is untouched -------------------
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
            "45 MARK LIII is untouched (git status on the guarded paths)",
            result.returncode == 0 and not dirty,
            " | ".join(dirty[:5]),
        )
    except Exception as exc:
        report.fail("45 MARK LIII is untouched", f"git unavailable: {exc}")

    # -- 46-48. the face JARVIS chose, arriving over the wire ----------------
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
        fresh_arrived = seen == [directive]

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
            "46 an avatar directive arrives, and a stale one does not",
            fresh_arrived and stale_dropped and malformed_survived,
            f"fresh={fresh_arrived} stale_dropped={stale_dropped} "
            f"malformed={malformed_survived}",
        )
    except Exception as exc:
        report.fail("46 an avatar directive arrives", repr(exc))

    # -- 47. the client's copy of the window matches the server's ------------
    try:
        server_window = None
        source = (_REPO_ROOT / "plugins" / "presence.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith("FRESH_S"):
                server_window = float(line.split("=", 1)[1].strip())
                break
        report.check(
            "47 the freshness window is the server's number, not a guess",
            server_window == P.AVATAR_FRESH_SECONDS,
            f"client {P.AVATAR_FRESH_SECONDS} vs tool {server_window}",
        )
    except Exception as exc:
        report.fail("47 the freshness window matches the server", repr(exc))

    # -- 48. only a body is offered an intent --------------------------------
    #
    # Read from the source rather than built, because building it needs Qt, a
    # display and a WebEngine process — none of which this file is allowed to
    # require. What matters is structural anyway: the panel must ask whether the
    # core can take an intent instead of assuming it can, or a 2D install dies
    # on the first directive.
    try:
        panel_source = (
            _REPO_ROOT / "client_desktop" / "ui" / "panel.py"
        ).read_text(encoding="utf-8")
        app_source = (
            _REPO_ROOT / "client_desktop" / "app.py"
        ).read_text(encoding="utf-8")
        asks_first = (
            'getattr(self._core, "set_intent_json"' in panel_source
            and "def set_avatar_intent" in panel_source
        )
        # And it must be handled above the notifications setting: an expression
        # makes no sound and raises no toast, so switching notifications off
        # must not also take JARVIS's face away.
        above_notifications = app_source.index('kind == "avatar"') < app_source.index(
            "if not self.settings.notifications:"
        )
        report.check(
            "48 an intent is offered to a body, and never to the 2D core",
            asks_first and above_notifications,
            f"asks_first={asks_first} above_notifications={above_notifications}",
        )
    except Exception as exc:
        report.fail("48 an intent is offered to a body only", repr(exc))

    total = report.passed + report.failed
    print(f"\n{report.passed}/{total}")
    if report.failed:
        print("\nFailures above. Nothing downstream is worth trying.")
    return report.failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
