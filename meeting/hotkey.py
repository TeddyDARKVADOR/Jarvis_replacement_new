"""
meeting/hotkey.py — Ctrl+Alt+J pendant la réunion : « aide-moi ».

Windows : RegisterHotKey, dans un fil qui a sa propre boucle de messages
(aucune dépendance). Ailleurs : pynput s'il est installé (macOS demande
l'autorisation Accessibilité ; sous Wayland, les raccourcis globaux ne sont pas
permis aux applications). Sans moyen, `start` rend False et on le dit.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_J = 0x4A
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
COMBO = "Ctrl+Alt+J"


class Hotkey:
    def __init__(self, on_press: Callable[[], None], log: Callable[[str], None] = print):
        self.on_press, self.log = on_press, log
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._listener = None
        self.active = False

    def start(self) -> bool:
        if sys.platform == "win32":
            ready = threading.Event()
            self._thread = threading.Thread(target=self._windows, args=(ready,), name="meeting-hotkey", daemon=True)
            self._thread.start()
            ready.wait(2)
            return self.active
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({"<ctrl>+<alt>+j": self._fire})
            self._listener.start()
            self.active = True
        except Exception as e:
            self.log(f"SYS: réunion — raccourci {COMBO} indisponible ici ({type(e).__name__})")
        return self.active

    def _fire(self) -> None:
        try:
            self.on_press()
        except Exception as e:
            self.log(f"SYS: réunion — aide : {e}")

    def _windows(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        self.active = bool(user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_J))
        if not self.active:
            self.log(f"SYS: réunion — {COMBO} est déjà pris par une autre application")
        ready.set()
        if not self.active:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                threading.Thread(target=self._fire, daemon=True).start()
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if sys.platform == "win32" and self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        self.active = False
