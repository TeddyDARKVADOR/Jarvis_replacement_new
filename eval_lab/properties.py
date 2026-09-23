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

HARD, SOFT, INCONCLUSIVE
    hard          the promise is explicit — in the code, its documentation, or
                  a dated decision of the project owner (DECISIONS below). A
                  violation makes the trial FAIL.
    soft          the code suggests it, the project has not decided. A WARNING
                  on a passing trial, "a trancher" in the report.
    inconclusive  the project decided NOT to decide yet: no explicit oracle
                  exists. The trial is INCONCLUSIVE — never PASS, never FAIL,
                  never a behaviour the lab imposes.
    Changing a strength is a human decision, taken in this file, dated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Decisions of the project owner that turned a suggestion into a promise, or
# explicitly refused to. Cited by the properties they govern.
DECISIONS = {
    "D2026-09-23-4": "une livraison sans canal ni sink disponible n'est jamais consideree delivree",
    "D2026-09-23-5": "augmenter la priorite ne rend jamais, a elle seule, la delivrance moins "
                     "immediate ni moins intrusive ; exceptions explicites seulement",
    "D2026-09-23-6": "cooldown des alertes IMPORTANT : INCONCLUSIVE, pas d'oracle explicite",
    "D2026-09-23-7": "un appareil hors ligne ne doit jamais etre selectionne (a verifier, "
                     "pas encore de modification du code)",
    "D2026-09-23-N1": "une cible explicitement nommee n'est jamais remplacee, ni par l'execution "
                      "locale ni par un autre appareil ; le repli V1 seulement sans cible explicite",
    "D2026-09-23-N2": "pendant les heures calmes, un contexte UNKNOWN ne leve jamais une restriction "
                      "de silence ; seul CRITICAL outrepasse les heures calmes",
    "D2026-09-23-N3": "toute remise effective a un sink utilisateur enregistre une livraison et arme "
                      "le cooldown, quelle que soit l'origine ; rien pour genere/tente/en file/echoue",
}

# Exceptions to D2026-09-23-5, each with the line of the specification that
# makes it one. Empty: no cooldown, override or setting is an exception today.
MONOTONIC_EXCEPTIONS: dict[str, str] = {}

INTRUSION = {"DROP": 0, "DEFER": 1, "NOTIFY_SILENT": 2, "NOTIFY": 3, "VOICE": 4, "INTERRUPT": 5}
PRIO_ORDER = ["TRIVIAL", "USEFUL", "IMPORTANT", "CRITICAL"]


@dataclass(frozen=True)
class Property:
    name: str
    surfaces: tuple[str, ...]
    strength: str            # hard | soft | inconclusive
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


_DELIVERING = ("VOICE", "INTERRUPT", "NOTIFY", "NOTIFY_SILENT")


@prop("NO_DELIVERY_WITHOUT_SINK", ("policy", "sequence"), "hard",
      "D2026-09-23-4 ; context/policy.py _enforce_reality : NOTIFY sans client joignable -> DEFER")
def _no_sink(s, t):
    if s["surface"] == "policy":
        rows = [t]
    else:
        rows = [st for st in t.get("steps") or [] if st.get("op") == "decide"]
    for st in rows:
        if st.get("route") == "NONE" and st.get("channel") in _DELIVERING:
            return f"{st.get('priority')} -> {st.get('channel')} alors qu'aucune sortie ni client (route NONE)"
        if st.get("delivered") and st.get("route") == "NONE":
            return "marque delivre alors qu'aucun sink n'existait"
    return None


@prop("PRIORITY_MONOTONIC", ("policy",), "hard",
      "D2026-09-23-5 ; context/policy.py table (monotone par ligne)")
def _monotonic(s, t):
    ladder = t.get("ladder") or {}
    for lo, hi in zip(PRIO_ORDER, PRIO_ORDER[1:]):
        a, b = ladder.get(lo), ladder.get(hi)
        if a is None or b is None or INTRUSION[b["channel"]] >= INTRUSION[a["channel"]]:
            continue
        why = b.get("reason", "")
        if any(k in why for k in MONOTONIC_EXCEPTIONS):
            continue
        return f"{hi} -> {b['channel']} moins intrusif que {lo} -> {a['channel']} ({why})"
    return None


@prop("STALE_CONTEXT_MUST_NOT_ESCALATE_DELIVERY_DURING_QUIET_HOURS", ("policy", "sequence"), "hard",
      "D2026-09-23-N2 ; context/situation.py : ASLEEP exige trois signaux, UNKNOWN n'en est pas l'absence")
def _stale_quiet(s, t):
    if s["surface"] == "policy":
        rows = [] if _forced(s) else [t]
    else:
        rows = [st for st in t.get("steps") or [] if st.get("op") == "decide"]
    for st in rows:
        if (st.get("quiet_hours") and st.get("situation") == "UNKNOWN"
                and st.get("priority") != "CRITICAL" and st.get("channel") not in ("DEFER", "DROP")):
            return (f"heures calmes, contexte UNKNOWN : {st.get('priority')} -> {st.get('channel')} "
                    "(seul CRITICAL peut passer)")
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


@prop("DELIVERY_ARMS_COOLDOWN", ("sequence",), "hard",
      "D2026-09-23-N3 ; context/policy.py note_delivered : « Call this only when the message actually went out »")
def _armed(s, t):
    """Every delivery reported in the trace (voice or notification, whatever
    its origin) must silence its level for the cooldown window: a later
    decision of that level inside the window, or a spoken alert, is a failure."""
    raw = ((s.get("world") or {}).get("policy") or {}).get("cooldowns") or {}
    window = {"IMPORTANT": 900.0, "USEFUL": 3600.0}
    window.update({k: float(v) for k, v in raw.items()})
    from .scenario import DEFAULT_TIME
    from .world import epoch_of
    start = epoch_of(((s.get("world") or {}).get("time") or {}).get("local") or DEFAULT_TIME)
    last = {h["priority"]: start - float(h["ago_s"]) for h in (s.get("world") or {}).get("delivered") or []}
    for i, st in enumerate(t.get("steps") or []):
        now = st.get("t_epoch")
        if now is not None and st.get("op") == "decide":
            p, w = st.get("priority"), window.get(st.get("priority"), 0.0)
            if w > 0 and p in last and now - last[p] < w and st.get("channel") not in ("DEFER", "DROP"):
                return f"etape {i} : {p} -> {st.get('channel')} {now - last[p]:.0f} s apres une livraison {p}"
        if now is not None and st.get("op") == "alerts" and st.get("spoken"):
            if "IMPORTANT" in last and now - last["IMPORTANT"] < window["IMPORTANT"]:
                return (f"etape {i} : alerte dite {now - last['IMPORTANT']:.0f} s apres une livraison "
                        "IMPORTANT")
        for dlv in st.get("deliveries") or []:
            last[dlv["priority"]] = now if now is not None else last.get(dlv["priority"], 0)
    return None


@prop("ALERT_BURST_RESPECTS_COOLDOWN", ("sequence",), "inconclusive",
      "D2026-09-23-6 ; context/policy.py _COOLDOWN_S IMPORTANT=900 s ; server/alerts.py n'appelle jamais note_delivered")
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


@prop("OFFLINE_DEVICE_MUST_NOT_BE_SELECTED", ("routing",), "hard",
      "D2026-09-23-7 ; server/targeting.py regle 1 — hors ligne -> UNAVAILABLE")
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
      "server/routing.py « THE RULE THAT KEEPS THIS ADDITIVE », restreinte par D2026-09-23-N1 : "
      "seulement quand aucune cible n'est nommee")
def _unclaimed(s, t):
    # Defined further down; the registry calls this after the module loaded.
    if not _claimed(s) and _requested(s) is None and t.get("executed_on") != ["local"]:
        return f"capacite revendiquee par personne, sans cible nommee, execution {t.get('executed_on')}"
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


def _requested(s) -> str | None:
    """The target the user or the model named for this turn, if any:
    pc / android / here / other. A turn older than 180 s names nothing
    (server/device_api.py TURN_CONTEXT_TTL). Hint detection is JARVIS's own
    detect_hint, itself covered by the legacy routing scenarios 13-14."""
    from server.targeting import _parse_model_hint, detect_hint
    turn = s["world"].get("turn") or {}
    if turn.get("origin") and turn.get("ago_s", 0) < 180:
        h = detect_hint(turn.get("text", ""))
        if h:
            return h.value
    raw = s["stimulus"].get("target_device")
    if raw:
        h = detect_hint(raw) or _parse_model_hint(raw)
        if h:
            return h.value
    return None


@prop("EXPLICIT_TARGET_NEVER_SUBSTITUTED", ("router",), "hard",
      "D2026-09-23-N1 ; server/targeting.py — a named target is never substituted")
def _explicit(s, t):
    hint = _requested(s)
    if hint is None:
        return None
    ex = t.get("executed_on") or []
    if "local" in ex:
        return f"« {hint} » demande, execute localement sur le serveur"
    devices = {d["id"]: d for d in s["world"].get("devices") or []}
    origin = (s["world"].get("turn") or {}).get("origin")
    for dev in ex:
        dtype = devices.get(dev, {}).get("type")
        if hint in ("pc", "android") and dtype != hint:
            return f"« {hint} » demande, execute sur {dev} ({dtype})"
        if hint == "here" and dev != origin:
            return f"« ici » depuis {origin}, execute sur {dev}"
        if hint == "other" and dev == origin:
            return f"« l'autre appareil » demande, execute sur l'origine {dev}"
    return None


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
            kind = {"hard": "property", "soft": "warning", "inconclusive": "inconclusive"}[p.strength]
            out.append({"kind": kind,
                        "property": p.name, "got": msg, "source": p.source})
    return out
