"""
The controller — the one place that knows about all the others.

```
   audio thread        network thread            GUI thread
   ────────────        ──────────────            ──────────
   Microphone  ──► gate? ──► NetworkWorker ──►  JarvisStore
       │                          │                  │
    WakeWord                  three sockets       snapshot
       │                          │                  │
    Gate.open()  ◄────────────────┘             panel / tray / debug
```

Three threads, and exactly two rules keep it honest:

* **Only the network layer writes state**, except for the microphone level and
  the gate, which are physically produced here. Everything the UI shows is a
  snapshot it pulled, never a value it computed.
* **Nothing touches a Qt widget off the GUI thread.** Store events arrive on
  whatever thread caused them and are re-emitted through `_Bridge`, whose
  queued connections are the marshalling.

**The microphone is not held open when it is not needed.** It runs when the wake
word is listening or the gate is open, and stops otherwise. On a work laptop the
difference between "the microphone is on whenever JARVIS is running" and "the
microphone is on when it has a reason to be" is the difference between a tool
and a thing people put tape over.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import autostart
from .audio import Microphone
from .config import Settings, load, save  # noqa: F401  (save: _on_settings_changed)
from .net import NetworkWorker
from .state import AssistantState, JarvisStore, LinkState, Snapshot
from .placement import PlacementMode, Rect
from .ui import screens
from .ui.debug import DebugWindow
from .ui.panel import JarvisPanel
from .ui.tray import JarvisTray
from .wake import Gate, WakeWord, engine_available, install
from .win_windows import foreground_window

#: How often the UI pulls a snapshot. The core repaints faster than this off its
#: own timer; this is for everything made of text.
SNAPSHOT_HZ = 10

#: How long a sent command may go unanswered before the client says so.
#:
#: Measured against this deployment, 6 turns on one connection: turns 2-6 were
#: answered in 3.6-4.7 s (mean 4.2), and **the first turn after connecting was
#: lost** — the server log shows `Phone connected` → the command → `Reconnected
#: — conversation restored`, i.e. a Gemini session rotation swallowed the turn
#: in flight. That is server behaviour, in files this client must not modify.
#:
#: So the client does the one honest thing available to it: it notices, and
#: says so. **It deliberately does not retry.** A command can be "delete the
#: downloads folder"; a silent second attempt at an irreversible action is a
#: far worse failure than a sentence the user has to repeat.
COMMAND_ANSWER_TIMEOUT = 25.0


def _close_enough(a: Rect, b: Rect, epsilon: int) -> bool:
    """True when two rectangles are the same window to within rounding.

    Physical-to-logical conversion divides by a scale factor, so a window that
    has not moved can still report a coordinate one pixel different between two
    reads. Re-applying the geometry on that makes the panel shiver against a
    window being dragged.
    """
    return (
        abs(a.x - b.x) <= epsilon
        and abs(a.y - b.y) <= epsilon
        and abs(a.w - b.w) <= epsilon
        and abs(a.h - b.h) <= epsilon
    )


class _Bridge(QObject):
    """Carries store events from whatever thread raised them onto the GUI one."""

    event = pyqtSignal(str, object)


class JarvisDesktop(QObject):
    """Everything wired together. One per process — see `single_instance`."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or load()
        self.store = JarvisStore()
        #: (text, sent_at) while a typed command is waiting for an answer.
        self._pending_command: tuple[str, float] | None = None
        #: `time.monotonic()` of the last thing JARVIS said. See _check_unanswered.
        self._last_jarvis_at = 0.0

        # ── the pieces ───────────────────────────────────────────────────────
        self.worker = NetworkWorker(self.settings, self.store)
        self.gate = Gate(
            seconds=self.settings.gate_seconds,
            on_change=self._on_gate_change,
        )
        self.wake = WakeWord(
            on_detect=self._on_wake,
            threshold=self.settings.wake_word_threshold,
            logger=self.store.log,
        )
        self.microphone = Microphone(
            on_frame=self._on_mic_frame,
            device_name=self.settings.input_device,
            on_error=self.store.log,
        )

        # ── the surfaces ─────────────────────────────────────────────────────
        self.panel = JarvisPanel(self.settings)
        self.debug = DebugWindow(self.settings)
        self.tray = JarvisTray()

        # ── following the active window ──────────────────────────────────────
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_tick)
        self._last_target_hwnd = 0
        self._target_seen_at = 0.0
        self._last_target_rect: Rect | None = None

        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_store_event)
        self.store.subscribe(lambda kind, payload: self._bridge.event.emit(kind, payload))

        self._wire()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // SNAPSHOT_HZ)

    # ── wiring ───────────────────────────────────────────────────────────────

    def _wire(self) -> None:
        self.panel.command_submitted.connect(self._send_command)
        self.panel.interrupt_requested.connect(self.worker.send_interrupt)
        self.panel.mic_toggled.connect(self._set_mic)
        self.panel.confirmation_answered.connect(self.worker.send_confirmation)
        self.panel.debug_requested.connect(self._show_debug)
        self.panel.quit_requested.connect(self.shutdown)

        self.debug.connect_requested.connect(self.worker.connect)
        self.debug.disconnect_requested.connect(self.worker.disconnect)
        self.debug.settings_changed.connect(self._on_settings_changed)
        self.debug.wake_word_install_requested.connect(self._install_wake_word)
        self.debug.autostart_toggled.connect(self._set_autostart)
        self.debug.placement_changed.connect(self._on_placement_settings_changed)
        self.debug.realign_requested.connect(self._capture_target_now)
        self.panel.placement_changed.connect(self._on_panel_dragged)

        self.tray.toggle_panel.connect(self._toggle_panel)
        self.tray.show_panel.connect(self._show_panel)
        self.tray.debug_requested.connect(self._show_debug)
        self.tray.mic_toggled.connect(self._set_mic)
        self.tray.reconnect_requested.connect(self.worker.connect)
        self.tray.quit_requested.connect(self.shutdown)

    # ── startup ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self.tray.show()
        self.panel.show()
        if self.settings.start_collapsed:
            self.panel._toggle_collapse()

        # After show(): winId() is only meaningful once the window exists, and
        # the exclusion set is what stops FOLLOW aligning the panel against
        # itself.
        self._apply_placement_mode()

        self.worker.start()
        self.debug.set_autostart_checked(autostart.is_enabled())

        if autostart.is_enabled() and not autostart.matches_current_install():
            # Silent-at-login failures are the worst kind: the user just sees
            # that JARVIS "stopped starting one day".
            self.store.log(
                "Démarrage auto : la commande enregistrée ne pointe plus ici. "
                "Ré-activez la case pour la corriger."
            )

        self.store.set_wake_word(self.wake.NAME if engine_available() else "")
        if self.settings.wake_word_enabled and engine_available():
            if self.wake.start():
                self.microphone.start()

        if self.settings.mic_open_at_start:
            self._set_mic(True)

        if not self.settings.is_configured:
            # Nothing to connect to yet. The debug window is the pairing screen.
            self.store.log("Non appairé — renseignez l'hôte et le jeton.")
            self._show_debug()
            return

        self.worker.connect()

    # ── placement ────────────────────────────────────────────────────────────

    #: How often FOLLOW re-reads the foreground window. One syscall, and the
    #: whole feature costs less than a frame of the core animation.
    FOLLOW_HZ = 4
    #: A new foreground window must hold still this long before the panel moves
    #: to it. Alt-tabbing through six windows should not drag the panel across
    #: the desktop six times.
    TARGET_SETTLE = 0.25
    #: Below this, a target that has "moved" is just rounding, and re-applying
    #: the geometry would make the panel shiver against a window being dragged.
    TARGET_MOVE_EPSILON = 8

    def _our_windows(self) -> frozenset[int]:
        """Our own handles, so the panel never aligns itself against itself.

        Without this, clicking the panel makes it the foreground window; it then
        computes a position beside its own rectangle, which moves it, which it
        then aligns against again — a slow drift into a corner that reads as the
        panel wandering off.
        """
        handles = []
        for widget in (self.panel, self.debug):
            try:
                handles.append(int(widget.winId()))
            except Exception:
                pass
        return frozenset(handles)

    def _read_target(self) -> Rect | None:
        window = foreground_window(exclude=self._our_windows())
        if window is None:
            return None
        return screens.to_logical(window)

    def _apply_placement_mode(self) -> None:
        """Start or stop following, and place the panel once, now."""
        mode = self.settings.placement.mode
        if mode is PlacementMode.FOLLOW:
            if not self._follow_timer.isActive():
                self._follow_timer.start(1000 // self.FOLLOW_HZ)
        else:
            self._follow_timer.stop()

        if mode is PlacementMode.WINDOW:
            # A one-shot alignment: the window that is in front *now*. Following
            # it continuously is a separate mode on purpose — a panel that moves
            # on every Alt-Tab is a different product from one that sits beside
            # the editor you told it about.
            self._capture_target_now()
            return
        if mode is not PlacementMode.FOLLOW:
            self.panel.set_target(None)
        self.panel.apply_placement()

    def _capture_target_now(self) -> None:
        target = self._read_target()
        if target is None:
            self.store.log("Aucune fenêtre cible utilisable — ancrage à l'écran.")
        self.panel.set_target(target)
        self.panel.apply_placement()

    def _follow_tick(self) -> None:
        window = foreground_window(exclude=self._our_windows())
        if window is None:
            # Nothing sensible in front — the desktop, the Start menu, or our
            # own panel. Keep the last position rather than snapping away: a
            # panel that jumps every time Start opens is worse than one that
            # waits.
            return

        now = time.monotonic()
        if window.hwnd != self._last_target_hwnd:
            self._last_target_hwnd = window.hwnd
            self._target_seen_at = now
            return
        if now - self._target_seen_at < self.TARGET_SETTLE:
            return

        rect = screens.to_logical(window)
        if rect is None:
            return
        previous = self._last_target_rect
        if previous is not None and _close_enough(
            previous, rect, self.TARGET_MOVE_EPSILON
        ):
            return
        self._last_target_rect = rect
        self.panel.set_target(rect)
        self.panel.apply_placement()

    def _on_placement_settings_changed(self) -> None:
        save(self.settings)
        self.panel.sync_always_on_top()
        self._apply_placement_mode()

    def _on_panel_dragged(self) -> None:
        """The panel was moved by hand — it is FREE now and that was persisted."""
        save(self.settings)
        self._follow_timer.stop()
        self.debug.refresh_placement_controls()

    # ── the audio thread ─────────────────────────────────────────────────────

    def _on_mic_frame(self, pcm: bytes, level: float) -> None:
        """Real-time thread. Three cheap things, in this order, and nothing else."""
        self.store.set_mic_level(level)
        self.wake.feed(pcm)
        if self.gate.is_open:
            self.worker.offer_frame(pcm)

    def _on_wake(self) -> None:
        """The wake-word thread. Opening the gate is all that happens here."""
        self.store.log("« Hey Jarvis » — micro ouvert.")
        self.gate.open()

    def _on_gate_change(self, open_: bool) -> None:
        """May be called from the audio, wake or GUI thread. All three targets
        below are thread-safe by construction."""
        self.store.set_gate_open(open_)
        self.worker.set_mic_streaming(open_)
        if open_:
            self.microphone.start()
        elif not (self.settings.wake_word_enabled and self.wake.running):
            # Nothing left that needs to hear the room.
            self.microphone.stop()

    # ── intent from the UI ───────────────────────────────────────────────────

    def _set_mic(self, open_: bool) -> None:
        if open_:
            self.microphone.start()
            self.gate.hold(True)
        else:
            self.gate.hold(False)

    def _send_command(self, text: str) -> None:
        self.worker.send_command(text)
        self._pending_command = (text, time.monotonic())
        # A typed command is activity: it keeps an open gate open rather than
        # letting it expire mid-conversation.
        self.gate.touch()

    def _show_panel(self) -> None:
        self.panel.show()
        self.panel.raise_()

    def _toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self._show_panel()

    def _show_debug(self) -> None:
        self.debug.show()
        self.debug.raise_()
        self.debug.activateWindow()

    def _on_settings_changed(self) -> None:
        save(self.settings)
        self.store.log("Réglages enregistrés — reconnexion.")
        self.worker.connect()

    def _set_autostart(self, enabled: bool) -> None:
        ok, detail = autostart.enable() if enabled else autostart.disable()
        self.store.log(
            f"Démarrage auto {'activé' if enabled else 'désactivé'} : {detail}"
            if ok
            else f"Démarrage auto — échec : {detail}"
        )
        self.debug.set_autostart_checked(autostart.is_enabled())

    def _install_wake_word(self) -> None:
        ok, message = install(self.store.log)
        self.store.log(message)
        if ok:
            self.store.set_wake_word(self.wake.NAME)
            if self.wake.start():
                self.microphone.start()

    # ── the pulse ────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self.gate.tick()
        self._check_unanswered()
        snapshot = self.store.snapshot()
        self.panel.apply(snapshot)
        self.tray.apply(snapshot)
        if self.debug.isVisible():
            self.debug.apply(snapshot)

    # ── notifications ────────────────────────────────────────────────────────

    def _on_store_event(self, kind: str, payload: object) -> None:
        """GUI thread, always — `_Bridge` guarantees it."""
        # Bookkeeping first, and outside the notifications setting: turning
        # notifications off must not also stop the client noticing that a
        # command went unanswered.
        if kind == "message" and getattr(payload, "from_jarvis", False):
            self._last_jarvis_at = time.monotonic()

        if not self.settings.notifications:
            return
        snapshot: Snapshot = self.store.snapshot()

        if kind == "confirm":
            # This one is raised whatever the panel is doing: the request dies
            # in ninety seconds and the user is looking at their editor.
            _, title, detail = payload  # type: ignore[misc]
            self.tray.notify(f"JARVIS — confirmation : {title}", detail or "")
            self._show_panel()

        elif kind == "message":
            message = payload  # type: ignore[assignment]
            if getattr(message, "from_jarvis", False) and not self._panel_readable():
                self.tray.notify("JARVIS", getattr(message, "text", "")[:180])

        elif kind == "link":
            if payload is LinkState.ERROR and snapshot.last_error:
                self.tray.notify("JARVIS — erreur", snapshot.last_error)

    def _check_unanswered(self) -> None:
        """Notice a command that never came back, and say so exactly once.

        The answer is whatever JARVIS says next, so any incoming line from it
        clears the wait — this is not trying to match a reply to a request,
        only to catch total silence.
        """
        if self._pending_command is None:
            return
        text, sent_at = self._pending_command

        # `_last_jarvis_at` is stamped on arrival rather than read from the
        # message's own `at`: the server dates a line with its own wall clock,
        # and comparing that against this machine's monotonic clock would be
        # comparing two unrelated numbers.
        if self._last_jarvis_at >= sent_at:
            self._pending_command = None
            return

        if time.monotonic() - sent_at < COMMAND_ANSWER_TIMEOUT:
            return

        self._pending_command = None
        self.store.log(
            f"sans réponse après {int(COMMAND_ANSWER_TIMEOUT)} s : « {text[:60]} » "
            "— rotation de session probable, renvoyez la commande."
        )
        if self.settings.notifications:
            self.tray.notify(
                "JARVIS n'a pas répondu",
                "La commande s'est perdue. Renvoyez-la si elle comptait.",
            )

    def _panel_readable(self) -> bool:
        """True when the user could actually have read the answer on screen."""
        return (
            self.panel.isVisible()
            and not self.panel.isMinimized()
            and not self.panel._collapsed
        )

    # ── shutdown ─────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        self._timer.stop()
        self._follow_timer.stop()
        self.gate.close()
        self.wake.stop()
        self.microphone.stop()
        self.worker.stop()
        # Deliberately NOT saving here.
        #
        # Settings are written when they are *changed*, by the Save button in
        # the debug window, which is explicit and user-initiated. Writing again
        # on the way out persists whatever happens to be in memory over a file
        # the user configured — and that is not hypothetical: a UI test that
        # constructed `Settings(host="", device_token="")` and shut down
        # cleanly wiped a working pairing, and the next launch came up
        # unpaired with nothing to explain why.
        self.tray.hide()
        self.panel.close()
        self.debug.close()
        from PyQt6.QtWidgets import QApplication

        QApplication.quit()
