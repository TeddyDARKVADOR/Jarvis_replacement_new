"""
context/selftest.py — proves the context layer before JARVIS is involved.

    python -m context.selftest

Runs with no Gemini key, no microphone, no phone, no config file and no
network. Same shape as server/selftest.py, deliberately: one convention for
"is this layer sound" across the project.

What it checks:

  1. The package imports nothing from the core. This is the V2 contract
     (docs/V2_CONTRACT.md, section 4) enforced by the parser rather than by
     anyone remembering it.
  2. Quiet hours wrap midnight.
  3. The situation precedence holds, including the two cases it exists for:
     driving at 3 a.m. is not sleep, and DND at 3 a.m. is not a meeting.
  4. All 24 cells of the priority x situation matrix.
  5. Impossible channels are downgraded to possible ones.
  6. A delivered level goes quiet, and the quiet is DEFER, not DROP.
  7. What was deferred while asleep comes back when awake.
  8. The store survives garbage, merges partial reports, and expires.
"""
from __future__ import annotations

import ast
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from context.model import (                                        # noqa: E402
    Activity, Channel, DeviceState, Priority, Ringer, Route, Situation,
)
from context.policy import DeferralQueue, ProactivityPolicy         # noqa: E402
from context.situation import Thresholds, derive, time_state        # noqa: E402
from context.store import ContextStore                              # noqa: E402

_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, str(detail)))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return wrap


NOW = 1_700_000_000.0


def _fresh(**kw) -> DeviceState:
    kw.setdefault("reported_at", NOW)
    return DeviceState(**kw)


# ── 1. the contract, enforced ────────────────────────────────────────────────

@check("context/ imports nothing from the core")
def _no_core_imports():
    forbidden = {"main", "ui", "dashboard", "core", "actions", "server"}
    offenders: list[str] = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in forbidden:
                    offenders.append(f"{path.name}:{node.lineno} -> {root}")
    # memory.config_manager is allowed, but only inside a function body (a lazy,
    # guarded read); a module-level import of it would make the package refuse
    # to load without a config file.
    assert not offenders, "forbidden imports: " + "; ".join(offenders)
    return "6 core packages, none imported"


@check("memory/ is imported lazily, never at module level")
def _lazy_config():
    tree = ast.parse((Path(__file__).parent / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:                      # top level only
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", "") or ""
            names = [a.name for a in node.names]
            assert not mod.startswith("memory"), "memory imported at module level"
            assert not any(n.startswith("memory") for n in names), \
                "memory imported at module level"
    return "config read is lazy and guarded"


# ── 2. time ──────────────────────────────────────────────────────────────────

@check("quiet hours wrap midnight")
def _quiet():
    th = Thresholds(quiet_start_hour=23, quiet_end_hour=7)
    cases = {23: True, 0: True, 3: True, 6: True, 7: False, 12: False, 22: False}
    for hour, expected in cases.items():
        got = time_state(datetime(2026, 1, 5, hour, 30), th).quiet_hours
        assert got is expected, f"{hour}h: attendu {expected}, obtenu {got}"
    return f"{len(cases)} heures verifiees de part et d'autre de minuit"


# ── 3. situation precedence ──────────────────────────────────────────────────

@check("no phone data -> UNKNOWN, never a guess")
def _unknown():
    snap = derive(DeviceState(), now=NOW)
    assert snap.situation is Situation.UNKNOWN, snap.situation
    snap2 = derive(_fresh(reported_at=NOW - 10_000, screen_on=True), now=NOW)
    assert snap2.situation is Situation.UNKNOWN, "stale data was trusted"
    assert "perimees" in " ".join(snap2.reasons), snap2.reasons
    return "absent et perime traites pareil"


@check("driving at 3 a.m. is DRIVING, not ASLEEP")
def _driving_beats_sleep():
    night = time_state(datetime(2026, 1, 5, 3, 0))
    dev = _fresh(screen_on=False, idle_seconds=7200,
                 activity=Activity.IN_VEHICLE, activity_confidence=95)
    snap = derive(dev, tstate=night, now=NOW)
    assert snap.situation is Situation.DRIVING, snap.situation
    return "l'observation directe l'emporte sur l'horaire"


@check("DND at 3 a.m. is ASLEEP, not MEETING")
def _sleep_beats_dnd():
    night = time_state(datetime(2026, 1, 5, 3, 0))
    dev = _fresh(screen_on=False, idle_seconds=7200, dnd=True)
    snap = derive(dev, tstate=night, now=NOW)
    assert snap.situation is Situation.ASLEEP, snap.situation
    return "un DND laisse la nuit ne requalifie pas la nuit en reunion"


@check("sleep needs three signals, not just the clock")
def _sleep_needs_corroboration():
    night = time_state(datetime(2026, 1, 5, 3, 0))
    awake = derive(_fresh(screen_on=True, idle_seconds=5), tstate=night, now=NOW)
    assert awake.situation is Situation.ACTIVE, awake.situation
    brief = derive(_fresh(screen_on=False, idle_seconds=60), tstate=night, now=NOW)
    assert brief.situation is not Situation.ASLEEP, "60 s d'inactivite = endormi ?"
    return "ecran allume ou inactivite courte pendant les heures calmes != sommeil"


@check("a car Bluetooth name stands in for activity recognition")
def _car_bluetooth():
    dev = _fresh(screen_on=False, idle_seconds=100,
                 bluetooth_devices=["Peugeot CarKit", "Mi Band"])
    assert derive(dev, now=NOW).situation is Situation.DRIVING
    dev2 = _fresh(screen_on=False, idle_seconds=100, bluetooth_devices=["Mi Band"])
    assert derive(dev2, now=NOW).situation is not Situation.DRIVING
    return "repli heuristique quand la permission d'activite manque"


@check("a car name is a whole word: a headset called Oscar or SyncBuds is not a car (REG-0002)")
def _car_whole_word():
    night = time_state(datetime(2026, 1, 5, 3, 0))
    traps = ["Oscar's AirPods", "Bose SyncBuds", "AutoFocus Cam", "Scarlett Solo"]
    for name in traps:
        dev = _fresh(screen_on=False, idle_seconds=7200, headset=True, bluetooth_devices=[name])
        got = derive(dev, tstate=night, now=NOW).situation
        assert got is Situation.ASLEEP, f"« {name} » a 3 h : {got.value}, attendu ASLEEP"
    th = Thresholds()
    th.car_bluetooth_names = th.car_bluetooth_names + ["peugeot 208"]
    for name in ["Peugeot CarKit", "Ford SYNC", "Android Auto", "My Car", "Peugeot 208 BT"]:
        dev = _fresh(screen_on=False, idle_seconds=100, bluetooth_devices=[name])
        got = derive(dev, th=th, now=NOW).situation
        assert got is Situation.DRIVING, f"« {name} » : {got.value}, attendu DRIVING"
    return f"{len(traps)} pieges dorment, 5 vraies voitures conduisent"


@check("the headset decides the route, never the priority")
def _route():
    day = time_state(datetime(2026, 1, 5, 14, 0))
    with_hs = derive(_fresh(screen_on=True, headset=True), tstate=day, now=NOW)
    without = derive(_fresh(screen_on=True, headset=False), tstate=day, now=NOW)
    assert with_hs.route is Route.HEADSET, with_hs.route
    assert without.route is Route.PHONE, without.route
    assert with_hs.situation == without.situation, "le casque a change la situation"
    return "HEADSET vs PHONE, situation inchangee"


# ── 4. the matrix ────────────────────────────────────────────────────────────

@check("all 24 cells of priority x situation")
def _matrix():
    expected = {
        Situation.DRIVING: [Channel.INTERRUPT, Channel.VOICE, Channel.DEFER, Channel.DROP],
        Situation.MEETING: [Channel.NOTIFY, Channel.NOTIFY_SILENT, Channel.DEFER, Channel.DROP],
        Situation.ASLEEP:  [Channel.INTERRUPT, Channel.DEFER, Channel.DEFER, Channel.DROP],
        Situation.ACTIVE:  [Channel.INTERRUPT, Channel.VOICE, Channel.NOTIFY_SILENT, Channel.DROP],
        Situation.IDLE:    [Channel.INTERRUPT, Channel.NOTIFY, Channel.NOTIFY_SILENT, Channel.DROP],
        Situation.UNKNOWN: [Channel.INTERRUPT, Channel.NOTIFY, Channel.DEFER, Channel.DROP],
    }
    order = [Priority.CRITICAL, Priority.IMPORTANT, Priority.USEFUL, Priority.TRIVIAL]
    checked = 0
    for situation, row in expected.items():
        for priority, want in zip(order, row):
            policy = ProactivityPolicy()          # neuf : aucun cooldown en cours
            snap = derive(_fresh(screen_on=True, headset=True), now=NOW)
            snap.situation, snap.route = situation, Route.HEADSET
            got = policy.decide(priority, snap, now=NOW).channel
            assert got is want, f"{situation.value}/{priority.value}: {got} != {want}"
            checked += 1
    return f"{checked} cellules conformes a la table"


@check("JARVIS never speaks in a meeting, at any priority")
def _meeting_silent():
    policy = ProactivityPolicy()
    for priority in Priority:
        snap = derive(_fresh(screen_on=True, dnd=True), now=NOW)
        assert snap.situation is Situation.MEETING, snap.situation
        assert not policy.decide(priority, snap, now=NOW).speaks, \
            f"{priority.value} a parle en reunion"
    return "4 priorites, aucune voix"


@check("wake_for_critical=False silences even CRITICAL while asleep")
def _wake_toggle():
    from context.policy import PolicyConfig
    night = time_state(datetime(2026, 1, 5, 3, 0))
    snap = derive(_fresh(screen_on=False, idle_seconds=7200), tstate=night, now=NOW)
    assert snap.situation is Situation.ASLEEP
    on = ProactivityPolicy().decide(Priority.CRITICAL, snap, now=NOW)
    off = ProactivityPolicy(PolicyConfig(wake_for_critical=False)) \
        .decide(Priority.CRITICAL, snap, now=NOW)
    assert on.channel is Channel.INTERRUPT, on.channel
    assert off.channel is Channel.DEFER, off.channel
    return "la seule case qui reveille est desactivable"


# ── 5. reality ───────────────────────────────────────────────────────────────

@check("no audio output -> notification instead of speech")
def _no_output():
    policy = ProactivityPolicy()
    snap = derive(_fresh(screen_on=True), now=NOW)
    snap.situation, snap.route = Situation.ACTIVE, Route.NONE
    d = policy.decide(Priority.IMPORTANT, snap, now=NOW)
    assert d.channel is Channel.NOTIFY, d.channel
    assert "sortie audio" in d.reason, d.reason
    return "VOICE degrade en NOTIFY, avec la raison"


@check("no reachable client -> deferred, not dropped")
def _no_client():
    policy = ProactivityPolicy()
    snap = derive(DeviceState(), now=NOW)          # aucun telephone
    assert snap.route is Route.NONE
    d = policy.decide(Priority.USEFUL, snap, now=NOW)
    assert d.channel is Channel.DEFER, d.channel
    return "rien n'est perdu faute de destinataire"


# ── 6. cooldown ──────────────────────────────────────────────────────────────

@check("a delivered level goes quiet, and the quiet is DEFER")
def _cooldown():
    policy = ProactivityPolicy()
    snap = derive(_fresh(screen_on=True, headset=True), now=NOW)
    first = policy.decide(Priority.IMPORTANT, snap, now=NOW)
    assert first.channel is Channel.VOICE, first.channel

    # Non delivre -> pas de cooldown : la decision seule ne consomme rien.
    again = policy.decide(Priority.IMPORTANT, snap, now=NOW + 1)
    assert again.channel is Channel.VOICE, "cooldown demarre sans livraison"

    policy.note_delivered(Priority.IMPORTANT, now=NOW)
    muted = policy.decide(Priority.IMPORTANT, snap, now=NOW + 60)
    assert muted.channel is Channel.DEFER, muted.channel
    later = policy.decide(Priority.IMPORTANT, snap, now=NOW + 1000)
    assert later.channel is Channel.VOICE, later.channel

    crit = policy.decide(Priority.CRITICAL, snap, now=NOW + 60)
    assert crit.channel is Channel.INTERRUPT, "CRITIQUE etouffe par un cooldown"
    return "silence 15 min sur IMPORTANT, CRITIQUE jamais bride"


# ── 7. deferral ──────────────────────────────────────────────────────────────

@check("what was held during the night comes back in the morning")
def _queue():
    policy, queue = ProactivityPolicy(), DeferralQueue()
    night = time_state(datetime(2026, 1, 5, 3, 0))
    asleep = derive(_fresh(screen_on=False, idle_seconds=7200), tstate=night, now=NOW)

    for label, prio in (("colis livre", Priority.USEFUL),
                        ("facture due", Priority.IMPORTANT),
                        ("pub", Priority.TRIVIAL)):
        d = policy.decide(prio, asleep, now=NOW)
        if d.channel is Channel.DEFER:
            queue.push(label, prio, d.reason, now=NOW)
    assert len(queue) == 2, f"{len(queue)} en attente, attendu 2 (TRIVIAL jete)"

    morning = time_state(datetime(2026, 1, 5, 8, 0))
    awake = derive(_fresh(screen_on=True, headset=True, reported_at=NOW + 3600),
                   tstate=morning, now=NOW + 3600)
    assert awake.situation is Situation.ACTIVE, awake.situation

    ready = queue.release(awake, ProactivityPolicy(), now=NOW + 3600)
    assert len(ready) == 2, f"{len(ready)} liberes"
    assert ready[0].priority is Priority.IMPORTANT, "le plus prioritaire n'est pas premier"
    assert len(queue) == 0, "la file n'a pas ete videe"
    return "2 messages retenus la nuit, rendus au reveil, tries"


@check("waking up into a meeting keeps the queue shut")
def _queue_still_held():
    policy, queue = ProactivityPolicy(), DeferralQueue()
    queue.push("note", Priority.USEFUL, now=NOW)
    meeting = derive(_fresh(screen_on=True, dnd=True), now=NOW)
    assert meeting.situation is Situation.MEETING
    assert queue.release(meeting, policy, now=NOW) == []
    assert len(queue) == 1, "libere alors que la situation l'interdit encore"
    return "la file rejoue la politique, elle ne suppose pas"


@check("a caller releases only what it can deliver; the rest stays held")
def _queue_deliverable():
    policy, queue = ProactivityPolicy(), DeferralQueue()
    queue.push("proactive", Priority.IMPORTANT, now=NOW)
    queue.push({"title": "colis", "text": "livre"}, Priority.IMPORTANT, now=NOW)
    awake = derive(_fresh(screen_on=True, headset=True), now=NOW)
    ready = queue.release(awake, policy, now=NOW + 60,
                          deliverable=lambda d: isinstance(d.payload, dict))
    assert [d.payload["title"] for d in ready] == ["colis"], ready
    assert [d.payload for d in queue.peek()] == ["proactive"], "l'element non delivrable a quitte la file"
    assert queue.peek()[0].queued_at == NOW, "l'element garde a ete rajeuni"
    everything = queue.release(awake, policy, now=NOW + 60)
    assert len(everything) == 1 and len(queue) == 0, "sans predicat, comportement d'origine"
    return "le marqueur reste, avec son age ; sans predicat rien ne change"


@check("expired items are dropped, not delivered late")
def _queue_expiry():
    queue = DeferralQueue(max_age_s=3600)
    queue.push("meteo d'hier", Priority.USEFUL, now=NOW)
    active = derive(_fresh(screen_on=True, headset=True, reported_at=NOW + 7200), now=NOW + 7200)
    assert queue.release(active, ProactivityPolicy(), now=NOW + 7200) == []
    return "peremption a 1 h respectee"


# ── 8. the store ─────────────────────────────────────────────────────────────

@check("the store survives whatever the phone sends")
def _store_garbage():
    store = ContextStore()
    store.update_device({"battery_percent": "not a number", "headset": "yes",
                         "screen_on": 1, "activity": "TELEPORTING",
                         "bluetooth_devices": "pas une liste",
                         "unknown_field_from_a_newer_app": {"nested": True}})
    dev = store.device()
    assert dev.battery_percent is None, dev.battery_percent
    assert dev.headset is True, dev.headset
    assert dev.screen_on is True, dev.screen_on
    assert dev.activity is Activity.UNKNOWN, dev.activity
    assert dev.bluetooth_devices == [], dev.bluetooth_devices
    for junk in (None, [], "string", 42):
        store.update_device(junk)                # ne doit jamais lever
    return "coercition champ par champ, aucune exception"


@check("partial reports merge instead of erasing")
def _store_merge():
    store = ContextStore()
    store.update_device({"headset": True, "headset_name": "WH-1000XM4"})
    store.update_device({"battery_percent": 42})
    dev = store.device()
    assert dev.headset is True, "le casque a ete efface par un rapport batterie"
    assert dev.battery_percent == 42
    return "un rapport partiel n'efface pas le reste"


@check("the store hands out copies, never its own object")
def _store_isolation():
    store = ContextStore()
    store.update_device({"battery_percent": 50})
    first = store.device()
    first.battery_percent = 1
    assert store.device().battery_percent == 50, "l'etat interne a ete mute de l'exterieur"
    return "isolation verifiee"


@check("a disconnect forgets, it does not wait for the TTL")
def _store_forget():
    store = ContextStore()
    store.update_device({"headset": True})
    assert store.snapshot().route is Route.HEADSET
    store.forget_device()
    assert store.snapshot().route is Route.NONE
    assert store.snapshot().situation is Situation.UNKNOWN
    return "oubli immediat a la deconnexion"


@check("a failing system probe is a missing fact, not a crash")
def _store_probe():
    store = ContextStore()
    store.bind_system_probe(lambda: 1 / 0)
    snap = store.snapshot()
    assert "probe_error" in snap.system.facts, snap.system.facts
    return "sonde en echec absorbee"


@check("describe() states facts and never invents one")
def _describe():
    store = ContextStore()
    empty = store.describe()
    assert "aucune donnee" in empty, empty
    assert "batterie" not in empty, "une batterie non rapportee est decrite"

    store.update_device({"headset": True, "headset_name": "WH-1000XM4",
                         "battery_percent": 14, "screen_on": True})
    text = store.describe()
    assert "casque connecte" in text and "WH-1000XM4" in text, text
    assert "14 %" in text, text
    assert "Situation deduite" in text, text
    return "le casque apparait — c'est la reponse a 'pourquoi le casque ?'"


@check("explain() answers 'pourquoi tu ne reponds pas vocalement ?'")
def _explain():
    store, policy = ContextStore(), ProactivityPolicy()
    store.update_device({"screen_on": True, "dnd": True})
    snap = store.snapshot()
    line = policy.explain(snap)
    assert "MEETING" in line and "deranger" in line, line
    return line


@check("no phone is not no user: the desktop reports its own presence")
def _local_presence():
    """The case that would otherwise switch proactivity off on any phone-less
    install — and the reason SystemState carries local_presence_s at all."""
    from context.model import SystemState

    recent = derive(DeviceState(), sys_state=SystemState(
        facts={"desktop_audio": True, "local_presence_s": 30}), now=NOW)
    assert recent.situation is Situation.ACTIVE, recent.situation
    assert recent.route is Route.DESKTOP, recent.route
    assert ProactivityPolicy().decide(Priority.IMPORTANT, recent, now=NOW).speaks,         "un check-in est muet alors que l'utilisateur vient de parler au poste"

    away = derive(DeviceState(), sys_state=SystemState(
        facts={"desktop_audio": True, "local_presence_s": 4000}), now=NOW)
    assert away.situation is Situation.IDLE, away.situation
    assert not ProactivityPolicy().decide(Priority.IMPORTANT, away, now=NOW).speaks,         "JARVIS parle dans une piece vide"

    # Un seul signal ne suffit jamais a conclure au sommeil : aucun ecran a voir.
    night = time_state(datetime(2026, 1, 5, 3, 0))
    deep = derive(DeviceState(), tstate=night, sys_state=SystemState(
        facts={"local_presence_s": 9999}), now=NOW)
    assert deep.situation is not Situation.ASLEEP, "sommeil deduit sans ecran"
    return "present -> parle, absent -> differe, jamais ASLEEP sans ecran"


@check("a phone report outranks the desktop's own presence")
def _phone_wins():
    """Both sources can be live at once. The phone sees more, so it wins."""
    from context.model import SystemState

    snap = derive(_fresh(screen_on=True, dnd=True), sys_state=SystemState(
        facts={"local_presence_s": 5}), now=NOW)
    assert snap.situation is Situation.MEETING, snap.situation
    return "le repli local ne s'applique qu'en l'absence de telephone frais"


# ── 9. the wire ──────────────────────────────────────────────────────────────

@check("the exact payload DeviceStateReporter.kt sends is understood")
def _android_payload():
    """Copied from the Kotlin collector's field names, not from the model.

    If someone renames a field on either side, this fails here rather than in
    production as a context that silently stops updating.
    """
    store = ContextStore()
    store.update_device({
        "screen_on": False, "idle_seconds": 2400,
        "battery_percent": 37, "battery_charging": False,
        "headset": True, "headset_name": "WH-1000XM4",
        "bluetooth_devices": ["WH-1000XM4"],
        "ringer": "NORMAL", "dnd": False,
        "network": "wifi",
    })
    dev = store.device()
    assert dev.battery_percent == 37, dev.battery_percent
    assert dev.headset is True and dev.headset_name == "WH-1000XM4"
    assert dev.ringer is Ringer.NORMAL, dev.ringer
    assert dev.idle_seconds == 2400.0, dev.idle_seconds
    assert dev.screen_on is False
    assert store.snapshot().route is Route.HEADSET
    return "10 champs Kotlin -> DeviceState, sans perte"


@check("a phone that omits every optional field still works")
def _android_minimal():
    """An older APK, or one whose reads all threw, sends almost nothing."""
    store = ContextStore()
    store.update_device({"screen_on": True})
    snap = store.snapshot()
    assert snap.situation is Situation.ACTIVE, snap.situation
    assert snap.route is Route.PHONE, snap.route
    d = ProactivityPolicy().decide(Priority.IMPORTANT, snap)
    assert d.channel is Channel.VOICE, d.channel
    return "un seul champ suffit a sortir de UNKNOWN"


@check("the heartbeat keeps the phone inside the TTL")
def _heartbeat_margin():
    """DeviceStateReporter.HEARTBEAT_MS must stay well under device_ttl_s.

    The two numbers live in different languages and different repositories of
    habit; this is the only place they are compared.
    """
    kotlin = Path(__file__).resolve().parent.parent / "client-android" / "app" / "src" /         "main" / "java" / "com" / "jarvis" / "device" / "DeviceStateReporter.kt"
    if not kotlin.exists():
        return "skipped (client Android absent)"
    text = kotlin.read_text(encoding="utf-8")
    import re
    m = re.search(r"HEARTBEAT_MS\s*=\s*([\d_]+)L", text)
    assert m, "HEARTBEAT_MS introuvable dans le collecteur Kotlin"
    heartbeat_s = int(m.group(1).replace("_", "")) / 1000.0
    ttl = Thresholds().device_ttl_s
    assert heartbeat_s * 2 <= ttl,         f"heartbeat {heartbeat_s} s trop proche du TTL {ttl} s"
    return f"heartbeat {int(heartbeat_s)} s vs TTL {int(ttl)} s — marge x{ttl / heartbeat_s:.1f}"


# ── report ───────────────────────────────────────────────────────────────────

def main() -> int:
    width = max(len(n) for n, _, _ in _results)
    print(f"\n  context/ selftest — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    for name, ok, detail in _results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n  {len(_results) - len(failed)}/{len(_results)} passed"
          + (f", FAILED: {', '.join(failed)}" if failed else "") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
