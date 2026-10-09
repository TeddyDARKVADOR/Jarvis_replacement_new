"""
JARVIS on this PC, with the same HUD as JARVIS on Oracle.

```
python main.py --local          the brain here (Gemini key in config/api_keys.json)
python main.py                  the brain on Oracle, this PC as its terminal
python main.py --classic        the original window, unchanged
```

ONE INTERFACE, TWO BRAINS
    Online, the HUD renders a `client_desktop.state.JarvisStore` that the
    network worker fills from Oracle's sockets. Here the same store is filled by
    `LocalUI`, the object `main.JarvisLive` talks to instead of `ui.JarvisUI`.
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
import sys
import threading
import time
from pathlib import Path

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

    def __init__(self, store: JarvisStore) -> None:
        super().__init__(echo=True)
        self.store = store
        store.set_link(LinkState.CONNECTING)

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
        if "jarvis online" in low or "reconnected" in low:
            self.store.set_link(LinkState.CONNECTED)
        elif "api key invalid" in low or "rejected the api key" in low:
            self.store.set_link(LinkState.ERROR, "Clé Gemini refusée — corrigez config/api_keys.json")
        elif low.startswith("sys: awake"):
            # The wake word was heard: replay a rising edge so the HUD shows
            # the same acknowledgement ring it shows online.
            self.store.set_gate_open(False)
            self.store.set_gate_open(not self._muted)

    def set_state(self, state: str) -> None:
        s = str(state).upper()
        if self.store.snapshot().link is not LinkState.CONNECTED and s in _STATES:
            self.store.set_link(LinkState.CONNECTED)
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
                return
            if not told:
                told = True
                self.store.set_link(LinkState.ERROR, "Clé Gemini absente — ajoutez-la dans config/api_keys.json")
                self.store.log(f"ERR: aucune clé Gemini dans {BASE_DIR / 'config' / 'api_keys.json'}")
            time.sleep(2)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    from client_desktop.config import load
    from client_desktop.single_instance import SingleInstance
    from client_desktop.ui import avatar_scheme
    from client_desktop.hud import __main__ as hud_main
    from client_desktop.hud import scheme

    guard = SingleInstance()
    if not guard.acquire():
        guard.signal_existing()
        print("JARVIS est déjà en cours d'exécution — instance existante affichée.")
        return 0

    avatar_scheme.register()
    if not scheme.register():
        print("PyQt6-WebEngine est requis pour le HUD : pip install PyQt6-WebEngine")
        return 1
    hud_main._gpu_flags()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
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

    # ── store events → GUI thread (same marshalling as app.JarvisDesktop) ──
    class _Bridge(QObject):
        event = pyqtSignal(str, object)

    bridge = _Bridge()
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
            if not hud.isVisible() or hud.isMinimized() or hud._collapsed:
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

    brain_ref: dict = {}

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
        save(settings)
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

    tray.toggle_panel.connect(toggle)
    tray.show_panel.connect(lambda: (hud.show(), hud.raise_()))
    tray.debug_requested.connect(lambda: hud.open_settings("avance"))
    tray.mic_toggled.connect(set_mic)
    tray.reconnect_requested.connect(reconnect)
    tray.quit_requested.connect(quit_all)
    guard.listen(tray.show_panel.emit)

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
            # Hear about the end of speech (see LocalUI.speaking_finished). An
            # instance attribute, so main.py's own code is left as it is.
            original = jarvis.set_speaking

            def set_speaking(value: bool) -> None:
                original(value)
                if not value:
                    ui.speaking_finished()

            jarvis.set_speaking = set_speaking
            asyncio.run(jarvis.run())
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
