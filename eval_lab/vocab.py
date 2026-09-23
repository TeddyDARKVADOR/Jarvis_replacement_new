"""
eval_lab/vocab.py — the code's own vocabularies, read from the code.

scenario.py mirrors the enums so it can validate without importing JARVIS.
This is the other half: the place that DOES import them, used by the selftest
to prove the mirror has not drifted, and by face validation, whose vocabulary
is too alive to copy (an intent added to presence/ must be usable at once).
"""
from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def face_intents() -> tuple[str, ...]:
    from presence.model import Intent
    return tuple(sorted(i.value for i in Intent))


def context_enums() -> dict[str, tuple[str, ...]]:
    from context.model import Activity, Channel, Priority, Ringer, Route, Situation
    return {
        "PRIORITIES": tuple(p.value for p in Priority),
        "SITUATIONS": tuple(s.value for s in Situation),
        "ROUTES": tuple(r.value for r in Route),
        "CHANNELS": tuple(c.value for c in Channel),
        "ACTIVITIES": tuple(a.value for a in Activity),
        "RINGERS": tuple(r.value for r in Ringer),
    }


def device_types() -> tuple[str, ...]:
    from server.devices import DeviceType
    return tuple(t.value for t in DeviceType)
