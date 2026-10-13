"""
JARVIS on this PC, with the same HUD as JARVIS on Oracle.

```
python main.py --local          the brain here (Gemini key in config/api_key.json or legacy api_keys.json)
python main.py                  the brain on Oracle, this PC as its terminal
python main.py --classic        the original window, unchanged
```

ONE INTERFACE, TWO BRAINS
    Online, the HUD renders a `client_desktop.state.JarvisStore` that the
    network worker fills from Oracle's sockets. Here the same store is filled by
    `LocalUI`, the object `main.JarvisLive` is handed as its UI.
    The window, the page, the store and the tray are the very same objects in
    both modes — so the two cannot drift apart: there is nothing to keep in sync.

WHY THIS FILE IS NOT IN client_desktop/
    `client_desktop/selftest.py` fails if anything in that package imports
    `main` — the thin client must never grow a brain by accident. This file does
    import it, on purpose, so it lives beside main.py instead.

WHY LocalUI DERIVES FROM server.headless_ui.HeadlessUI
    HeadlessUI already answers every `self.ui.<attr>` main.py uses, and
    `server/selftest.py` re-checks that against main.py's own syntax tree on
    every run. Deriving from it inherits that guarantee: a call main.py gains
    one day fails a test instead of an AttributeError mid-session. Only what has
    somewhere to go on a desktop is overridden.
"""

from __future__ import annotations

import re
import os
import sys
import threading
import time
import hashlib
import math
from pathlib import Path
from config import api_key_path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from client_desktop.state import AssistantState, JarvisStore, LinkState, Message  # noqa: E402
from server.headless_ui import HeadlessUI  # noqa: E402

_STATES = {
    "LISTENING": AssistantState.LISTENING,
    "THINKING": AssistantState.THINKING,
    "SPEAKING": AssistantState.SPEAKING,
    "SLEEPING": AssistantState.SLEEPING,
}
_LINE = re.compile(r"^\s*(\[?[^:\]\n]{1,32}\]?):\s?(.*)$", re.S)


class LocalUI(HeadlessUI):
    """What main.JarvisLive draws on, translated into the HUD's store."""

    # Plugins can use this capability flag to distinguish a desktop brain with
    # a real browser from the server's headless UI without importing either
    # host implementation or changing the plugin protocol.
    local_mode = True

    def __init__(self, store: JarvisStore) -> None:
        super().__init__(echo=True)
        self.store = store
        #: The file the user attached in the HUD (📎 or drag-and-drop): what
        #: main.py's file_processor reads when asked about "this file".
        self.attached: str | None = None
        self.health_provider = None
        #: Set by the host: receives JPEG bytes for the HUD's camera view, or
        #: None when it closes. Called from the camera thread.
        self.on_camera_frame = None
        self._camera_stop = threading.Event()
        store.set_link(LinkState.CONNECTING)

    def health_snapshot(self) -> dict:
        """Read-only local runtime facts for the optional health plugin."""
        provider = self.health_provider
        if not callable(provider):
            return {"error": "état détaillé indisponible pendant le démarrage"}
        try:
            result = provider()
            return result if isinstance(result, dict) else {"error": "état invalide"}
        except Exception as exc:
            return {"error": f"lecture de l'état impossible : {exc}"}

    @property
    def current_file(self) -> str | None:
        if self.attached and Path(self.attached).is_file():
            return self.attached
        return None

    # ── text ─────────────────────────────────────────────────────────────────

    def write_log(self, text: str) -> None:
        line = str(text)
        try:
            print(f"[JARVIS] {line}", flush=True)
        except Exception:
            pass
        m = _LINE.match(line)
        who = m.group(1).strip() if m else ""
        body = m.group(2).strip() if m else line
        name = (self.assistant_name or "JARVIS").lower()

        if who in ("You", "Vous", "[Web]") and body:
            self.store.add_message(Message(False, body, time.time()))
        elif who.lower() in (name, "jarvis") and body:
            self.store.add_message(Message(True, body, time.time()))
        else:
            self.store.log(line)
            self._follow_link(line)

    def _follow_link(self, line: str) -> None:
        """The 'link' of a local JARVIS is its Gemini Live session."""
        low = line.lower()
        if "jarvis online" in low or "reconnected — conversation restored" in low:
            self.store.set_link(LinkState.CONNECTED)
        elif low.startswith("net:") and "retrying in" in low:
            delay = re.search(r"retrying in\s+(\d+)s", low)
            snap = self.store.snapshot()
            self.store.set_retry(snap.attempt, int(delay.group(1)) if delay else 0)
            self.store.set_link(LinkState.RECONNECTING)
        elif "applying" in low and "reconnecting" in low:
            self.store.set_link(LinkState.RECONNECTING)
        elif "api key invalid" in low or "rejected the api key" in low:
            self.store.set_link(LinkState.ERROR,
                                f"Clé Gemini refusée — corrigez {api_key_path(BASE_DIR).relative_to(BASE_DIR)}")
        elif low.startswith("sys: awake"):
            # The wake word was heard: replay a rising edge so the HUD shows
            # the same acknowledgement ring it shows online.
            self.store.set_gate_open(False)
            self.store.set_gate_open(not self._muted)

    def set_state(self, state: str) -> None:
        s = str(state).upper()
        # Assistant activity is not evidence of a live Gemini connection.
        # The session watcher below and the explicit "JARVIS online" log own
        # link transitions; otherwise the THINKING state at the start of a
        # failed connection attempt would make the HUD claim it was connected.
        snap = self.store.snapshot()
        if (s == "THINKING" and snap.link is LinkState.RECONNECTING
                and snap.next_retry_seconds == 0):
            self.store.set_link(LinkState.CONNECTING)
        if s == "LISTENING" and self._muted:
            # Never show "listening" with the microphone shut: that is the one
            # lie a voice interface must not tell. Muted and idle is "ready".
            s = "READY"
        self.store.set_assistant(_STATES.get(s, AssistantState.UNKNOWN))

    def speaking_finished(self) -> None:
        """main.py only announces LISTENING after speech when unmuted; muted,
        it says nothing and the HUD would stay on SPEAKING. Close the turn."""
        if self._muted:
            self.store.set_assistant(AssistantState.UNKNOWN)
            self.store.set_speaker_level(0.0)

    def set_audio_level(self, level: float) -> None:
        # main.py feeds one level from two places: the microphone callback and
        # the playback loop. The state says which one is talking.
        if self.store.snapshot().assistant is AssistantState.SPEAKING:
            self.store.set_speaker_level(float(level))
        else:
            self.store.set_mic_level(float(level))

    def show_content(self, title: str, text: str) -> None:
        self.store.add_message(Message(True, str(text)[:4000], time.time(), title=str(title)[:48]))

    def emit_event(self, msg: dict) -> None:
        # plugins/presence.py's `avatar` event: the expression JARVIS chose.
        if isinstance(msg, dict) and msg.get("type") == "avatar" and isinstance(msg.get("directive"), dict):
            age = max(0.0, time.time() - float(msg.get("ts") or time.time()))
            self.store.set_avatar_intent(msg["directive"], age=age)

    # ── the microphone ───────────────────────────────────────────────────────

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, v: bool) -> None:
        self._muted = bool(v)
        # Locally the microphone streams whenever it is not muted, so "open"
        # in the HUD is exactly "not muted" — never a decoration.
        self.store.set_gate_open(not self._muted)
        self.store.set_mic_open(not self._muted)
        now = self.store.snapshot().assistant
        if now not in (AssistantState.SPEAKING, AssistantState.THINKING, AssistantState.SLEEPING):
            self.store.set_assistant(AssistantState.UNKNOWN if self._muted else AssistantState.LISTENING)

    # ── confirmation gate ────────────────────────────────────────────────────

    def show_confirm(self, title: str, detail: str) -> None:
        from core import confirm as confirm_gate
        self.store.log(f"SYS: Confirmation required — {title}")
        self.store.set_confirmation(confirm_gate.pending_id(), str(title)[:120], str(detail)[:300],
                                    int(round(confirm_gate.seconds_left())))

    def hide_confirm(self) -> None:
        self.store.clear_confirmation()

    # ── the camera ───────────────────────────────────────────────────────────
    #
    # main.py asks the UI to show the webcam while it looks through it; the
    # frames it analyses are its own. This is only the user's view of it —
    # the same loop the original window ran, shown in the HUD's corner.

    def start_camera_stream(self) -> None:
        self._camera_stop.clear()
        threading.Thread(target=self._camera_loop, name="hud-camera", daemon=True).start()

    def stop_camera_stream(self) -> None:
        self._camera_stop.set()

    def show_camera_frame(self, img_bytes: bytes) -> None:
        if callable(self.on_camera_frame):
            self.on_camera_frame(bytes(img_bytes))

    def _camera_loop(self) -> None:
        try:
            import cv2
            from memory.config_manager import load_api_keys
            index = int(load_api_keys().get("camera_index", 0) or 0)
            backend = getattr(cv2, "CAP_DSHOW", 0) if sys.platform == "win32" else 0
            cap = cv2.VideoCapture(index, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            for _ in range(5):          # warm-up frames are dark
                cap.read()
            while not self._camera_stop.wait(0.05) and cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None and callable(self.on_camera_frame):
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self.on_camera_frame(buf.tobytes())
            cap.release()
        except Exception as exc:
            self.store.log(f"ERR: caméra — {exc}")
        finally:
            if callable(self.on_camera_frame):
                self.on_camera_frame(None)

    # ── the key ──────────────────────────────────────────────────────────────

    def wait_for_api_key(self) -> None:
        """Wait for a key instead of exiting: the HUD is up and can say so."""
        from memory.config_manager import get_gemini_key
        told = False
        while True:
            try:
                key = get_gemini_key()
            except Exception:
                key = None
            if key:
                self.store.set_link(LinkState.CONNECTING)
                return
            if not told:
                told = True
                key_path = api_key_path(BASE_DIR)
                self.store.set_link(LinkState.ERROR, f"Clé Gemini absente — ajoutez-la dans {key_path.relative_to(BASE_DIR)}")
                self.store.log(f"ERR: aucune clé Gemini dans {key_path}")
            time.sleep(2)

    def prompt_reconfig(self) -> None:
        """Park local retries until the rejected key is actually changed.

        main.JarvisLive already waits on ``_win._ready`` after this callback.
        The server UI keeps its timed re-arm; locally we can watch the config
        file and let the existing connection loop retry once a new key appears.
        No Gemini session or retry code is changed here.
        """
        self._win._ready = False
        self.store.set_link(
            LinkState.ERROR,
            f"Clé Gemini refusée — modifiez {api_key_path(BASE_DIR).relative_to(BASE_DIR)} ; Jarvis attend.",
        )
        self.write_log(
            "ERR: Clé Gemini refusée. Modifiez gemini_api_key dans "
            f"{api_key_path(BASE_DIR)} ; aucune nouvelle tentative avant modification."
        )

        try:
            from memory.config_manager import get_gemini_key
            previous = get_gemini_key() or ""
        except Exception:
            previous = ""
        previous_hash = hashlib.sha256(previous.encode("utf-8")).digest()

        def wait_for_changed_key() -> None:
            while True:
                time.sleep(1)
                try:
                    from memory.config_manager import get_gemini_key
                    candidate = get_gemini_key() or ""
                except Exception:
                    candidate = ""
                candidate_hash = hashlib.sha256(candidate.encode("utf-8")).digest()
                if candidate and candidate_hash != previous_hash:
                    self._win._ready = True
                    self.store.set_link(LinkState.RECONNECTING)
                    self.write_log("SYS: Clé Gemini modifiée — nouvelle tentative de connexion.")
                    return

        threading.Thread(target=wait_for_changed_key, daemon=True,
                         name="jarvis-local-key-watch").start()


class LocalBackend:
    """The HUD's backend when the brain runs here — see client_desktop/hud/backends.py.

    Everything the settings page asks goes to `server.assistant_control`, the
    very object Oracle serves behind /api/assistant. Two things only exist
    locally: the attached file (the brain reads it straight from disk) and the
    phone pairing (the brain's own dashboard hands out the key).
    """

    def __init__(self, ui: LocalUI, brain_ref: dict) -> None:
        from server.assistant_control import AssistantControl
        self._ui = ui
        self._brain = brain_ref
        self._control = AssistantControl(lambda: ui)

    def capabilities(self) -> dict:
        return {"local": True, "memory": True, "profile": True, "plugins": True,
                "phone": True, "files": True, "studio": True, "cognition": True}

    def call(self, op: str, data: dict | None = None) -> dict:
        if op == "phone.pair":
            return self._pair_phone()
        if str(op).startswith("studio."):
            # Jarvis Code Studio (core/code_workspace): the workspace is on
            # this PC, so only the local brain answers these.
            from core.code_workspace import get_studio
            studio = get_studio()
            studio.bind(self._ui)
            return studio.hud(op, data)
        if str(op).startswith("cognition."):
            # Panneau ESPRIT (module cognition.hud) : la base cognitive est sur ce
            # PC. Absente ou désactivée -> une erreur lisible, rien d'autre.
            try:
                from cognition import get_active
            except Exception:
                return {"error": "module cognition absent"}
            cog = get_active()
            if cog is None:
                return {"error": "la cognition n'est pas active (JARVIS démarre encore ?)"}
            return cog.hud(op, data)
        return self._control.call(op, data)

    def attach_file(self, path: str) -> dict:
        p = Path(path)
        if not p.is_file():
            return {"error": "fichier introuvable"}
        self._ui.attached = str(p)
        self._ui.store.log(f"SYS: fichier joint — {p.name}")
        return {"ok": True, "name": p.name}

    def clear_file(self) -> None:
        self._ui.attached = None

    def _pair_phone(self) -> dict:
        jarvis = self._brain.get("jarvis")
        make = getattr(jarvis, "_make_remote_key", None)
        if not callable(make):
            return {"error": "JARVIS démarre encore — réessayez dans un instant."}
        made = make()
        if not made:
            return {"error": "tableau de bord indisponible (pip install fastapi \"uvicorn[standard]\" cryptography)"}
        url, key, auto_url, manual = made
        qr = ""
        try:
            import base64
            import io

            import qrcode
            buf = io.BytesIO()
            qrcode.make(auto_url).save(buf, format="PNG")
            qr = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            pass
        return {"ok": True, "url": url, "key": key, "auto_url": auto_url, "manual": manual, "qr": qr}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Let the shared startup setting preserve this brain choice at next login.
    os.environ["JARVIS_LOCAL_MODE"] = "1"

    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    from client_desktop.config import load
    from client_desktop.hud import boot
    from client_desktop.single_instance import SingleInstance

    guard = SingleInstance()
    if not guard.acquire():
        guard.signal_existing()
        print("JARVIS est déjà en cours d'exécution — instance existante affichée.")
        return 0

    if not boot.prepare():
        print(boot.MISSING_WEBENGINE)
        guard.release()
        return 1
    app = QApplication(sys.argv[:1])
    app.setApplicationName("JARVIS")
    app.setQuitOnLastWindowClosed(False)

    from client_desktop.hud.window import HudWindow
    from client_desktop.ui.tray import JarvisTray

    settings = load()
    store = JarvisStore()
    hud = HudWindow(settings)
    hud.local_mode = True
    hud.attach_store(store)
    tray = JarvisTray()
    ui = LocalUI(store)
    brain_ref: dict = {}
    hud.backend = LocalBackend(ui, brain_ref)
    # The Code Studio opens its web previews in the HUD's isolated preview window.
    from core.code_workspace import get_studio
    get_studio().preview_opener = hud.request_preview

    def local_audio() -> dict:
        """The page's audio tab shows main.py's own devices and wake word."""
        from memory import config_manager as cm
        return {"input_device": cm.get_input_device() or None,
                "output_device": cm.get_output_device() or None,
                "wake_word_enabled": bool(cm.get_wake_word_enabled())}

    hud.settings_overrides = local_audio

    # ── store events → GUI thread (same marshalling as app.JarvisDesktop) ──
    class _Bridge(QObject):
        event = pyqtSignal(str, object)
        camera = pyqtSignal(object)

    bridge = _Bridge()
    bridge.camera.connect(hud.show_camera_frame)
    ui.on_camera_frame = bridge.camera.emit
    store.subscribe(lambda kind, payload: bridge.event.emit(kind, payload))

    def on_event(kind: str, payload: object) -> None:
        if kind == "avatar" and isinstance(payload, dict):
            hud.set_avatar_intent(payload)
        elif kind == "confirm":
            hud.show()
            hud.raise_()
            if settings.notifications:
                tray.notify(f"JARVIS — confirmation : {payload[1]}", payload[2] or "")
        elif kind == "message" and getattr(payload, "from_jarvis", False) and settings.notifications:
            if not hud.isVisible() or hud.isMinimized() or hud._mini:
                tray.notify("JARVIS", payload.text[:180])

    bridge.event.connect(on_event)

    # ── intent from the HUD → the local brain ──
    def call(name: str, *args) -> None:  # noqa: ANN002
        fn = getattr(ui, name, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception as exc:
                store.log(f"ERR: {name} — {exc}")

    def set_mic(open_: bool) -> None:
        snap = store.snapshot()
        if open_ and snap.assistant is AssistantState.SLEEPING:
            call("on_wake_manual")          # clicking the core wakes him
        ui.muted = not open_

    def answer(cid: str, accepted: bool) -> None:
        from core import confirm as confirm_gate
        store.clear_confirmation()
        try:
            confirm_gate.resolve(bool(accepted), cid)
        except TypeError:
            confirm_gate.resolve(bool(accepted))

    def reconnect() -> None:
        # The same call main.py makes when the audio device changes: rebuild
        # the Gemini session, keep the conversation.
        jarvis = brain_ref.get("jarvis")
        if jarvis is not None and hasattr(jarvis, "request_reconnect"):
            jarvis.request_reconnect(keep_context=True, reason="tray")

    def quit_all() -> None:
        tray.hide()
        hud.close()
        app.quit()

    hud.command_submitted.connect(lambda text: call("on_text_command", text))
    hud.interrupt_requested.connect(lambda: call("on_interrupt"))
    hud.mic_toggled.connect(set_mic)
    hud.confirmation_answered.connect(answer)
    hud.debug_requested.connect(lambda: hud.open_settings("avance"))
    hud.quit_requested.connect(quit_all)

    def on_settings(changed: list) -> None:
        from client_desktop.config import save
        from memory import config_manager as cm
        save(settings)
        # The same machine, the same microphone: the local brain follows.
        if {"input_device", "output_device"} & set(changed):
            cm.save_input_device(settings.input_device or "")
            cm.save_output_device(settings.output_device or "")
            call("on_audio_device_change")
        if "wake_word_enabled" in changed and callable(ui.on_wake_toggle):
            result = ui.on_wake_toggle(settings.wake_word_enabled)
            store.log(f"SYS: mot d'éveil — {result}")

    def on_wake_install() -> None:
        if callable(ui.on_wake_install):
            threading.Thread(target=lambda: store.log(f"SYS: {ui.on_wake_install()}"),
                             name="wake-install", daemon=True).start()

    hud.settings_saved.connect(on_settings)
    hud.wake_install_requested.connect(on_wake_install)

    def toggle() -> None:
        hud.hide() if hud.isVisible() else (hud.show(), hud.raise_())

    tray.toggle_hud.connect(toggle)
    tray.show_hud.connect(lambda: (hud.show(), hud.raise_()))
    tray.debug_requested.connect(lambda: hud.open_settings("avance"))
    tray.mic_toggled.connect(set_mic)
    tray.reconnect_requested.connect(reconnect)
    tray.quit_requested.connect(quit_all)
    guard.listen(tray.show_hud.emit)

    pulse = QTimer()
    pulse.timeout.connect(lambda: tray.apply(store.snapshot()))
    pulse.start(200)

    # ── the brain, on its own thread, exactly as main.main() runs it ──
    def runner() -> None:
        import asyncio
        try:
            ui.wait_for_api_key()
            import main as brain           # imported, so its own launcher block does not run
            jarvis = brain.JarvisLive(ui)
            brain_ref["jarvis"] = jarvis

            def local_health_snapshot() -> dict:
                from memory.config_manager import (
                    get_gemini_key, get_input_device, get_output_device,
                )
                plugins = jarvis._plugin_registry.list_for_ui()
                return {
                    "link": store.snapshot().link.value,
                    "gemini_key_configured": bool(get_gemini_key()),
                    "microphone_setting": get_input_device() or "défaut système",
                    "speaker_setting": get_output_device() or "défaut système",
                    "actions_loaded": len(jarvis._action_registry.names()),
                    "plugins_loaded": sum(1 for p in plugins if p.get("valid")),
                    "plugins_enabled": sum(
                        1 for p in plugins if p.get("valid") and p.get("enabled")
                    ),
                    "dashboard_initialized": jarvis._dashboard is not None,
                }

            ui.health_provider = local_health_snapshot

            # Read-only health observer. JarvisLive owns all connect/reconnect
            # decisions; this thread only mirrors whether its session exists so
            # the HUD stays truthful across failures and backoff sleeps.
            watch_stop = threading.Event()

            def watch_session() -> None:
                was_connected = False
                attempts = 0
                retry_deadline = 0.0
                while not watch_stop.wait(0.5):
                    session_active = jarvis.session is not None
                    current = store.snapshot()
                    if session_active:
                        was_connected = True
                        attempts = 0
                        retry_deadline = 0.0
                        if current.link is not LinkState.CONNECTED:
                            store.set_link(LinkState.CONNECTED)
                        store.set_retry(0, 0)
                    elif was_connected:
                        was_connected = False
                        if store.snapshot().link is not LinkState.ERROR:
                            attempts += 1
                            delay = int(getattr(jarvis, "_conn_backoff", 0) or 0)
                            retry_deadline = time.monotonic() + delay
                            store.set_retry(attempts, delay)
                            store.set_link(LinkState.RECONNECTING)
                    elif (current.link is LinkState.CONNECTING
                          and int(getattr(jarvis, "_conn_backoff", 0) or 0) > 0):
                        # Also cover failures before the first session opened.
                        attempts += 1
                        delay = int(getattr(jarvis, "_conn_backoff", 0) or 0)
                        retry_deadline = time.monotonic() + delay
                        store.set_retry(attempts, delay)
                        store.set_link(LinkState.RECONNECTING)
                    elif current.link is LinkState.RECONNECTING and retry_deadline:
                        remaining = max(0, math.ceil(retry_deadline - time.monotonic()))
                        store.set_retry(attempts or current.attempt, remaining)
                    elif (current.link is LinkState.RECONNECTING
                          and current.next_retry_seconds > 0):
                        retry_deadline = time.monotonic() + current.next_retry_seconds

            watcher = threading.Thread(
                target=watch_session, daemon=True, name="jarvis-local-health"
            )
            watcher.start()
            # Hear about the end of speech (see LocalUI.speaking_finished). An
            # instance attribute, so main.py's own code is left as it is.
            original = jarvis.set_speaking

            def set_speaking(value: bool) -> None:
                original(value)
                if not value:
                    ui.speaking_finished()

            jarvis.set_speaking = set_speaking
            try:
                asyncio.run(jarvis.run())
            finally:
                watch_stop.set()
        except Exception as exc:
            store.set_link(LinkState.ERROR, f"JARVIS local s'est arrêté : {exc}")
            store.log(f"ERR: {exc!r}")

    tray.show()
    if "--mini" in argv or settings.start_collapsed:
        hud._set_mini(True)
    else:
        hud.show()
    # The microphone starts SHUT: nothing leaves this PC until you open it
    # (mic button, Ctrl+Space, or a click on the core). A microphone open by
    # default sent every word in the room to Gemini — and the room's language
    # became JARVIS's language.
    ui.muted = True
    threading.Thread(target=runner, name="jarvis-local", daemon=True).start()

    try:
        return app.exec()
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
