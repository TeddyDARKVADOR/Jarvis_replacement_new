"""
server/headless_ui.py — the HUD, with no screen behind it.

WHY THIS WORKS WITHOUT TOUCHING main.py
    JarvisLive never asks what kind of object its `ui` is. It assigns callbacks
    onto it, reads two properties, and calls twenty-odd methods — ui.py's
    JarvisUI is already just a façade over the Qt window (see ui.py:4526). So a
    class with the same surface is accepted as-is, and `python main.py` on the
    desktop is not affected in any way: it still builds the real JarvisUI.

    The surface is not guessed. server/selftest.py parses main.py with `ast`,
    collects every `self.ui.<attr>` it touches, and fails if one is missing
    here — so this file cannot silently fall behind main.py.

WHERE THE HUD OUTPUT GOES INSTEAD
    write_log   → RuntimeState ring buffer  → GET /status
                → dashboard broadcast as {"type": "sys"}  → phone
    set_state   → RuntimeState              → GET /status
                → dashboard broadcast as {"type": "jarvis_state"}  → phone

    "jarvis_state" is a NEW message type on purpose. dashboard/static/app.html
    only understands status values "active" and "sleeping"; pushing LISTENING /
    THINKING through the existing "status" channel would make the current web
    client show the wrong thing. A type it does not know, it ignores.

THE CONFIRMATION GATE
    show_confirm() puts an irreversible action behind a CONFIRM button
    (core/confirm.py). There is no button on this host, so the request is sent
    to the connected client as a `confirm` event carrying the id core/confirm.py
    issued for it, and the client's answer comes back as
    `confirmation_response`. The decision is all that travels: what runs, and
    whether anything runs at all, is still decided here against the pending
    request and its 90 s expiry. A client that answers late, twice, or with the
    id of a request that has been replaced changes nothing.

    With no client connected the behaviour is the old one and the safe one: the
    request is announced, surfaced in /status, and expires unconfirmed.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from core import confirm as confirm_gate

from .runtime_state import RuntimeState, State

BASE_DIR = Path(__file__).resolve().parent.parent

# How long to wait before letting main.py retry after it reported an invalid
# API key. main.py parks on `while not self.ui._win._ready` and would otherwise
# hammer the API; on a server the key can be fixed in config/api_keys.json
# while the process keeps running, so re-arming after a pause is what allows
# that fix to take effect without a restart.
_REARM_AFTER_BAD_KEY = 60.0


class _WinShim:
    """main.py:1694 reads `self.ui._win._ready` directly while waiting for a
    re-entered API key. That is the only member of the real MainWindow it ever
    touches, so this is the whole of it."""

    def __init__(self):
        self._ready = True


class _RootShim:
    """Mirrors ui._RootShim. Never used headless — run_headless drives the
    asyncio loop itself — but the façade would be incomplete without it."""

    def mainloop(self):
        while True:
            time.sleep(3600)

    def protocol(self, *_a):
        pass


class HeadlessUI:
    """Stand-in for ui.JarvisUI. No Qt, no window, no display required."""

    def __init__(self, state: RuntimeState | None = None, echo: bool = True):
        self.state = state or RuntimeState()
        self._echo = echo                  # mirror log lines to stdout (journalctl)
        self._win  = _WinShim()
        self.root  = _RootShim()

        self._muted = False
        self._dashboard = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # ── Callbacks JarvisLive assigns onto the UI ─────────────────────────
        # Declared here so they exist before __init__ of JarvisLive runs and so
        # a stale call can never raise AttributeError mid-session.
        self.on_text_command        = None
        self.on_remote_clicked      = None
        self.on_interrupt           = None
        self.on_voice_change        = None
        self.on_audio_device_change = None
        self.get_plugins            = None
        self.get_plugin_settings    = None
        self.request_say            = None
        self.wake_is_ready          = None
        self.wake_get_state         = None
        self.on_wake_toggle         = None
        self.on_wake_manual         = None
        self.on_wake_install        = None

    # ── wiring done by run_headless ──────────────────────────────────────────

    def bind_dashboard(self, dashboard) -> None:
        """Called once, from the loop, as soon as DashboardServer exists."""
        self._dashboard = dashboard
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ── properties JarvisLive reads ──────────────────────────────────────────

    @property
    def muted(self) -> bool:
        # Must stay False: _relay_phone_audio drops the phone's microphone
        # while muted, so a True here makes a headless JARVIS deaf.
        return self._muted

    @muted.setter
    def muted(self, v: bool) -> None:
        self._muted = bool(v)

    @property
    def current_file(self) -> str | None:
        """The desktop has a drag-and-drop zone; the phone has POST /api/upload.
        Pointing this at the newest upload is what makes "analyse this file"
        work from the phone — main.py already falls back to it for
        file_processor (main.py:867), so nothing new is wired, only fed."""
        try:
            updir = getattr(self._dashboard, "_uploads_dir", None)
            if updir is None:
                return None
            files = [p for p in Path(updir).iterdir() if p.is_file()]
            if not files:
                return None
            return str(max(files, key=lambda p: p.stat().st_mtime))
        except Exception:
            return None

    @property
    def assistant_name(self) -> str:
        try:
            cfg = json.loads((BASE_DIR / "config" / "api_keys.json")
                             .read_text(encoding="utf-8"))
            return (cfg.get("assistant_name") or "JARVIS").strip()
        except Exception:
            return "JARVIS"

    # ── the HUD surface ──────────────────────────────────────────────────────

    def write_log(self, text: str) -> None:
        line = str(text)
        self.state.log(line)
        if self._echo:
            print(f"[JARVIS] {line}", flush=True)
        self._emit({"type": "sys", "text": line[:500]})

    def set_state(self, state: str) -> None:
        self.state.note_ui_state(state)
        self._emit({"type": "jarvis_state", "state": str(state).upper()})

    def set_audio_level(self, level: float) -> None:
        self.state.set_audio_level(level)

    def show_content(self, title: str, text: str) -> None:
        """Desktop shows this in the panel under the HUD; the phone gets it as
        a message it can render however it likes."""
        self._emit({"type": "content",
                    "title": str(title)[:48], "text": str(text)[:4000]})

    def notify_phone_connected(self) -> None:
        self.state.note_phone_connected()

    # ── confirmation gate ────────────────────────────────────────────────────

    def show_confirm(self, title: str, detail: str) -> None:
        t = str(title)[:120]
        self.state.note_pending_confirmation(t)

        # The id core/confirm.py issued for this request. It is read here rather
        # than passed in because the HUD's show(title, detail) signature is
        # shared with the desktop and there was no reason to change it there.
        # A client must quote this back for its answer to count.
        cid = confirm_gate.pending_id()

        self.write_log(f"SYS: Confirmation required — {t}")
        self._emit({
            "type":   "confirm",
            "id":     cid,
            "title":  t,
            "detail": str(detail)[:300],
            "timeout_s": round(confirm_gate.seconds_left()),
        })

    def hide_confirm(self) -> None:
        self.state.note_pending_confirmation("")
        self._emit({"type": "confirm_hide"})

    # ── startup / reconfiguration ────────────────────────────────────────────

    def wait_for_api_key(self) -> None:
        """The desktop blocks here until the setup overlay is filled in. A
        server has nobody to fill it in, so an absent key is a startup failure
        with an actionable message rather than a silent forever-wait."""
        try:
            from memory.config_manager import get_gemini_key
            key = get_gemini_key()
        except Exception:
            key = None
        if not key:
            raise SystemExit(
                "No Gemini API key found.\n"
                f"  Expected \"gemini_api_key\" in: {BASE_DIR / 'config' / 'api_keys.json'}\n"
                "  Copy the file from your desktop install, or create it with that one field."
            )

    def prompt_reconfig(self) -> None:
        """main.py calls this on an invalid key, then waits on _win._ready."""
        self._win._ready = False
        self.state.set(State.ERROR)
        self.write_log(
            "ERR: Gemini rejected the API key. Fix \"gemini_api_key\" in "
            f"{BASE_DIR / 'config' / 'api_keys.json'} — retrying in "
            f"{int(_REARM_AFTER_BAD_KEY)}s."
        )

        def _rearm():
            time.sleep(_REARM_AFTER_BAD_KEY)
            self._win._ready = True
            self.write_log("SYS: Retrying the Gemini connection.")

        threading.Thread(target=_rearm, daemon=True,
                         name="headless-key-rearm").start()

    # ── camera (no screen to show it on) ─────────────────────────────────────

    def show_camera_frame(self, img_bytes: bytes) -> None:
        pass

    def start_camera_stream(self) -> None:
        # A server has no webcam; screen_process(angle="camera") will already
        # have failed in _capture_camera and been reported as a tool error.
        pass

    def stop_camera_stream(self) -> None:
        pass

    # ── convenience mirrors of JarvisUI ──────────────────────────────────────

    def start_speaking(self) -> None:
        self.set_state("SPEAKING")

    def stop_speaking(self) -> None:
        if not self.muted:
            self.set_state("LISTENING")

    # ── internal ─────────────────────────────────────────────────────────────

    def _emit(self, msg: dict) -> None:
        """Push to every connected dashboard/phone client.

        Called from the asyncio loop, from Qt-less executor threads, and from
        the audio writer thread — so it never assumes it is on the loop."""
        dash = self._dashboard
        if dash is None:
            return
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                return

        def _fire():
            try:
                asyncio.ensure_future(dash.broadcast(msg))
            except Exception:
                pass

        try:
            if loop.is_running():
                loop.call_soon_threadsafe(_fire)
        except RuntimeError:
            pass


class _JarvisUICompat(HeadlessUI):
    """Same constructor signature as ui.JarvisUI(face_path, size=None).

    run_headless builds its HeadlessUI directly, so this is only reached if
    something calls the module the way main.main() would — in which case it
    should get a working headless UI rather than a TypeError, or worse, a
    HeadlessUI whose `state` is the string "face.png"."""

    def __init__(self, face_path=None, size=None, *, state=None, echo=True):
        super().__init__(state=state, echo=echo)


# main.py does `from ui import JarvisUI`. run_headless installs this module
# under the name "ui", so that import resolves to the class below.
JarvisUI = _JarvisUICompat
