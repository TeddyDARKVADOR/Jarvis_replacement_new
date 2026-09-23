"""
server/notify.py — the transport for a decision that is not speech.

WHAT WAS MISSING
    context/policy.py already classifies a message and can answer NOTIFY or
    NOTIFY_SILENT. Nothing delivered that answer, so main.py's proactive hook
    holds anything that is not speech in context.DeferralQueue. This module is
    the missing half: it turns a NOTIFY decision into an event on the socket the
    phone is already listening to.

WHY NO NEW SOCKET AND NO NEW PROTOCOL
    `dashboard.broadcast()` already fans out typed JSON to every connected
    client, and the Android client already ignores a `type` it does not know
    without closing the socket (PROTOCOL.md §4). So a notification is one more
    event on /ws — not a channel, not a server, not a second authority.

THE PROBLEM THIS MODULE ACTUALLY SOLVES: DUPLICATES
    `broadcast()` appends every message to the dashboard's `_history`, and a
    client that connects is replayed the last 50. That is exactly right for a
    transcript and exactly wrong for a notification: a phone that reconnects
    five times on a train would show the same alert five times.

    Three things together make that safe, and none of them alone is enough:

      1. Every notification carries a stable `id`.
      2. The client remembers the ids it has already shown, across restarts,
         and drops a repeat (see JarvisNotificationManager.kt).
      3. This hub refuses to replay anything older than `ttl_s`, so a phone
         that was off for a day does not wake up to yesterday.

    The client is the authority on "already shown", because it is the only one
    that knows. The server never learns whether a notification was displayed —
    there is no ack in this protocol and this module does not invent one.

WHY THE PENDING LIST IS NOT A GENERAL QUEUE
    A notification produced while no phone is connected would otherwise be lost:
    `broadcast()` fans out to whoever is there and keeps no obligation. So this
    keeps its own short, bounded, expiring list and re-offers it when a client
    connects. That is all. It does not retry, it does not order, it does not
    persist across a restart — a notification worth surviving a server restart
    is a reminder, and reminders already have a home in actions/reminder.py.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass

EVENT_TYPE = "notification"

# Mirrors context.Priority. Deliberately NOT imported from it: context/ is an
# optional package and deleting it must not take notifications with it. A check
# in server/selftest.py fails if the two ever drift apart.
PRIORITIES = ("CRITICAL", "IMPORTANT", "USEFUL", "TRIVIAL")

# TRIVIAL is "n'aurait pas du remonter" — the policy table already maps it to
# DROP everywhere. Delivering it would contradict the table, so the hub refuses
# it at the door and counts it rather than letting the phone decide.
_NEVER_DELIVERED = ("TRIVIAL",)

_MAX_TITLE = 120
_MAX_TEXT = 600


@dataclass
class Notification:
    id: str
    priority: str
    title: str
    text: str
    created_at: float

    def to_event(self) -> dict:
        """The wire form. `ts` is what lets a client judge freshness for itself
        rather than trusting that anything it receives is new."""
        return {
            "type": EVENT_TYPE,
            "id": self.id,
            "priority": self.priority,
            "title": self.title,
            "text": self.text,
            "ts": round(self.created_at, 3),
        }

    def age_s(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.created_at)


class NotificationHub:
    """Create, deliver and re-offer notifications. Thread-safe by construction:
    every mutation happens in one synchronous call and the broadcast is handed
    to the event loop rather than performed here."""

    def __init__(self, ttl_s: float = 21_600.0, maxlen: int = 50):
        self._recent: deque[Notification] = deque(maxlen=int(maxlen))
        self._ttl_s = float(ttl_s)
        self._dashboard = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._counts = {"created": 0, "delivered": 0, "refused": 0, "replayed": 0}
        # Ids of notifications that actually reached a connected client — the
        # ONE place a notification delivery is confirmed, whatever produced it
        # (alert, released queue item, /api/notify). See _reached_user.
        self._reached: set[str] = set()

    # ── wiring ───────────────────────────────────────────────────────────────

    def bind_dashboard(self, dashboard) -> None:
        """Called once by run_headless. Before this, notify() still records and
        returns — it simply has nowhere to send, which is the same situation as
        having no client connected and is handled the same way."""
        self._dashboard = dashboard
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ── production ───────────────────────────────────────────────────────────

    def notify(self, priority: str, title: str, text: str) -> Notification | None:
        """Classify, record, send. Returns None when nothing was delivered.

        Never raises: a producer calling this is doing so from a background
        task, and a notification failing to send must not take that task down.
        """
        level = str(priority or "").strip().upper()
        if level not in PRIORITIES:
            level = "USEFUL"
        if level in _NEVER_DELIVERED:
            self._counts["refused"] += 1
            return None

        clean_title = str(title or "JARVIS").strip()[:_MAX_TITLE] or "JARVIS"
        clean_text = str(text or "").strip()[:_MAX_TEXT]
        if not clean_text:
            # A notification with no body is a buzz with no information.
            self._counts["refused"] += 1
            return None

        note = Notification(
            id=uuid.uuid4().hex[:16],
            priority=level,
            title=clean_title,
            text=clean_text,
            created_at=time.time(),
        )
        self._recent.append(note)
        self._counts["created"] += 1
        if self._emit(note.to_event()):
            self._counts["delivered"] += 1
            if self._has_client():
                self._reached_user(note)
        return note

    # ── redelivery ───────────────────────────────────────────────────────────

    def pending(self, now: float | None = None) -> list[dict]:
        """Everything recent enough to still be worth showing.

        Offered to a client the moment it connects. The client drops what it has
        already seen, so this is safe to send in full and there is nothing to
        track per-client — which is what keeps a reconnect storm from needing
        any bookkeeping at all.
        """
        now = time.time() if now is None else now
        fresh = [n for n in self._recent if n.age_s(now) <= self._ttl_s]
        if fresh:
            self._counts["replayed"] += len(fresh)
        # Offered to a client that has just connected: for what no client had
        # received yet, THIS is the delivery.
        for n in fresh:
            self._reached_user(n)
        return [n.to_event() for n in fresh]

    def forget(self) -> None:
        self._recent.clear()
        self._reached.clear()

    def stats(self) -> dict:
        return dict(self._counts, held=len(self._recent))

    # ── delivery ─────────────────────────────────────────────────────────────

    def _has_client(self) -> bool:
        """A client is connected to /ws right now. A dashboard that does not
        expose its clients counts as none: an unconfirmed delivery is not one."""
        return bool(getattr(self._dashboard, "_clients", None))

    def _reached_user(self, note: Notification) -> None:
        """Confirm, once per notification, that it reached a user sink, and
        tell the context policy so the level's cooldown starts (decision N3).

        Optional on purpose, like everything that touches context/ from here:
        deleting context/ must not take notifications with it.
        """
        if note.id in self._reached:
            return
        self._reached.add(note.id)
        try:
            from context import Priority, get_policy
            get_policy().note_delivered(Priority(note.priority))
        except Exception:
            pass

    def _emit(self, event: dict) -> bool:
        """Hand the broadcast to the loop. Mirrors HeadlessUI._emit, on purpose:
        producers call this from background tasks and executor threads, so it
        must never assume it is on the loop."""
        dash = self._dashboard
        if dash is None:
            return False
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                return False

        def _fire():
            try:
                asyncio.ensure_future(dash.broadcast(event))
            except Exception:
                pass

        try:
            if loop.is_running():
                loop.call_soon_threadsafe(_fire)
                return True
        except RuntimeError:
            pass
        return False


# ── module-level access ──────────────────────────────────────────────────────
#
# Same reasoning as context/__init__.py: the producer and the transport sit in
# unrelated places, and threading a hub between them would mean a parameter
# through the core — the edit the V2 contract exists to avoid.

_hub: NotificationHub | None = None


def get_hub() -> NotificationHub:
    global _hub
    if _hub is None:
        _hub = NotificationHub()
    return _hub


def notify(priority: str, title: str, text: str) -> Notification | None:
    """The whole public API for a producer.

        from server.notify import notify
        notify("IMPORTANT", "Rendez-vous", "Ca commence dans 15 minutes.")
    """
    return get_hub().notify(priority, title, text)


def reset() -> None:
    """Drop the singleton — for tests."""
    global _hub
    _hub = None
