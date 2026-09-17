"""
server/selftest.py — proves the headless layer before Gemini is involved.

    python -m server.selftest

Everything here runs without a Gemini key, without a microphone, without a
display, and without a network. What it checks:

  1. main.py is untouched, and so is everything else this layer builds around.
  2. HeadlessUI covers every `self.ui.<attr>` main.py actually uses — read out
     of main.py's own syntax tree, so the test tracks main.py instead of a list
     somebody has to remember to update.
  3. The sounddevice stand-in honours the exact calls main.py makes on it.
  4. RuntimeState reports the link honestly, including when the UI stream is
     stale.
  5. /health, /status and /ws/phone-out behave — including rejecting an
     unauthenticated caller.
  6. The persistent device credential pairs through the dashboard's own
     /api/device-login, unmodified.

Check 5 and 6 need fastapi + httpx. If they are missing the test says so and
skips those two rather than failing: they are the deployment's dependencies,
not this layer's.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, str(detail)))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return wrap


# ── 1. nothing existing was modified ─────────────────────────────────────────

@check("MARK LIII core is unmodified")
def _untouched():
    guarded = ["main.py", "ui.py", "dashboard", "core", "memory",
               "actions", "plugins", "requirements.txt"]
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--"] + guarded,
            cwd=BASE_DIR, capture_output=True, text=True, timeout=20,
        )
    except Exception as e:
        return f"skipped (git unavailable: {e})"
    dirty = [l for l in out.stdout.splitlines() if l.strip()]
    # ui.py was already modified in the working tree before this layer existed;
    # what must hold is that nothing here added to that list.
    unexpected = [l for l in dirty if not l.endswith("ui.py")]
    assert not unexpected, "modified by this layer: " + "; ".join(unexpected)
    return f"{len(guarded)} paths clean" + (" (ui.py pre-existing edit ignored)"
                                            if dirty else "")


# ── 2. the UI façade is complete ─────────────────────────────────────────────

@check("HeadlessUI covers every self.ui.* in main.py")
def _facade():
    from server.headless_ui import HeadlessUI

    tree = ast.parse((BASE_DIR / "main.py").read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "ui"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"):
            used.add(node.attr)
    assert used, "parsed no self.ui.* at all — the AST walk is wrong"

    ui = HeadlessUI()
    missing = sorted(a for a in used if not hasattr(ui, a))
    assert not missing, f"HeadlessUI is missing: {missing}"
    return f"{len(used)} attributes, all present"


@check("the 'ui' stand-in satisfies `from ui import JarvisUI`")
def _ui_module():
    import server.headless_ui as headless
    assert hasattr(headless, "JarvisUI"), "no JarvisUI in the stand-in module"
    inst = headless.JarvisUI("face.png")        # main.main()'s call shape
    assert inst.muted is False, "a headless UI must never start muted"
    assert inst._win._ready is True, "main.py waits on _win._ready"
    return "constructs with ui.py's signature"


# ── 3. the sound-card stand-in ───────────────────────────────────────────────

@check("sounddevice stand-in matches main.py's usage")
def _audio_stub():
    from server.audio_bridge import AudioHub, build_module

    hub = AudioHub()
    sd = build_module(hub, log=lambda *_: None)

    # _listen_audio: opens a callback stream and uses it as a context manager.
    seen = []
    mic = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                         blocksize=1024, device=None,
                         callback=lambda *a: seen.append(a))
    with mic:
        pass
    assert not seen, "the server microphone must never deliver audio"
    assert mic.closed, "__exit__ must close the stream"

    # _play_audio: start(), write() from a worker thread, stop(), close().
    spk = sd.RawOutputStream(samplerate=24000, channels=1, dtype="int16",
                             blocksize=1024, device=None)
    spk.start()
    spk.write(b"\x00\x01" * 8)
    spk.write(bytearray(b"\x02\x03" * 8))
    spk.write(memoryview(b"\x04\x05" * 8))
    spk.stop(); spk.close()
    assert hub.frames_published == 3, f"expected 3 frames, got {hub.frames_published}"

    # core/audio_devices enumerates through these two.
    assert sd.query_devices() == [] and sd.query_hostapis() == []
    return "InputStream, RawOutputStream, query_devices, query_hostapis"


@check("audio hub drops the OLDEST frame when a listener falls behind")
def _drop_policy():
    import asyncio

    from server.audio_bridge import AudioHub

    async def run():
        hub = AudioHub()
        sub = hub.subscribe()
        for i in range(sub.queue.maxsize + 5):
            hub.publish(bytes([i]))
        await asyncio.sleep(0)          # let call_soon_threadsafe callbacks run
        first = await sub.queue.get()
        assert sub.dropped == 5, f"dropped {sub.dropped}, expected 5"
        assert first[0] == 5, (
            f"kept the oldest frame ({first[0]}) — latency would grow without bound")
        return f"{sub.dropped} oldest frames dropped, newest kept"

    return asyncio.run(run())


# ── 4. runtime state ─────────────────────────────────────────────────────────

@check("RuntimeState corrects a stale UI state against the real session")
def _state():
    from server.runtime_state import RuntimeState, State

    live = {"up": False}
    st = RuntimeState()
    st.bind_session_probe(lambda: live["up"])

    assert st.snapshot()["state"] == State.DISCONNECTED.value

    live["up"] = True
    st.note_session_opened()
    st.note_ui_state("LISTENING")
    assert st.snapshot()["state"] == State.LISTENING.value

    # The session dies; main.py has not pushed a new UI state yet.
    live["up"] = False
    assert st.snapshot()["state"] == State.RECONNECTING.value, (
        "a dead session must not be reported as LISTENING")

    st.log("NET: Connection failed — retrying in 6s.")
    snap = st.snapshot()
    assert snap["last_error"] and "retrying" in snap["last_error"]
    assert snap["counts"]["errors"] == 1
    return "LISTENING → RECONNECTING on session loss, NET: captured"


# ── 5 & 6. the routes and the pairing, against the real DashboardServer ──────

def _fastapi_available() -> bool:
    try:
        import fastapi  # noqa: F401
        import httpx    # noqa: F401
        return True
    except Exception:
        return False


def _build_dashboard():
    """A real DashboardServer with our routes grafted on — no socket opened."""
    from dashboard.server import DashboardServer

    from server import api, auth
    from server.audio_bridge import AudioHub
    from server.runtime_state import RuntimeState

    dash = DashboardServer()
    token = auth.seed_dashboard(dash)

    # An earlier check may have run run_headless.build(), which replaces
    # dashboard.server.DashboardServer globally with a subclass that attaches
    # the routes in its constructor. When that has happened the app is already
    # wired, and attaching again would register shadowed routes — so reuse what
    # is there instead.
    if getattr(dash.app.state, "jarvis_attached", False):
        return dash, dash.app.state.jarvis_state, dash.app.state.jarvis_hub, token

    state = RuntimeState()
    hub = AudioHub()
    api.attach(dash, state=state, hub=hub, sample_rate=24000,
               log=lambda *_: None)
    return dash, state, hub, token


@check("/health answers unauthenticated, /status does not")
def _routes():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    dash, _state, _hub, _tok = _build_dashboard()
    with TestClient(dash.app) as client:
        r = client.get("/health")
        assert r.status_code == 200, f"/health → {r.status_code}"
        body = r.json()
        assert body["ok"] is True and "uptime_s" in body
        assert "log" not in body and "state" not in body, (
            "/health is unauthenticated and must not leak session state")

        assert client.get("/status").status_code == 401, (
            "/status answered without a token")

        bearer = next(iter(dash._tokens)) if dash._tokens else None
        if bearer is None:
            bearer = "t-" + "x" * 8
            dash._tokens.add(bearer)
        r = client.get("/status", headers={"Authorization": f"Bearer {bearer}"})
        assert r.status_code == 200, f"/status → {r.status_code}"
        snap = r.json()
        for key in ("state", "uptime_s", "session_live", "audio_out", "log"):
            assert key in snap, f"/status is missing {key!r}"
        assert snap["audio_out"]["sample_rate"] == 24000
    return "200 / 401 / 200"


@check("/ws/phone-out authenticates, announces the format, carries PCM")
def _phone_out():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    dash, state, hub, _tok = _build_dashboard()
    bearer = "t-" + "y" * 20
    dash._tokens.add(bearer)

    with TestClient(dash.app) as client:
        try:
            with client.websocket_connect("/ws/phone-out?token=wrong"):
                raise AssertionError("a bad token was accepted")
        except AssertionError:
            raise
        except Exception:
            pass    # closed with 4001, as it should be

        with client.websocket_connect(f"/ws/phone-out?token={bearer}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "audio_format", hello
            assert hello["sample_rate"] == 24000
            assert hello["encoding"] == "pcm_s16le"

            # This is exactly what _play_audio does, from its worker thread.
            payload = b"\x10\x20" * 64
            hub.publish(payload)
            assert ws.receive_bytes() == payload, "PCM did not arrive intact"

        assert state.snapshot()["phone"]["audio_streams"] == 0, (
            "listener count not released on disconnect")
    return "4001 on bad token, format hello, PCM round-trip"


@check("the persistent credential logs in through /api/device-login")
def _pairing():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    from server import auth

    creds_a = auth.load_or_create()
    creds_b = auth.load_or_create()
    assert creds_a["device_token"] == creds_b["device_token"], (
        "the device credential must be stable across calls")

    dash, _state, _hub, token = _build_dashboard()
    with TestClient(dash.app) as client:
        r = client.post("/api/device-login", json={"device_token": token})
        assert r.status_code == 200, f"device-login → {r.status_code}"
        body = r.json()
        assert body["ok"] is True and body["token"], body
        assert body["key"] == creds_a["session_key"]

        # And the bearer it just issued opens /status.
        r = client.get("/status",
                       headers={"Authorization": f"Bearer {body['token']}"})
        assert r.status_code == 200, "the issued bearer was rejected by /status"

        r = client.post("/api/device-login", json={"device_token": "nope"})
        assert r.status_code == 401, "an unknown device token was accepted"

    gi = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "*credentials*.json" in gi, (
        "the credential filename relies on this .gitignore pattern")
    return f"{auth.CRED_PATH.name} → bearer → /status, and it is git-ignored"


# ── 7. the real boot path, everything but Gemini ─────────────────────────────

def _can_boot() -> str:
    """main.py's own import chain. Missing pieces here are deployment gaps, not
    bugs in this layer, so the boot checks skip rather than fail."""
    for mod, why in (("google.genai", "google-genai"), ("numpy", "numpy"),
                     ("psutil", "psutil"), ("fastapi", "fastapi")):
        try:
            __import__(mod)
        except Exception:
            return why
    if not (BASE_DIR / "config" / "api_keys.json").exists():
        return "config/api_keys.json"
    return ""


@check("run_headless.build() constructs the real JarvisLive")
def _boot():
    missing = _can_boot()
    if missing:
        return f"skipped ({missing} not available)"

    from server import run_headless

    jarvis, ui, state, hub = run_headless.build(echo=False)

    import main
    assert isinstance(jarvis, main.JarvisLive)
    assert jarvis.ui is ui, "JarvisLive is not holding the headless UI"
    assert main.sd.__headless__ is True, (
        "main.py bound the real sounddevice — the stand-in went in too late")

    # The tool surface Gemini will be given, built by MARK LIII's own loaders.
    actions = len(jarvis._action_registry.names())
    plugins = len(jarvis._plugin_registry.get_tool_declarations())
    assert actions > 0, "no actions were discovered at all"

    # And the probe reports the truth before any session exists.
    assert jarvis.session is None
    assert state.snapshot()["session_live"] is False
    return (f"{actions} actions, {plugins} plugins, "
            f"{len(main.TOOL_DECLARATIONS)} inline tools")


@check("JARVIS's voice reaches a phone listener end to end")
def _end_to_end():
    missing = _can_boot()
    if missing:
        return f"skipped ({missing} not available)"
    from fastapi.testclient import TestClient

    import dashboard.server as dserver
    from server import run_headless

    jarvis, ui, state, hub = run_headless.build(echo=False)

    # Constructing the dashboard is what run() does; the patched subclass fires
    # the hook that seeds pairing, grafts the routes and binds the UI.
    dash = dserver.DashboardServer()
    assert ui._dashboard is dash, "the UI was not given its broadcast channel"

    bearer = "t-" + "z" * 20
    dash._tokens.add(bearer)

    import main
    with TestClient(dash.app) as client:
        assert client.get("/health").status_code == 200
        with client.websocket_connect(f"/ws/phone-out?token={bearer}") as ws:
            fmt = ws.receive_json()
            assert fmt["sample_rate"] == main.RECEIVE_SAMPLE_RATE, (
                "the announced rate is not the one main.py plays at")

            # Exactly what _play_audio does: open the output stream main.py
            # opens, and write a batch to it.
            spk = main.sd.RawOutputStream(
                samplerate=main.RECEIVE_SAMPLE_RATE, channels=1,
                dtype="int16", blocksize=main.CHUNK_SIZE, device=None)
            spk.start()
            voice = bytes(range(256)) * 4
            spk.write(voice)
            assert ws.receive_bytes() == voice, "JARVIS's voice did not arrive"
            spk.stop(); spk.close()

        r = client.get("/status", headers={"Authorization": f"Bearer {bearer}"})
        snap = r.json()
        assert snap["audio_out"]["frames"] >= 1
        assert "tools" in snap and "wake" in snap, (
            "/status is not reading JarvisLive's own registries")
    return (f"{main.RECEIVE_SAMPLE_RATE} Hz PCM: _play_audio → "
            f"/ws/phone-out → client")


# ── 8. the /status contract a deployment depends on ──────────────────────────

@check("/status keeps its deployment contract and leaks no secret")
def _status_contract():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    from server import run_headless

    dash, state, _hub, _tok = _build_dashboard()
    bearer = "t-" + "c" * 20
    dash._tokens.add(bearer)

    # Make the link counters real: one session opened, then lost.
    state.note_link(True)
    state.note_link(False)
    state.note_link(True)

    with TestClient(dash.app) as client:
        r = client.get("/status", headers={"Authorization": f"Bearer {bearer}"})
        assert r.status_code == 200
        body = r.json()
        raw = r.text

    # What ORACLE.md tells an operator to watch. Renaming any of these silently
    # breaks a monitoring script that has no way to notice.
    required = ["service", "uptime_s", "state", "session_live", "session_held_s",
                "longest_session_s", "phone", "counts", "audio_out"]
    missing = [k for k in required if k not in body]
    assert not missing, f"/status is missing {missing}"
    for k in ("sessions", "reconnects", "phone_connects", "errors"):
        assert k in body["counts"], f"counts is missing {k!r}"
    assert body["counts"]["sessions"] == 2 and body["counts"]["reconnects"] == 1, (
        f"link counters wrong: {body['counts']}")
    assert "audio_streams" in body["phone"]

    # phone_mic_streaming comes from run_headless._extras, not RuntimeState, so
    # it is checked against that function directly.
    class _FakeJarvis:
        session = object()
        _phone_active = True
        _wake_enabled = False
        _awake = True
        _resume_handle = None
    extras = run_headless._extras(_FakeJarvis())
    assert extras.get("phone_mic_streaming") is True, (
        "_extras no longer reports phone_mic_streaming")

    # Nothing in here may be a credential. The resumption handle is a key to the
    # conversation, the device token is full control of JARVIS.
    for secret in ("gemini_api_key", "device_token", "_resume_handle"):
        assert secret not in raw, f"/status mentions {secret!r}"
    assert body.get("session_resumable") in (True, False, None), (
        "session_resumable must stay a boolean, never the handle itself")
    return f"{len(required)} fields, counters correct, no secret in the body"


# ── 13. interrupt reaches the audio actually queued for the phone ────────────

@check("interrupt empties what is already queued for every listener")
def _hub_flush():
    import asyncio

    from server.audio_bridge import AudioHub

    async def run():
        hub = AudioHub()
        sub = hub.subscribe()
        for i in range(40):
            hub.publish(bytes([i]))
        await asyncio.sleep(0)
        assert sub.queue.qsize() == 40, f"expected 40 queued, got {sub.queue.qsize()}"

        # This is the half of an interrupt that main.py's interrupt() cannot do:
        # it drains the queue feeding _play_audio, but Gemini generates faster
        # than real time, so a whole answer can already be past that point and
        # sitting here. Without this the listener speaks the abandoned answer
        # for up to ~40 s after the user asked for silence.
        hub.flush()
        await asyncio.sleep(0)

        assert sub.queue.qsize() == 0, (
            f"{sub.queue.qsize()} frames survived the flush — the phone would "
            f"keep talking after an interrupt")
        assert sub.flushed == 40, f"flushed counter says {sub.flushed}, expected 40"

        # A flush with nothing queued must be harmless: interrupt is a button a
        # user can press twice.
        hub.flush()
        await asyncio.sleep(0)
        return f"{sub.flushed} queued frames discarded, empty flush is a no-op"

    return asyncio.run(run())


# ── 14. the two client→server events are carried and validated ───────────────

@check("/ws carries interrupt and confirmation_response to the server")
def _ws_control_events():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    dash, _state, _hub, _tok = _build_dashboard()
    bearer = "t-" + "z" * 20
    dash._tokens.add(bearer)

    seen: dict = {"interrupts": 0, "confirms": []}
    dash.set_interrupt_callback(lambda: seen.__setitem__("interrupts",
                                                         seen["interrupts"] + 1))
    dash.set_confirm_callback(
        lambda accepted, cid: seen["confirms"].append((accepted, cid)))

    with TestClient(dash.app) as client:
        with client.websocket_connect(f"/ws?token={bearer}") as ws:
            ws.send_json({"type": "interrupt"})
            ws.send_json({"type": "confirmation_response",
                          "id": "abc123", "confirmed": True})
            ws.send_json({"type": "confirmation_response",
                          "id": "def456", "confirmed": False})
            # An unknown type must be ignored, not crash the socket: older and
            # newer clients share this channel.
            ws.send_json({"type": "something_from_a_future_client"})
            ws.send_json({"type": "interrupt"})
            # Round-trip a command to be sure the socket is still alive after
            # all of the above.
            ws.send_json({"type": "command", "text": "ping"})

    assert seen["interrupts"] == 2, f"interrupts delivered: {seen['interrupts']}"
    assert seen["confirms"] == [(True, "abc123"), (False, "def456")], (
        f"confirmation decisions delivered: {seen['confirms']}")
    return "2 interrupts, 2 decisions with ids, unknown type ignored"


# ── 15. a decision cannot be replayed onto a different request ───────────────

@check("a confirmation answer only resolves the request it names")
def _confirm_identity():
    import time as _time

    from core import confirm as gate

    shown: list = []
    ran:   list = []
    gate.bind(show=lambda t, d: shown.append(t), hide=lambda: None,
              log=lambda _m: None)

    gate.request("k", "Empty the trash", "-", lambda: ran.append("trash") or "ok")
    first = gate.pending_id()
    assert first, "request issued no id"
    gate.resolve(True, first)
    _time.sleep(0.15)
    assert ran == ["trash"], f"confirm did not run the action: {ran}"

    # Replay of the same decision.
    gate.resolve(True, first)
    _time.sleep(0.15)
    assert ran == ["trash"], "a replayed answer ran the action twice"

    # The dangerous one: a client still showing the old banner answers after the
    # request has been replaced. Its yes must not run the new action, and must
    # not consume the new request either.
    gate.request("k2", "Shut down the machine", "-",
                 lambda: ran.append("shutdown") or "ok")
    second = gate.pending_id()
    gate.resolve(True, first)
    _time.sleep(0.15)
    assert "shutdown" not in ran, "a stale id confirmed a different action"
    assert gate.pending_id() == second, "a stale answer consumed the live request"

    # Expiry: nothing runs, whatever the client says.
    gate._pending.at -= (gate.TIMEOUT_SECONDS + 1)
    assert gate.pending_id() == "", "an expired request still reports an id"
    gate.resolve(True, second)
    _time.sleep(0.15)
    assert "shutdown" not in ran, "an expired confirmation ran the action"

    # The desktop HUD calls resolve() with no id at all; that must still work.
    gate.request("k3", "From the HUD", "-", lambda: ran.append("hud") or "ok")
    gate.resolve(True)
    _time.sleep(0.15)
    assert ran == ["trash", "hud"], f"HUD path broken: {ran}"

    gate.bind(show=None, hide=None, log=None)
    return "replay, stale id, expiry refused; HUD path unchanged"


# ── 16. notifications: the transport for a decision that is not speech ───────

@check("notification levels are exactly context/'s priority vocabulary")
def _notify_vocabulary():
    """server/notify.py deliberately does NOT import context.Priority — deleting
    the optional context package must not take notifications with it. The cost
    of that independence is two copies of one vocabulary, so this is the check
    that makes the copies stay equal."""
    from server.notify import PRIORITIES
    try:
        from context.model import Priority
    except ImportError:
        return f"{len(PRIORITIES)} levels (context/ absent, nothing to compare)"
    theirs = tuple(p.value for p in Priority)
    assert set(PRIORITIES) == set(theirs), (
        f"drift: notify has {PRIORITIES}, context has {theirs}")
    return f"{len(PRIORITIES)} levels, identical on both sides"


@check("the hub refuses what must never reach the shade")
def _notify_refuses():
    from server import notify as N

    N.reset()
    hub = N.get_hub()

    # TRIVIAL is DROP in every cell of the policy table; delivering it would
    # contradict the table, so it is refused at the door rather than left for
    # the phone to decide.
    assert hub.notify("TRIVIAL", "x", "y") is None, "TRIVIAL was accepted"
    # A notification with no body is a buzz carrying no information.
    assert hub.notify("IMPORTANT", "x", "   ") is None, "empty text was accepted"
    assert hub.notify("IMPORTANT", "x", None) is None, "null text was accepted"
    # An unknown level must not escalate into an interruption.
    note = hub.notify("WHATEVER", "t", "body")
    assert note is not None and note.priority == "USEFUL", note
    stats = hub.stats()
    assert stats["refused"] == 3, stats
    assert stats["created"] == 1, stats
    N.reset()
    return "TRIVIAL, empty and unknown handled; unknown falls to USEFUL"


@check("a notification reaches the dashboard's broadcast, once")
def _notify_broadcast():
    import asyncio as _a

    from server import notify as N

    sent: list = []

    class _FakeDash:
        async def broadcast(self, msg):
            sent.append(msg)

    async def run():
        N.reset()
        hub = N.get_hub()
        hub.bind_dashboard(_FakeDash())
        note = hub.notify("CRITICAL", "Fuite", "Le detecteur du garage a sonne.")
        assert note is not None
        await _a.sleep(0)          # laisser partir le call_soon_threadsafe
        await _a.sleep(0)
        return note

    note = _a.run(run())
    assert len(sent) == 1, f"{len(sent)} broadcasts for one notification"
    event = sent[0]
    assert event["type"] == "notification", event
    for field in ("id", "priority", "title", "text", "ts"):
        assert field in event, f"event is missing {field!r}"
    assert event["priority"] == "CRITICAL" and event["id"] == note.id
    assert isinstance(event["ts"], float), "ts must be a number the client can compare"
    N.reset()
    return "5 fields, one broadcast, id matches the record"


@check("a reconnecting client is re-offered recent notifications, never stale ones")
def _notify_replay():
    """The whole reason notifications are not lost while the phone is away —
    and the whole reason they must not come back as news a day later."""
    import time as _t

    from server import notify as N

    N.reset()
    hub = N.NotificationHub(ttl_s=60.0)
    fresh = hub.notify("IMPORTANT", "Facture", "Elle est due demain.")
    assert fresh is not None
    # Age one notification past the window by hand rather than by sleeping.
    old = hub.notify("IMPORTANT", "Vieux", "Date de longtemps.")
    assert old is not None
    old.created_at = _t.time() - 3600

    pending = hub.pending()
    ids = [p["id"] for p in pending]
    assert fresh.id in ids, "a fresh notification was not re-offered"
    assert old.id not in ids, "a stale notification was re-offered as news"
    N.reset()
    return "fresh re-offered, 1 h old withheld (ttl 60 s)"


@check("/ws replays pending notifications the moment a client connects")
def _notify_on_connect():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    from server import notify as N

    dash, _state, _hub, _tok = _build_dashboard()
    bearer = "t-" + "n" * 20
    dash._tokens.add(bearer)

    N.reset()
    note = N.get_hub().notify("IMPORTANT", "Rendez-vous", "Dans 15 minutes.")
    assert note is not None

    received: list = []
    with TestClient(dash.app) as client:
        with client.websocket_connect(f"/ws?token={bearer}") as ws:
            # History first, then pending. Read a bounded number of frames and
            # look for ours rather than assuming a position: other checks in
            # this file may have left messages in the same history.
            for _ in range(80):
                try:
                    received.append(ws.receive_json())
                except Exception:
                    break
                if received[-1].get("id") == note.id:
                    break

    mine = [m for m in received if m.get("id") == note.id]
    assert mine, "the pending notification was not sent on connect"
    assert mine[0]["type"] == "notification" and mine[0]["text"] == "Dans 15 minutes."
    N.reset()
    return "delivered on connect, without a producer being present"


@check("/api/notify authenticates, produces, and refuses an empty body")
def _notify_route():
    if not _fastapi_available():
        return "skipped (fastapi/httpx not installed)"
    from fastapi.testclient import TestClient

    from server import notify as N

    dash, _state, _hub, _tok = _build_dashboard()
    bearer = "t-" + "q" * 20
    dash._tokens.add(bearer)
    auth_header = {"Authorization": f"Bearer {bearer}"}

    N.reset()
    with TestClient(dash.app) as client:
        r = client.post("/api/notify", json={"priority": "IMPORTANT",
                                             "title": "T", "text": "B"})
        assert r.status_code == 401, f"unauthenticated -> {r.status_code}"

        r = client.post("/api/notify", headers=auth_header,
                        json={"priority": "IMPORTANT", "title": "T", "text": "B"})
        assert r.status_code == 200, f"authorised -> {r.status_code}: {r.text}"
        assert r.json()["ok"] is True and r.json()["id"]

        r = client.post("/api/notify", headers=auth_header,
                        json={"priority": "IMPORTANT", "title": "T", "text": ""})
        assert r.status_code == 400, f"empty text -> {r.status_code}"

        r = client.post("/api/notify", headers=auth_header,
                        json={"priority": "TRIVIAL", "title": "T", "text": "B"})
        assert r.status_code == 400, f"TRIVIAL -> {r.status_code}"

        # And the counters must reach /status without leaking any content.
        r = client.get("/status", headers=auth_header)
        body, raw = r.json(), r.text
        assert "notifications" in body, "/status does not report the counters"
        assert body["notifications"]["created"] >= 1, body["notifications"]
        for key in body["notifications"]:
            assert key in ("created", "delivered", "refused", "replayed", "held"), (
                f"/status exposes an unexpected notification field: {key}")
        assert "Rendez-vous" not in raw, "/status leaks notification content"
    N.reset()
    return "401 / 200 / 400 / 400, counters on /status, no content in it"


@check("the Android client's dedup outlives the server's replay window")
def _notify_android_contract():
    """Three constants in two languages have to agree or notifications repeat.

    SEEN_LIMIT (Kotlin) must exceed the hub's `maxlen`, or an id can age out of
    the phone's memory while the server is still re-offering it — which shows
    the user the same alert again with nothing in either log to explain it.
    """
    import re

    root = BASE_DIR / "client-android" / "app" / "src" / "main" / "java" / "com" / "jarvis"
    manager = root / "notification" / "JarvisNotificationManager.kt"
    mapper = root / "notification" / "NotificationMapper.kt"
    if not manager.exists() or not mapper.exists():
        return "skipped (client Android absent)"

    text = manager.read_text(encoding="utf-8")
    m = re.search(r"SEEN_LIMIT\s*=\s*(\d+)", text)
    assert m, "SEEN_LIMIT introuvable"
    seen_limit = int(m.group(1))

    from server.notify import NotificationHub
    server_maxlen = NotificationHub()._recent.maxlen
    assert seen_limit > server_maxlen, (
        f"SEEN_LIMIT {seen_limit} <= hub maxlen {server_maxlen}: "
        "an id can be forgotten while still replayable")

    # The mapper must name every level the server can actually send.
    mapper_text = mapper.read_text(encoding="utf-8")
    from server.notify import PRIORITIES, _NEVER_DELIVERED
    for level in PRIORITIES:
        assert f'"{level}"' in mapper_text, f"the mapper never mentions {level}"
    for level in _NEVER_DELIVERED:
        assert f'priority == "{level}"' in mapper_text, (
            f"the mapper does not refuse {level} defensively")

    # The foreground notification's id must stay out of reach.
    assert "BASE_NOTIFICATION_ID = 100_000" in mapper_text, (
        "the notification id base moved - check it cannot collide with NOTIF_ID 5301")
    return f"SEEN_LIMIT {seen_limit} > hub {server_maxlen}, 4 levels mapped"


# ── report ───────────────────────────────────────────────────────────────────

def main() -> int:
    width = max(len(n) for n, _, _ in _results)
    failed = 0
    print()
    for name, ok, detail in _results:
        if ok and detail.startswith("skipped"):
            mark = "SKIP"
        elif ok:
            mark = "PASS"
        else:
            mark = "FAIL"
            failed += 1
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    print()
    total = len(_results)
    print(f"  {total - failed}/{total} checks passed.")
    if failed:
        print("  Phase 2 is NOT green — fix the above before deploying.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
