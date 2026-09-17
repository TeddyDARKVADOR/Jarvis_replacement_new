"""
server/alerts.py — a finished alert, run past the context policy.

THE ONE SENTENCE
    A system alert must no longer speak to the user without respecting JARVIS's
    context.

WHAT THIS IS NOT
    Not a monitor. `actions/background_monitor.py` finds the headlines, keeps
    its own "already seen" state and decides what counts as new; none of that is
    touched, read or duplicated here. This module is handed the strings it
    already produced and answers one question about each: speak it, show it, or
    hold it.

    Not a second priority system either. Every decision comes from
    `context/policy.py` — the same table the proactive check-in uses.

WHY MONITOR ALERTS ARE ALL `IMPORTANT`
    The monitor has no way to express urgency. It reports that a topic the user
    asked to follow has a new headline: worth seeing within the hour, never
    worth waking someone for. Deriving a level from the text — keyword matching
    for "urgent", "breaking" — would be exactly the second hierarchy the design
    forbids, and it would be wrong in both directions.

    So CRITICAL is never produced from here. The rule that CRITICAL pierces
    sleep is untouched and still tested in context/selftest.py; it simply has no
    producer in this path. A monitor alert cannot wake anybody, by construction.

THE TWO DEDUPLICATIONS STAY SEPARATE
    The monitor avoids raising the same alert twice (it hashes the headline).
    The notification hub avoids delivering the same notification twice (it
    carries an id the client remembers). Neither knows about the other, and
    nothing here adds a third.
"""
from __future__ import annotations

import sys

# Every alert from this source is IMPORTANT. See the module docstring.
ALERT_PRIORITY = "IMPORTANT"

_MARKER = "[MONITOR_ALERT]"


def split_monitor_alert(alert: str) -> tuple[str, str]:
    """Turn one alert string into (title, text), losing nothing the user wants.

    `actions/background_monitor.check_all` builds them like this::

        [MONITOR_ALERT] <topic>
        Headline: <title>
        <snippet>              (optional)
        Source: <source>       (optional)

    The topic is already a short, explicit label — it is what the user typed
    when they asked to follow something — so it becomes the title as-is. Every
    other line is kept verbatim in the body: the text is not rewritten, only
    split.

    The only thing dropped is the `[MONITOR_ALERT]` marker, which exists to tell
    Gemini what kind of prompt it is reading and has no meaning to a person.

    An alert in any other shape keeps its whole text and gets a generic title,
    so a change to the monitor's format degrades to "slightly worse title"
    rather than to "content silently truncated".
    """
    raw = (alert or "").strip()
    if not raw:
        return "JARVIS", ""

    lines = raw.splitlines()
    first = lines[0].strip()
    if first.startswith(_MARKER):
        topic = first[len(_MARKER):].strip()
        body = "\n".join(lines[1:]).strip()
        if topic and body:
            return topic, body
        if topic:
            return topic, topic
    return "JARVIS", raw


def _transport_ready(dashboard) -> bool:
    """True when a notification could actually reach somebody.

    `self._dashboard` is None on a desktop run until the user links a phone, and
    a notification raised with nowhere to send it would be an alert the user
    never hears about. So when there is no dashboard this module declines to
    decide at all and V1 speaks — which is the documented fallback and the only
    delivery that exists in that situation.
    """
    if dashboard is None:
        return False
    try:
        from server.notify import get_hub
    except Exception:
        return False
    get_hub().bind_dashboard(dashboard)
    return True


def _system_facts() -> dict:
    """What this process can say about itself without asking main.py.

    `sounddevice.__headless__` is set by server/audio_bridge.py's stand-in, so
    it answers "are there local speakers" from the object that would be the
    speakers — the same source the proactive hook reads, rather than a second
    flag that could disagree with it.
    """
    return {"desktop_audio": not getattr(sys.modules.get("sounddevice"),
                                         "__headless__", False)}


def route_monitor_alerts(alerts: list[str], dashboard=None) -> list[str]:
    """Decide what happens to each alert. Returns the ones that should be SPOKEN.

    The caller keeps its existing loop and simply speaks what comes back, so the
    voice path is byte-for-byte V1 for every alert the policy allows out loud.

    Never raises: this runs inside main.py's monitor task, and a failure here
    must not stop the monitor. On any problem the alerts are returned unchanged,
    which is V1 — the safe direction, because an alert spoken at a bad moment is
    an annoyance while an alert silently dropped is a broken feature.
    """
    if not isinstance(alerts, list):
        return alerts

    try:
        from context import Priority, get_policy, get_queue, get_store
    except ImportError:
        return alerts                      # context/ absent -> V1
    except Exception:
        return alerts

    if not _transport_ready(dashboard):
        return alerts                      # rien ne pourrait recevoir -> V1

    try:
        from server.notify import notify

        store, policy, queue = get_store(), get_policy(), get_queue()
        if store._system_probe is None:
            # Only if nobody bound one already: main.py's proactive hook binds a
            # richer probe (it can also report local presence) and overwriting
            # it with this poorer one would lose that.
            store.bind_system_probe(_system_facts)

        snapshot = store.snapshot()

        # What was held earlier and is now allowed through. Released as
        # notifications rather than speech on purpose: something withheld
        # overnight arriving as a spoken sentence out of nowhere is startling,
        # and the user did not ask for it at this moment.
        for held in queue.release(snapshot, policy):
            payload = held.payload
            if isinstance(payload, dict):
                notify(held.priority.value if hasattr(held.priority, "value")
                       else str(held.priority),
                       payload.get("title", "JARVIS"), payload.get("text", ""))

        speak: list[str] = []
        for alert in alerts:
            decision = policy.decide(Priority.IMPORTANT, snapshot)
            if decision.speaks:
                speak.append(alert)
                continue

            title, text = split_monitor_alert(alert)
            if decision.silent:
                # DEFER: held, never discarded. Released by the next pass of
                # this same function, i.e. on the monitor's own 30-minute
                # cadence — no second scheduler.
                queue.push({"title": title, "text": text},
                           Priority.IMPORTANT, decision.reason)
            else:
                notify(ALERT_PRIORITY, title, text)
        return speak
    except Exception:
        return alerts                      # tout incident -> V1, rien n'est perdu
