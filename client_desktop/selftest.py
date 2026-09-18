"""
Offline checks. No Gemini, no microphone, no display, no network, no server.

Modelled on `server/selftest.py`, including the part that matters most: it fails
if this package has drifted into touching something it must not. Two of the
checks here are not tests of behaviour at all but of *architecture*, and they
are the reason this file exists —

* **check 11** fails if `git status` reports a modification to `main.py`,
  `dashboard/`, `core/`, `memory/`, `actions/`, `plugins/` or `requirements.txt`.
  This client is additive or it is broken.

* **check 12** fails if anything under `client_desktop/` imports `main`,
  `ui`, or a Gemini SDK. That is the "no second session" promise made
  mechanical: it cannot be quietly undone by someone reaching for
  `JarvisLive` to save an afternoon.

Run it with `python -m client_desktop --selftest`.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PASS = "  ok  "
_FAIL = " FAIL "

#: The directories `server/selftest.py` guards. Kept in sync by hand, which is
#: fine: the list has not changed since the headless layer was written, and a
#: copy that is wrong fails loudly rather than silently passing.
GUARDED = (
    "main.py", "dashboard", "core", "memory", "actions", "plugins",
    "requirements.txt",
)

#: Importing any of these would mean this client had grown a brain.
FORBIDDEN_IMPORTS = ("main", "google.genai", "google_genai")


def _is_core_module(name: str) -> bool:
    """True only for the repository's `core` package — never for a module that
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
            print(f"[{_FAIL}] {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.check(name, False, detail)


def run() -> bool:  # noqa: C901
    report = _Report()
    print("JARVIS Desktop — selftest\n")

    # ── 1. every module imports ──────────────────────────────────────────────
    try:
        from client_desktop import (  # noqa: F401
            app, audio, autostart, config, net, protocol, reconnect,
            single_instance, state, wake,
        )
        from client_desktop.ui import core_widget, debug, panel, theme, tray  # noqa: F401

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
    from client_desktop.ui.core_widget import mood_of
    from client_desktop.wake import Gate

    # ── 2. the endpoints are the documented ones ─────────────────────────────
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

    # ── 4. the audio format is the server's, not ours ────────────────────────
    report.check(
        "4  uplink is 16 kHz / 2048-byte frames (main.py CHUNK_SIZE)",
        P.UPLINK_SAMPLE_RATE == 16_000 and P.UPLINK_FRAME_BYTES == 2048,
    )

    # ── 5. backoff: shape and bounds ─────────────────────────────────────────
    import random

    rng = random.Random(0)
    bounds_ok = True
    for attempt in range(1, 12):
        base = BACKOFF_STEPS[min(attempt - 1, len(BACKOFF_STEPS) - 1)]
        for _ in range(50):
            value = backoff_seconds(attempt, rng)
            if not (1 <= value <= base * 1.2 + 1):
                bounds_ok = False
    report.check("5  backoff stays within its step +/-20 %", bounds_ok)
    report.check(
        "6  backoff caps the delay, never the attempts",
        backoff_seconds(500, rng) <= 37,
    )

    # ── 7. the eight states the UI names ─────────────────────────────────────
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

    # ── 9. the store ─────────────────────────────────────────────────────────
    store = JarvisStore()
    store.add_message(Message(from_jarvis=False, text="bonjour"))
    store.add_message(Message(from_jarvis=True, text="Je vous écoute."))
    store.count_sent(2048)
    store.count_received(9600)
    store.set_confirmation("abc", "Supprimer", "3 fichiers", 90)
    snapshot = store.snapshot()
    store_ok = (
        snapshot.last_spoken_line == "Je vous écoute."
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

    # ── 10. levels, the gate, and settings ───────────────────────────────────
    import numpy as np

    silence = np.zeros(1024, dtype=np.int16)
    loud = (np.ones(1024, dtype=np.int16) * 6000)
    report.check(
        "10 pcm_level floors silence and saturates on a shout",
        audio_mod.pcm_level(silence) == 0.0 and audio_mod.pcm_level(loud) == 1.0,
    )

    gate = Gate(seconds=0.01)
    gate.open()
    was_open = gate.is_open
    import time as _time

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

    with tempfile.TemporaryDirectory() as temp:
        saved = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = temp
        try:
            settings = config_mod.Settings(host="h.ts.net", device_token="tok", port=8123)
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

    # ── 13. autostart, without writing anything ──────────────────────────────
    command = autostart_mod.command()
    report.check(
        "13 the startup command points at launch.pyw with pythonw",
        command.count('"') == 4
        and "launch.pyw" in command
        and autostart_mod.launcher_path().is_file(),
        command,
    )

    # ── 14. the architecture checks ──────────────────────────────────────────
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / "client_desktop").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            offenders.append(f"{path.name}: {exc}")
            continue
        if path.name == "selftest.py":
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if name in FORBIDDEN_IMPORTS or root in ("main",):
                    offenders.append(f"{path.name} imports {name}")
    report.check(
        "14 nothing here imports main.py or a Gemini SDK",
        not offenders,
        "; ".join(offenders),
    )

    # `core.wake_word` is the one reach into the existing tree, and it must stay
    # the only one: importing `core.llm_client` or `core.tts` would be a second
    # brain arriving by the back door.
    core_imports: set[str] = set()
    for path in sorted((_REPO_ROOT / "client_desktop").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # `node.level == 0` matters: `from .core_widget import ...` is this
            # package's own UI, not the repository's `core/`. Without the test
            # every relative import whose name merely starts with "core" reads
            # as a reach into MARK LIII.
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                if _is_core_module(node.module or ""):
                    core_imports.add(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_core_module(alias.name):
                        core_imports.add(alias.name)
    report.check(
        "15 core.wake_word is the only thing reached into core/",
        core_imports <= {"core.wake_word"},
        f"also imports {sorted(core_imports - {'core.wake_word'})}",
    )

    # ── 16. shutdown must not write the settings file ────────────────────────
    #
    # A regression guard for a bug that cost a working pairing: `shutdown()`
    # used to call `save(self.settings)`, so any process holding an in-memory
    # Settings — a UI test, a future headless mode — overwrote the user's
    # configured file on the way out, and the next launch came up unpaired
    # with nothing on screen to explain it. Settings are written when changed,
    # never on exit.
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
        report.fail("16 shutdown does not write settings", repr(exc))
    else:
        report.check(
            "16 shutdown does not overwrite the settings file",
            not shutdown_saves,
            "shutdown() calls save() — an unpaired process would wipe the pairing",
        )

    # ── 17. non-regression: the guarded tree is untouched ────────────────────
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
            "17 MARK LIII is untouched (git status on the guarded paths)",
            result.returncode == 0 and not dirty,
            " | ".join(dirty[:5]),
        )
    except Exception as exc:
        report.fail("17 MARK LIII is untouched", f"git unavailable: {exc}")

    total = report.passed + report.failed
    print(f"\n{report.passed}/{total}")
    if report.failed:
        print("\nFailures above. Nothing downstream is worth trying.")
    return report.failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
