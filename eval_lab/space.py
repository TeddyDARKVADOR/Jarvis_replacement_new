"""
eval_lab/space.py — the dimensions a situation is made of, and where the code
draws its lines.

WHY THE VALUES ARE CHOSEN AND NOT RANDOM
    A uniform float in [0, 10000] for `idle_seconds` would spend almost all of
    its budget far from the only numbers the code compares against (300,
    1800). Each domain below is: a "don't know" value, one value well inside
    each region the code distinguishes, and the values that sit ON a line.

THRESHOLDS ARE READ FROM THE CODE
    `thresholds()` imports the real constants. If someone moves the TTL to
    240 s, the boundary generator moves with it instead of carefully testing
    300 s forever.
"""
from __future__ import annotations

EPS = 0.5          # seconds on either side of a line (float compares, not ints)


def thresholds() -> dict[str, float]:
    from context.situation import Thresholds
    from context.policy import _COOLDOWN_S, DeferralQueue
    from context.model import Priority
    from server.devices import STALE_AFTER_SECONDS
    th = Thresholds()
    return {
        "device_ttl_s": th.device_ttl_s,
        "asleep_idle_s": th.asleep_idle_s,
        "active_idle_s": th.active_idle_s,
        "vehicle_confidence": float(th.vehicle_confidence),
        "quiet_start_hour": float(th.quiet_start_hour),
        "quiet_end_hour": float(th.quiet_end_hour),
        "cooldown_important_s": _COOLDOWN_S[Priority.IMPORTANT],
        "cooldown_useful_s": _COOLDOWN_S[Priority.USEFUL],
        "queue_max_age_s": DeferralQueue()._max_age_s,
        "device_stale_s": STALE_AFTER_SECONDS,
    }


PRIORITIES = ["CRITICAL", "IMPORTANT", "USEFUL", "TRIVIAL"]

# ── context/ ─────────────────────────────────────────────────────────────────

CONTEXT_DIMS: dict[str, list] = {
    "hour":          [0, 3, 6, 7, 8, 12, 14, 18, 22, 23],
    "phone":         ["none", "fresh", "fresh", "stale"],       # fresh weighted x2
    "age_fresh":     [0, 30, 299],
    "screen_on":     [None, True, False],
    "idle_seconds":  [None, 5, 299, 301, 900, 1799, 1801, 7200],
    "dnd":           [None, False, True],
    "ringer":        ["UNKNOWN", "NORMAL", "VIBRATE", "SILENT"],
    "activity":      ["UNKNOWN", "STILL", "WALKING", "IN_VEHICLE"],
    "confidence":    [None, 50, 69, 70, 95],
    "headset":       [None, True, False],
    "bluetooth":     [[], ["Mi Band"], ["Peugeot CarKit"]],
    "desktop_audio": ["absent", True, False],
    "local_presence_s": ["absent", 10, 299, 301, 3600],
    "priority":      PRIORITIES,
    "delivered":     ["none", "IMPORTANT@60", "IMPORTANT@899", "IMPORTANT@901",
                      "USEFUL@3599", "USEFUL@3601", "CRITICAL@5"],
    "wake_for_critical": [True, True, True, False],              # rare on purpose
}

# ── server/targeting ─────────────────────────────────────────────────────────

ROUTING_TEXTS = {
    "generic":      "ouvre le navigateur",
    "pc":           "ouvre Chrome sur mon PC",
    "pc_ordi":      "lance ça sur l'ordi",
    "laptop":       "ouvre ça sur mon ordinateur portable",
    "phone":        "ouvre Chrome sur mon téléphone",
    "portable":     "mets ça sur mon portable",
    "english_phone": "open it on my phone",
    "here":         "ouvre le navigateur ici",
    "other":        "ouvre ça sur l'autre appareil",
    "upper":        "OUVRE CHROME SUR MON PC",
}

ROUTING_DIMS: dict[str, list] = {
    "pc":          ["online", "online", "offline", "stale", "absent"],
    "phone":       ["online", "online", "offline", "stale", "absent"],
    "unknown_dev": ["absent", "absent", "capable", "incapable"],
    "origin":      ["pc", "phone", "unknown_dev", None],
    "text":        list(ROUTING_TEXTS),
    "capability":  ["", "open_app", "computer_control", "phone_camera", "launch_rocket"],
    "model_hint":  ["", "", "pc", "android", "other", "n'importe quoi"],
}

PC_CAPS = ["open_app", "computer_control"]
PHONE_CAPS = ["open_app", "phone_camera"]
