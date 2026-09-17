"""
context/policy.py — who gets interrupted, when, and through what.

WHAT THIS REPLACES
    Nothing. actions/proactive.py already decides WHEN it is reasonable to
    speak at all (silence gate + cooldown) and builds the prompt. It stays
    exactly as it is. This module answers the question that came after it:
    once there IS something to say, how much is it worth, and does the user's
    current situation allow that channel at all.

WHY A MATRIX AND NOT A CHAIN OF IFS
    The rules are a product of two small sets — four priorities by six
    situations — so writing them as a table makes all twenty-four outcomes
    visible at once, including the ones nobody thought about. A chain of ifs
    hides the combination that was never considered until it fires at 3 a.m.
    The table below is the specification; read it as the feature.

THE ONE JUDGEMENT CALL IN IT
    CRITICAL pierces sleep. "Tu dors -> silence" is right for everything that
    can wait until morning, and a priority level that could wait until morning
    is not CRITICAL — it is IMPORTANT. Keeping a level that wakes you is what
    makes the other three safe to silence. It is a setting (`wake_for_critical`)
    for anyone who disagrees.

WHY DEFER IS NOT DROP
    Silencing a message and destroying it are different promises. Everything
    withheld because of the situation goes to DeferralQueue and is handed back
    when the situation allows it — which is what turns "quiet while you sleep"
    into a briefing at breakfast instead of a message you never got.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .model import Channel, Decision, Priority, Route, Situation, Snapshot


# ── the table ────────────────────────────────────────────────────────────────
#
#                   CRITICAL          IMPORTANT            USEFUL              TRIVIAL
# DRIVING           interrompre       voix                 plus tard           rien
# MEETING           notif sonore      notif muette         plus tard           rien
# ASLEEP            interrompre       plus tard            plus tard           rien
# ACTIVE            interrompre       voix                 notif muette        rien
# IDLE              interrompre       notif sonore         notif muette        rien
# UNKNOWN           interrompre       notif sonore         plus tard           rien
#
# Lecture des lignes qui ne vont pas de soi :
#   DRIVING  mains et yeux pris : une notification muette n'est pas lisible, donc
#            UTILE attend. La voix est le seul canal reellement disponible.
#   MEETING  jamais de voix, quelle que soit la priorite. CRITIQUE passe en
#            notification sonore : assez pour etre vu, sans parler a la place
#            de l'utilisateur devant temoins.
#   IDLE     l'utilisateur est eveille mais pas la : parler dans une piece vide
#            n'informe personne, donc IMPORTANT devient une notification.
#   UNKNOWN  aucune donnee fraiche. On ne parie pas sur une presence qu'on
#            n'observe pas.

_MATRIX: dict[Situation, dict[Priority, Channel]] = {
    Situation.DRIVING: {
        Priority.CRITICAL:  Channel.INTERRUPT,
        Priority.IMPORTANT: Channel.VOICE,
        Priority.USEFUL:    Channel.DEFER,
        Priority.TRIVIAL:   Channel.DROP,
    },
    Situation.MEETING: {
        Priority.CRITICAL:  Channel.NOTIFY,
        Priority.IMPORTANT: Channel.NOTIFY_SILENT,
        Priority.USEFUL:    Channel.DEFER,
        Priority.TRIVIAL:   Channel.DROP,
    },
    Situation.ASLEEP: {
        Priority.CRITICAL:  Channel.INTERRUPT,
        Priority.IMPORTANT: Channel.DEFER,
        Priority.USEFUL:    Channel.DEFER,
        Priority.TRIVIAL:   Channel.DROP,
    },
    Situation.ACTIVE: {
        Priority.CRITICAL:  Channel.INTERRUPT,
        Priority.IMPORTANT: Channel.VOICE,
        Priority.USEFUL:    Channel.NOTIFY_SILENT,
        Priority.TRIVIAL:   Channel.DROP,
    },
    Situation.IDLE: {
        Priority.CRITICAL:  Channel.INTERRUPT,
        Priority.IMPORTANT: Channel.NOTIFY,
        Priority.USEFUL:    Channel.NOTIFY_SILENT,
        Priority.TRIVIAL:   Channel.DROP,
    },
    Situation.UNKNOWN: {
        Priority.CRITICAL:  Channel.INTERRUPT,
        Priority.IMPORTANT: Channel.NOTIFY,
        Priority.USEFUL:    Channel.DEFER,
        Priority.TRIVIAL:   Channel.DROP,
    },
}

# Combien de temps JARVIS se tait apres avoir delivre un message de ce niveau.
# CRITIQUE n'a pas de silence impose : deux urgences reelles a une minute
# d'intervalle sont deux urgences, pas du bruit.
_COOLDOWN_S: dict[Priority, float] = {
    Priority.CRITICAL:  0.0,
    Priority.IMPORTANT: 900.0,     # 15 min
    Priority.USEFUL:    3600.0,    # 1 h
    Priority.TRIVIAL:   0.0,       # jamais delivre de toute facon
}


@dataclass
class PolicyConfig:
    wake_for_critical: bool = True
    cooldowns: dict = field(default_factory=lambda: dict(_COOLDOWN_S))
    # Surcharges ponctuelles : {Situation: {Priority: Channel}}, fusionnees
    # par-dessus _MATRIX. Permet de changer une case sans copier la table.
    overrides: dict = field(default_factory=dict)


class ProactivityPolicy:
    """Stateless decision + a little delivery bookkeeping for the cooldowns.

    Thread-safety: `decide()` reads and `note_delivered()` writes one small
    dict of floats. Both are called from the asyncio loop in practice; the
    operations are single dict stores, atomic under the GIL, so no lock is
    taken for what would only ever protect a timestamp.
    """

    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()
        self._last_delivered: dict[Priority, float] = {}

    # ── decision ─────────────────────────────────────────────────────────────

    def decide(
        self,
        priority: Priority,
        snapshot: Snapshot,
        now: float | None = None,
    ) -> Decision:
        """Return what to do with a message of `priority`, given the world.

        Never raises and never blocks: an unknown priority falls back to USEFUL
        rather than letting a caller's typo escalate into an interruption.
        """
        now = time.time() if now is None else now
        if not isinstance(priority, Priority):
            try:
                priority = Priority(str(priority).upper())
            except ValueError:
                priority = Priority.USEFUL

        situation = snapshot.situation if isinstance(snapshot.situation, Situation) \
            else Situation.UNKNOWN

        table = dict(_MATRIX[situation])
        table.update(self.config.overrides.get(situation, {}))
        channel = table.get(priority, Channel.DEFER)
        why: list[str] = list(snapshot.reasons)

        # Le reveil pour CRITIQUE est desactivable : la seule case de la table
        # qui contredit "tu dors -> silence" doit pouvoir etre retiree.
        if (situation is Situation.ASLEEP and priority is Priority.CRITICAL
                and not self.config.wake_for_critical):
            channel = Channel.DEFER
            why.append("reveil pour CRITIQUE desactive")

        channel, why = self._apply_cooldown(priority, channel, now, why)
        channel, route, why = self._enforce_reality(channel, snapshot.route, why)

        return Decision(
            channel   = channel,
            route     = route,
            priority  = priority,
            situation = situation,
            reason    = " ; ".join(why),
        )

    def _apply_cooldown(
        self, priority: Priority, channel: Channel, now: float, why: list[str],
    ) -> tuple[Channel, list[str]]:
        """A recently-delivered level goes quiet, it does not go away.

        Downgrading to DEFER rather than DROP is the whole point: the message
        comes back when the cooldown lapses, so a burst of three USEFUL items
        becomes one message later instead of three now or none ever.
        """
        if channel in (Channel.DROP, Channel.DEFER):
            return channel, why
        window = float(self.config.cooldowns.get(priority, 0.0))
        if window <= 0:
            return channel, why
        last = self._last_delivered.get(priority, 0.0)
        if last and (now - last) < window:
            remaining = int(window - (now - last))
            why.append(f"silence {priority.value} encore {remaining} s")
            return Channel.DEFER, why
        return channel, why

    @staticmethod
    def _enforce_reality(
        channel: Channel, route: Route, why: list[str],
    ) -> tuple[Channel, Route, list[str]]:
        """The table says what is appropriate; this says what is possible.

        Kept apart from the table on purpose. Folding "there is no speaker" into
        the priority rules would mean re-reading the whole matrix every time the
        hardware situation changes, and would make an unplugged headset look
        like a change of policy.
        """
        if channel in (Channel.VOICE, Channel.INTERRUPT) and route is Route.NONE:
            why.append("aucune sortie audio disponible -> notification")
            return Channel.NOTIFY, route, why
        if channel in (Channel.NOTIFY, Channel.NOTIFY_SILENT) and route is Route.NONE:
            why.append("aucun client joignable -> differe")
            return Channel.DEFER, route, why
        return channel, route, why

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def note_delivered(self, priority: Priority, now: float | None = None) -> None:
        """Call this only when the message actually went out.

        Marking at decision time instead would start the cooldown on messages
        that were never delivered, and the next real one would be swallowed.
        """
        self._last_delivered[priority] = time.time() if now is None else now

    def explain(self, snapshot: Snapshot) -> str:
        """One line, in French, for "pourquoi tu ne reponds pas vocalement ?".

        Answering that question is the reason the reasons are carried around at
        all; without it the policy is a black box the user cannot argue with.
        """
        sit = snapshot.situation.value if snapshot.situation else "?"
        route = snapshot.route.value if snapshot.route else "?"
        because = " ; ".join(snapshot.reasons) or "aucun signal"
        return f"Situation {sit} (sortie {route}) parce que : {because}."


# ── deferral ─────────────────────────────────────────────────────────────────

@dataclass
class Deferred:
    payload:  object
    priority: Priority
    queued_at: float
    reason:   str = ""


class DeferralQueue:
    """Holds what the policy silenced, until the situation allows it through.

    Bounded, and it drops the OLDEST item when full. Newest-first matters here:
    the queue exists because the user was unreachable, so what accumulated
    while they were asleep is worth less the older it is, and a queue that
    dropped new arrivals would go permanently deaf after one bad night.
    """

    def __init__(self, maxlen: int = 50, max_age_s: float = 86_400.0):
        self._items: list[Deferred] = []
        self._maxlen = int(maxlen)
        self._max_age_s = float(max_age_s)

    def __len__(self) -> int:
        return len(self._items)

    def push(self, payload, priority: Priority, reason: str = "",
             now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._items.append(Deferred(payload, priority, now, reason))
        if len(self._items) > self._maxlen:
            self._items = self._items[-self._maxlen:]

    def release(self, snapshot: Snapshot, policy: ProactivityPolicy,
                now: float | None = None) -> list[Deferred]:
        """Return everything the current situation would now let through.

        Re-runs the policy on each held item rather than assuming a deferred
        message is automatically deliverable later: waking up does not make a
        message deliverable if the user woke up in a meeting.
        """
        now = time.time() if now is None else now
        ready: list[Deferred] = []
        keep:  list[Deferred] = []
        for item in self._items:
            if (now - item.queued_at) > self._max_age_s:
                continue                      # perime : personne ne veut la meteo d'hier
            decision = policy.decide(item.priority, snapshot, now=now)
            if decision.channel in (Channel.DROP, Channel.DEFER):
                keep.append(item)
            else:
                ready.append(item)
        self._items = keep
        # Le plus prioritaire d'abord, puis le plus recent.
        order = {Priority.CRITICAL: 0, Priority.IMPORTANT: 1,
                 Priority.USEFUL: 2, Priority.TRIVIAL: 3}
        ready.sort(key=lambda d: (order.get(d.priority, 9), -d.queued_at))
        return ready

    def peek(self) -> list[Deferred]:
        return list(self._items)

    def clear(self) -> None:
        self._items = []
