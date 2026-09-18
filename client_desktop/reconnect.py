"""
When to try again — the only place in this client that decides that.

A port of `client-android/.../net/ReconnectManager.kt`, coroutines swapped for
asyncio tasks. The policy is carried over unchanged, because it was written
against a real measured failure and the failure is not Android-specific.

Three categories, and they are not interchangeable:

| Situation                                        | Response                    |
|--------------------------------------------------|-----------------------------|
| ordinary close, server restart, network gone      | reconnect with backoff      |
| close 4001 — the bearer went stale                | log in again, not fatal     |
| 401 on /api/device-login — the credential is bad  | stop, and say so            |

**What is deliberately NOT carried over: a cap on the number of attempts.** A
workstation left running overnight, or one whose Tailscale tunnel is down while
the VPS is rebooted, must still be connected in the morning. So the *delay* is
capped and the attempts are not; only a refused credential ends the loop.

**`MIN_SESSION_LIFE`, the least obvious part.** A connection that dies two
seconds after opening is not a success followed by a failure; it is one failure
in two acts. Resetting the backoff on it produces a tight loop that reads like a
working reconnect in the logs and hammers the server. Only a connection that
*lived* clears the counter.

**And the guard in `_schedule_retry`.** Three sockets can report the same outage
within milliseconds of each other. Without it they would each schedule their own
attempt and the backoff would mean nothing. It deliberately does not look at the
in-flight connect task: a failed attempt calls back from inside that very task,
and treating that as "a retry is already pending" is what used to kill the loop
on Android — the client sat in RECONNECTING for ever while the server was up.
"""

from __future__ import annotations

import asyncio
import random
import socket
import time
from typing import Awaitable, Callable, Protocol

from .protocol import AuthRejected
from .state import LinkState

#: Below this, a connection did not live. See the module note.
MIN_SESSION_LIFE = 3.0

#: 1, 2, 4, 8, 15, 30 s, then 30 s for ever.
BACKOFF_STEPS = (1, 2, 4, 8, 15, 30)

#: How often, during a long countdown, to check whether the network came back.
#: Android gets a callback for this; Windows gets a cheap TCP probe. Three
#: seconds is slow enough to be free and fast enough that walking back into
#: Wi-Fi range does not cost a full thirty-second wait.
PROBE_EVERY = 3

#: Don't probe unless there is enough countdown left to be worth skipping.
PROBE_WORTH_IT_ABOVE = 4


class _Connectable(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...


def backoff_seconds(attempt: int, rand: random.Random | None = None) -> int:
    """1, 2, 4, 8, 15, 30 s, then 30 s for ever, +/-20 %.

    The jitter is not decoration: after a VPS reboot every device that was
    connected wakes on the same schedule and retries in step. Spreading them
    costs nothing and stops the server being hit by a synchronised wave.
    """
    rng = rand or random
    index = min(max(attempt - 1, 0), len(BACKOFF_STEPS) - 1)
    base = BACKOFF_STEPS[index]
    return max(1, int(base * (1.0 + rng.uniform(-0.2, 0.2))))


class ReconnectManager:
    """Owns the attempt loop. Nothing else starts a connection."""

    def __init__(
        self,
        client: _Connectable,
        on_link: Callable[[LinkState, str | None], None],
        on_retry: Callable[[int, int], None],
        on_note: Callable[[str], None] = lambda _: None,
        probe_target: Callable[[], tuple[str, int] | None] = lambda: None,
    ) -> None:
        self._client = client
        self._on_link = on_link
        self._on_retry = on_retry
        self._on_note = on_note
        self._probe_target = probe_target

        self._running = False
        self._connected = False
        self._attempts = 0
        self._connected_at = 0.0

        #: The countdown between attempts — and *only* that. Never consulted as
        #: a handle on the in-flight attempt; see the module note.
        self._retry_task: asyncio.Task[None] | None = None
        #: The attempt in flight.
        self._connect_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            # Already trying. Treat a second CONNECT as "stop waiting, try now"
            # rather than as a no-op: that button is dead exactly when the user
            # most wants it, staring at a stalled RECONNECTING.
            if not self._connected:
                self._attempts = 0
                self._connect_now("connect pressed while retrying")
            return
        self._running = True
        self._attempts = 0
        self._connect_now("start")

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        for task in (self._retry_task, self._connect_task):
            if task is not None:
                task.cancel()
        self._retry_task = None
        self._connect_task = None
        try:
            await self._client.disconnect()
        except Exception:
            pass
        self._on_retry(0, 0)
        self._on_link(LinkState.DISCONNECTED, None)

    # ── reported by the client ───────────────────────────────────────────────

    def note_connected(self) -> None:
        self._connected = True
        self._connected_at = time.monotonic()
        if self._retry_task is not None:
            self._retry_task.cancel()
            self._retry_task = None
        self._on_retry(self._attempts, 0)
        self._on_link(LinkState.CONNECTED, None)

    def note_disconnected(self, reason: str, fatal: bool = False) -> None:
        lived = (
            self._connected
            and time.monotonic() - self._connected_at >= MIN_SESSION_LIFE
        )
        self._connected = False

        if not self._running:
            return

        if fatal:
            self._on_link(LinkState.ERROR, reason)
            self._on_note(f"Stopped: {reason}")
            self._running = False
            return

        if lived:
            # A real session ended. Start the escalation from the bottom again
            # so a server restart costs one second, not thirty.
            self._attempts = 0
        self._schedule_retry(reason)

    # ── the loop ─────────────────────────────────────────────────────────────

    def _connect_now(self, why: str) -> None:
        if self._retry_task is not None:
            self._retry_task.cancel()
            self._retry_task = None
        if self._connect_task is not None:
            self._connect_task.cancel()
        self._connect_task = asyncio.ensure_future(self._attempt(why))

    async def _attempt(self, why: str) -> None:
        self._on_link(
            LinkState.CONNECTING if self._attempts == 0 else LinkState.RECONNECTING,
            None,
        )
        try:
            await self._client.connect()
            # Success is not declared here: the sockets are only *requested*.
            # note_connected() arrives when they are genuinely open.
        except asyncio.CancelledError:
            raise
        except AuthRejected as exc:
            self.note_disconnected(str(exc) or "device token refused", fatal=True)
        except Exception as exc:
            self._on_note(f"connect failed ({why}): {exc}")
            self._schedule_retry(str(exc) or type(exc).__name__)

    def _schedule_retry(self, reason: str) -> None:
        if not self._running:
            return
        if (
            self._retry_task is not None
            and not self._retry_task.done()
            and not self._connected
        ):
            # A countdown is already running. See the module note — this must
            # not look at `_connect_task`.
            return

        self._attempts += 1
        seconds = backoff_seconds(self._attempts)
        self._on_link(LinkState.RECONNECTING, reason)
        self._on_note(
            f"Reconnecting in {seconds}s (attempt {self._attempts}) — {reason}"
        )
        if self._retry_task is not None:
            self._retry_task.cancel()
        self._retry_task = asyncio.ensure_future(self._countdown(seconds))

    async def _countdown(self, seconds: int) -> None:
        left = seconds
        while left > 0:
            self._on_retry(self._attempts, left)
            await asyncio.sleep(1)
            left -= 1
            # "The network is back" — Android gets this from a system callback.
            # Without some equivalent the client sits out a thirty-second wait
            # that the returning link has just made pointless, which is the
            # whole difference between "reconnects eventually" and "is there
            # when you come back to the desk".
            if left > PROBE_WORTH_IT_ABOVE and left % PROBE_EVERY == 0:
                if await self._reachable():
                    self._on_note("Server reachable again — reconnecting now")
                    self._attempts = 0
                    break
        self._on_retry(self._attempts, 0)
        self._connect_now(f"retry #{self._attempts}")

    async def _reachable(self) -> bool:
        """A cheap TCP knock on the server's door. Never raises."""
        target = self._probe_target()
        if not target:
            return False
        host, port = target
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, _tcp_knock, host, port
            )
        except Exception:
            return False


def _tcp_knock(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
