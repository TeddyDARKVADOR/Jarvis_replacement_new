"""
One JARVIS, and only one.

The brief is explicit about this — "éviter une boucle qui lance plusieurs copies
de JARVIS" — and it is not a tidiness concern. Two instances means two
microphones streaming to the same server, which is two uplinks fighting over the
same Gemini turn, and the second one silences the first server-side. It is also
exactly what a Run key plus a watchdog plus a user double-clicking the shortcut
produces, all three of which this client has.

**A named kernel mutex, not a PID file.** A PID file survives a crash and then
lies: the next launch reads a PID that now belongs to something else and refuses
to start, and the user has an assistant that will not come back until they find
a file they have never heard of. A mutex is owned by a process and released by
the kernel when that process dies, however it dies.

**And a named event, so the second launch is not simply thrown away.** Someone
who double-clicks the shortcut wants to *see* JARVIS. The second instance sets
the event and exits; the first sees it and shows its panel. That is a better
answer than a silent no-op, which reads as a broken shortcut.

**On Linux, an abstract Unix socket plays both parts.** Binding a name in the
abstract namespace succeeds for exactly one process and the kernel frees the name
when that process dies — the mutex — and the second launch connects to it and
writes "show" — the event. No file is left behind after a crash. The name carries
the user id and the graphical session, so it is per session as on Windows.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from typing import Callable

try:
    import win32api
    import win32event
    import winerror

    _AVAILABLE = True
except Exception:  # pragma: no cover - non-Windows or pywin32 absent
    _AVAILABLE = False

#: Global\ rather than Local\ would make this one instance per *machine* rather
#: than per session. Per session is right: two users fast-switched on the same
#: workstation each get their own JARVIS, their own microphone and their own
#: settings file, and neither should block the other.
MUTEX_NAME = "Local\\JarvisDesktop.SingleInstance"
EVENT_NAME = "Local\\JarvisDesktop.ShowPanel"

_UNIX = sys.platform.startswith("linux")


def _unix_name() -> str:
    """Abstract-namespace address: one per user and graphical session."""
    session = (os.environ.get("XDG_SESSION_ID") or os.environ.get("WAYLAND_DISPLAY")
               or os.environ.get("DISPLAY") or "0")
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return f"\0JarvisDesktop.SingleInstance.{uid}.{session}"


class SingleInstance:
    """Held for the life of the process. Release happens on exit, or on crash."""

    def __init__(self) -> None:
        self._mutex = None
        self._event = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.is_first = True

    def acquire(self) -> bool:
        """True if this process is the one that should run.

        Without pywin32 this returns True and says so in the caller's log: a
        missing guard is a worse failure than a duplicate instance, because it
        stops JARVIS starting at all.
        """
        if _UNIX:
            return self._acquire_unix()
        if not _AVAILABLE:
            return True
        try:
            self._mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
            self.is_first = (
                win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
            )
            self._event = win32event.CreateEvent(None, False, False, EVENT_NAME)
            return self.is_first
        except Exception:
            return True

    def signal_existing(self) -> None:
        """Ask the instance that is already running to show itself."""
        if _UNIX:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                    c.settimeout(2)
                    c.connect(_unix_name())
                    c.sendall(b"show")
            except OSError:
                pass
            return
        if not _AVAILABLE:
            return
        try:
            event = win32event.OpenEvent(
                win32event.EVENT_MODIFY_STATE, False, EVENT_NAME
            )
            win32event.SetEvent(event)
            win32api.CloseHandle(event)
        except Exception:
            pass

    def listen(self, on_show: Callable[[], None]) -> None:
        """Watch for a second launch. `on_show` runs on this helper thread, so a
        Qt caller must marshal — a queued signal does it for free."""
        if _UNIX:
            self._listen_unix(on_show)
            return
        if not _AVAILABLE or self._event is None or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                # A timeout rather than INFINITE so the thread can be asked to
                # stop; a daemon thread blocked on INFINITE keeps the process
                # alive past the last window closing on some Python builds.
                result = win32event.WaitForSingleObject(self._event, 500)
                if result == win32event.WAIT_OBJECT_0:
                    try:
                        on_show()
                    except Exception:
                        pass

        self._thread = threading.Thread(
            target=loop, name="JarvisSingleInstance", daemon=True
        )
        self._thread.start()

    # ── Linux ────────────────────────────────────────────────────────────────

    def _acquire_unix(self) -> bool:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(_unix_name())
        except OSError:
            sock.close()
            self.is_first = False
            return False
        sock.listen(4)
        sock.settimeout(0.5)
        self._mutex = sock
        self.is_first = True
        return True

    def _listen_unix(self, on_show: Callable[[], None]) -> None:
        sock = self._mutex
        if sock is None or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with conn:
                    try:
                        conn.settimeout(1)
                        if conn.recv(16).startswith(b"show"):
                            on_show()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=loop, name="JarvisSingleInstance", daemon=True)
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if isinstance(self._mutex, socket.socket):
            try:
                self._mutex.close()
            except OSError:
                pass
            self._mutex = None
            return
        for handle in (self._event, self._mutex):
            if handle is not None:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass
        self._event = None
        self._mutex = None
