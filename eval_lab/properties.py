"""
eval_lab/properties.py — what must hold in every scenario, whoever wrote it.

An oracle says what ONE scenario expects. A property says what EVERY
scenario of a surface must satisfy, which is what lets a generated scenario
with no hand-written oracle still be judged.

EACH PROPERTY CITES ITS SOURCE
    None of these is invented by the lab. Each one restates a promise the code
    or its documentation already makes, and `source` says where. A property
    with no source in the project would be the lab's opinion, and a failure
    against it would be a disagreement, not a bug.

HARD AND SOFT
    hard  the promise is explicit. A violation makes the trial FAIL.
    soft  the code suggests it, but the project has not decided. A violation
          is recorded as a WARNING on a passing trial and goes to the report
          under "a trancher" — it is never counted as a bug by itself. Turning
          a soft property hard is a human decision, taken in this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Property:
    name: str
    surfaces: tuple[str, ...]
    strength: str            # hard | soft
    source: str
    fn: Callable[[dict, dict], str | None]


REGISTRY: dict[str, Property] = {}


def prop(name: str, surfaces: tuple[str, ...], strength: str, source: str):
    def wrap(fn):
        REGISTRY[name] = Property(name, surfaces, strength, source, fn)
        return fn
    return wrap


def _phone(s: dict) -> dict:
    return (s.get("world") or {}).get("phone") or {}


def _forced(s: dict) -> bool:
    return bool((s.get("world") or {}).get("force"))


# ── context/ : situation ─────────────────────────────────────────────────────

@prop("SLEEP_NEEDS_THREE_SIGNALS", ("situation", "policy"), "hard",
      "context/situation.py §2 — jamais l'horloge seule")
def _sleep(s, t):
    if _forced(s) or t.get("situation") != "ASLEEP":
        return None
    p = _phone(s)
    th = (s["world"].get("thresholds") or {}).get("asleep_idle_s", 1800.0)
    idle = p.get("idle_seconds")
    if not (t.get("quiet_hours") and p.get("screen_on") is False
            and idle is not None and idle >= th):
        return f"ASLEEP sans les trois signaux (heures calmes={t.get('quiet_hours')}, " \
               f"ecran={p.get('screen_on')}, inactif={idle})"
    return None


@prop("STALE_PHONE_NEVER_TRUSTED", ("situation", "policy"), "hard",
      "context/model.py DeviceState.is_stale — des faits perimes sont pires que rien")
def _stale(s, t):
    if _forced(s):
        return None
    p = _phone(s)
    age = p.get("age_s")
    ttl = (s["world"].get("thresholds") or {}).get("device_ttl_s", 300.0)
    stale = age is None or age > ttl
    if stale and t.get("situation") in ("DRIVING", "ASLEEP", "MEETING"):
        return f"situation {t['situation']} deduite d'un telephone perime (age {age})"
    if stale and t.get("route") in ("HEADSET", "PHONE"):
        return f"sortie {t['route']} deduite d'un telephone perime (age {age})"
    return None


@prop("HEADSET_DECIDES_ROUTE", ("situation", "policy"), "hard",
      "context/model.py Route — le casque decide de la route")
def _headset(s, t):
    if _forced(s):
        return None
    p = _phone(s)
    ttl = (s["world"].get("thresholds") or {}).get("device_ttl_s", 300.0)
    fresh = p.get("age_s") is not None and p["age_s"] <= ttl
    if fresh and p.get("headset") is True and t.get("route") != "HEADSET":
        return f"casque connecte, route {t.get('route')}"
    return None


# ── context/ : policy ────────────────────────────────────────────────────────

@prop("MEETING_NEVER_SPEAKS", ("policy",), "hard",
      "context/policy.py table — MEETING : jamais de voix, quelle que soit la priorite")
def _meeting(s, t):
    if t.get("situation") == "MEETING" and t.get("speaks"):
        return f"{t.get('priority')} parle en reunion ({t.get('channel')})"
    return None


@prop("TRIVIAL_NEVER_DELIVERED", ("policy",), "hard",
      "context/policy.py table + server/notify.py _NEVER_DELIVERED")
def _trivial(s, t):
    if t.get("priority") == "TRIVIAL" and t.get("channel") != "DROP":
        return f"TRIVIAL -> {t.get('channel')}"
    return None


@prop("CRITICAL_NEVER_COOLED_DOWN", ("policy",), "hard",
      "context/policy.py _COOLDOWN_S — CRITIQUE n'a pas de silence impose")
def _critical_cooldown(s, t):
    if t.get("priority") == "CRITICAL" and "silence CRITICAL" in (t.get("reason") or ""):
        return "CRITIQUE retenu par un cooldown"
    return None


@prop("NO_DELIVERY_CHANNEL_WITHOUT_ROUTE", ("policy",), "soft",
      "context/policy.py _enforce_reality — NOTIFY sans client joignable -> DEFER ; "
      "mais VOICE sans sortie -> NOTIFY s'arrete la, meme quand route=NONE")
def _no_route(s, t):
    if t.get("route") == "NONE" and t.get("channel") in ("VOICE", "INTERRUPT", "NOTIFY", "NOTIFY_SILENT"):
        return f"{t.get('channel')} alors que route=NONE (aucune sortie, aucun client ?)"
    return None


# ── sequences ────────────────────────────────────────────────────────────────

@prop("DEFERRED_NEVER_LOST", ("sequence",), "hard",
      "context/policy.py « WHY DEFER IS NOT DROP » + context/README « Plus tard != rien »")
def _lost(s, t):
    for i, step in enumerate(t.get("steps") or []):
        if step.get("lost"):
            return f"etape {i} ({step['op']}) : sorti de la file sans etre delivre : {step['lost']}"
    return None


@prop("QUEUE_BOUNDED", ("sequence",), "hard",
      "context/policy.py DeferralQueue — bornee, elle jette la plus ancienne")
def _bounded(s, t):
    cap = ((s.get("world") or {}).get("queue") or {}).get("maxlen", 50)
    for i, step in enumerate(t.get("steps") or []):
        if len(step.get("held") or []) > cap:
            return f"etape {i} : {len(step['held'])} elements en file, borne {cap}"
    return None


@prop("ALERT_BURST_RESPECTS_COOLDOWN", ("sequence",), "soft",
      "context/policy.py _COOLDOWN_S IMPORTANT=900 s ; server/alerts.py n'appelle jamais note_delivered")
def _burst(s, t):
    for i, step in enumerate(t.get("steps") or []):
        if step.get("op") == "alerts" and len(step.get("spoken") or []) > 1:
            return f"etape {i} : {len(step['spoken'])} alertes IMPORTANT parlees d'affilee"
    return None


# ── server/targeting ─────────────────────────────────────────────────────────

@prop("NAMED_TARGET_NEVER_SUBSTITUTED", ("routing",), "hard",
      "server/targeting.py — a named target is never substituted")
def _named(s, t):
    if t.get("hint") in ("pc", "android") and t.get("kind") == "device" \
            and t.get("device_type") != t["hint"]:
        return f"« {t['hint']} » demande, {t.get('device_type')} choisi"
    return None


@prop("CHOSEN_DEVICE_IS_ONLINE", ("routing",), "hard",
      "server/targeting.py regle 1 — hors ligne -> UNAVAILABLE")
def _online(s, t):
    if t.get("kind") == "device" and t.get("device") not in (t.get("online") or []):
        return f"{t.get('device')} choisi alors qu'il est hors ligne"
    return None


@prop("CHOSEN_DEVICE_HAS_CAPABILITY", ("routing",), "hard",
      "server/devices.py — a declared capability is a promise")
def _capable(s, t):
    cap = (s.get("stimulus") or {}).get("capability")
    if t.get("kind") != "device" or not cap:
        return None
    caps = {d["id"]: d.get("caps", []) for d in s["world"].get("devices") or []}
    if cap not in caps.get(t.get("device"), []):
        return f"{t.get('device')} choisi pour « {cap} » qu'il ne declare pas"
    return None


@prop("UNKNOWN_ORIGIN_NEVER_DEFAULTED", ("routing",), "hard",
      "server/targeting.py — an origin we cannot identify gets a question, never a default")
def _unknown_origin(s, t):
    stim, world = s.get("stimulus") or {}, s.get("world") or {}
    if stim.get("capability") or t.get("hint") or stim.get("model_hint"):
        return None
    origin = (world.get("turn") or {}).get("origin")
    types = {d["id"]: d.get("type", "unknown") for d in world.get("devices") or []}
    if (origin is None or types.get(origin) == "unknown") and t.get("kind") == "device":
        return f"origine {origin!r} non identifiee, et pourtant {t.get('device')} choisi"
    return None


@prop("REFUSAL_IS_EXPLAINED", ("routing",), "hard",
      "server/targeting.py Resolution — CLARIFY porte sa question, UNAVAILABLE sa raison")
def _explained(s, t):
    if t.get("kind") == "clarify" and not t.get("question"):
        return "clarification sans question"
    if t.get("kind") == "unavailable" and not t.get("detail"):
        return "refus sans raison"
    return None


# ── server/routing (FULL) ────────────────────────────────────────────────────

def _claimed(s) -> bool:
    tool = (s.get("stimulus") or {}).get("tool")
    return any(tool in d.get("caps", []) for d in (s.get("world") or {}).get("devices") or [])


@prop("TARGET_HINT_NEVER_REACHES_ACTION", ("router",), "hard",
      "server/routing.py TARGET_PARAM — consomme par le routeur, jamais transmis a l'action")
def _hint_leak(s, t):
    return "target_device transmis a l'action" if t.get("hint_leaked") else None


@prop("AT_MOST_ONE_EXECUTION", ("router",), "hard",
      "server/routing.py — le routeur decide QUEL appareil, un seul")
def _once(s, t):
    ex = t.get("executed_on") or []
    return f"execute {len(ex)} fois : {ex}" if len(ex) > 1 else None


@prop("UNCLAIMED_RUNS_LOCALLY", ("router",), "hard",
      "server/routing.py « THE RULE THAT KEEPS THIS ADDITIVE »")
def _unclaimed(s, t):
    if not _claimed(s) and t.get("executed_on") != ["local"]:
        return f"capacite revendiquee par personne, execution {t.get('executed_on')}"
    return None


@prop("CLAIMED_NEVER_FALLS_BACK_LOCALLY", ("router",), "hard",
      "server/routing.py — « Deliberately NOT a fallback to whatever else is connected »")
def _no_local_fallback(s, t):
    if _claimed(s) and "local" in (t.get("executed_on") or []):
        return "capacite revendiquee par un appareil, et pourtant executee localement"
    return None


@prop("REMOTE_TARGET_ONLINE_AND_CAPABLE", ("router",), "hard",
      "server/targeting.py regles 1-3 + server/devices.py capacites declarees")
def _remote_ok(s, t):
    tool = s["stimulus"]["tool"]
    caps = {d["id"]: d.get("caps", []) for d in s["world"].get("devices") or []}
    for dev in t.get("executed_on") or []:
        if dev == "local":
            continue
        if dev not in (t.get("online") or []):
            return f"{dev} hors ligne a execute « {tool} »"
        if tool not in caps.get(dev, []):
            return f"{dev} a execute « {tool} » qu'il ne declare pas"
    return None


@prop("NAMED_DEVICE_HONOURED", ("router",), "hard",
      "server/targeting.py — a named target is never substituted")
def _named_router(s, t):
    from server.targeting import detect_hint, _parse_model_hint
    turn = s["world"].get("turn") or {}
    hint = None
    if turn.get("origin") and turn.get("ago_s", 0) < 180:
        h = detect_hint(turn.get("text", ""))
        hint = h.value if h else None
    if hint is None and s["stimulus"].get("target_device"):
        h = detect_hint(s["stimulus"]["target_device"]) or _parse_model_hint(s["stimulus"]["target_device"])
        hint = h.value if h else None
    if hint not in ("pc", "android"):
        return None
    types = {d["id"]: d.get("type") for d in s["world"].get("devices") or []}
    wrong = [d for d in t.get("executed_on") or [] if d != "local" and types.get(d) != hint]
    return f"« {hint} » demande, execute sur {wrong}" if wrong else None


# ── avatar/ ──────────────────────────────────────────────────────────────────

@prop("FACE_INVARIANTS_I1_I7", ("face",), "hard",
      "avatar/checks/scenario_matrix.mjs — les invariants I1 a I7, sur la sortie")
def _face(s, t):
    v = t.get("violations") or []
    return "; ".join(f"{x['kind']} ({x['detail']})" for x in v) or None


# ── entry point ──────────────────────────────────────────────────────────────

def check_all(s: dict, trace: dict) -> list[dict]:
    """Hard violations as problems; soft ones as warnings (kind='warning')."""
    out = []
    for p in REGISTRY.values():
        if s.get("surface") not in p.surfaces:
            continue
        try:
            msg = p.fn(s, trace)
        except Exception as e:     # a broken property is the lab's bug
            msg = None
            out.append({"kind": "infra", "property": p.name, "got": f"{type(e).__name__}: {e}"})
        if msg:
            out.append({"kind": "property" if p.strength == "hard" else "warning",
                        "property": p.name, "got": msg, "source": p.source})
    return out
