"""
eval_lab/coverage.py — what has been explored, and where to look next.

FEATURES, NOT TEST COUNTS
    A scenario is reduced to a set of features: the value of each dimension
    (bucketed where the code only cares about a side of a line), each PAIR of
    those, the outcome cell it landed in (situation x priority -> channel,
    rule -> kind), the boundaries it sits on, the transitions of a story.
    Coverage is the union; the size of a run is irrelevant to it.

NOVELTY
    novelty(s) = share of its features never seen before. A generator that
    produces 100 000 near-copies scores ~0 after the first few, and the report
    says so. The coverage-guided loop (strategy G) keeps a child only if it
    brought something new, and mutates the most novel seeds first.
"""
from __future__ import annotations

import itertools
import random
from collections import Counter

from . import scenario as sc


def _bucket_idle(v):
    if v is None:
        return "none"
    return "<300" if v < 300 else "<1800" if v < 1800 else ">=1800"


def _bucket_age(v):
    if v is None:
        return "never"
    return "fresh" if v <= 300 else "stale"


def dims_of(s: dict) -> dict:
    surface, w, st = s["surface"], s.get("world") or {}, s.get("stimulus") or {}
    d = {"surface": surface}
    if surface in ("policy", "situation", "sequence"):
        phone = w.get("phone")
        d["phone"] = "none" if phone is None else _bucket_age(phone.get("age_s"))
        if phone:
            d["screen_on"] = phone.get("screen_on")
            d["idle"] = _bucket_idle(phone.get("idle_seconds"))
            d["dnd"] = phone.get("dnd")
            d["headset"] = phone.get("headset")
            d["activity"] = phone.get("activity", "UNKNOWN")
            d["car_bt"] = bool(phone.get("bluetooth_devices"))
        hour = int(((w.get("time") or {}).get("local") or sc.DEFAULT_TIME)[11:13])
        d["hour"] = "quiet" if hour >= 23 or hour < 7 else "day"
        sysd = w.get("system") or {}
        d["desktop_audio"] = sysd.get("desktop_audio", "absent")
        d["local_presence"] = "absent" if "local_presence_s" not in sysd else _bucket_idle(sysd["local_presence_s"])
        d["delivered"] = ",".join(sorted(h["priority"] for h in w.get("delivered") or [])) or "none"
        d["wake"] = (w.get("policy") or {}).get("wake_for_critical", True)
        d["forced"] = bool(w.get("force"))
        if surface == "policy":
            d["priority"] = st.get("priority")
    elif surface == "routing":
        for dev in w.get("devices") or []:
            state = "offline" if not dev.get("online", True) else \
                "stale" if dev.get("last_seen_ago_s", 0) > 90 else "online"
            d[f"dev.{dev.get('type')}"] = state
        d["n_devices"] = len(w.get("devices") or [])
        origin = (w.get("turn") or {}).get("origin")
        types = {x["id"]: x.get("type") for x in w.get("devices") or []}
        d["origin"] = types.get(origin, "none")
        d["capability"] = st.get("capability") or "none"
        d["model_hint"] = bool(st.get("model_hint"))
    elif surface == "face":
        d.update({k: st.get(k) for k in ("intent", "state", "speech", "gaze", "urgent", "interrupted")})
    return d


def features(s: dict, rec: dict | None = None) -> set:
    d = dims_of(s)
    f = {("dim", k, str(v)) for k, v in d.items()}
    items = sorted(d.items())
    f |= {("pair", a, str(va), b, str(vb)) for (a, va), (b, vb) in itertools.combinations(items, 2)}
    fam = s.get("family", "")
    if fam.startswith("boundary."):
        f.add(("boundary", fam, str((s["lineage"].get("generator") or {}).get("offset"))))
    t = (rec or {}).get("trace") or {}
    if s["surface"] == "policy" and t:
        f.add(("cell", t.get("situation"), t.get("priority"), t.get("channel")))
        f.add(("reason_kind", t.get("route"), "cooldown" in (t.get("reason") or "")))
    elif s["surface"] == "situation" and t:
        f.add(("cell", t.get("situation"), t.get("route")))
    elif s["surface"] == "routing" and t:
        f.add(("rule", t.get("rule"), t.get("kind")))
    elif s["surface"] == "sequence" and t:
        steps = t.get("steps") or []
        ops = [x.get("op") for x in steps]
        f |= {("op2", a, b) for a, b in zip(ops, ops[1:])}
        sits = [x.get("situation") for x in steps if x.get("situation")]
        f |= {("transition", a, b) for a, b in zip(sits, sits[1:]) if a != b}
        f |= {("seq_channel", x.get("priority"), x.get("channel")) for x in steps if x.get("op") == "decide"}
        if any(x.get("lost") for x in steps):
            f.add(("event", "lost"))
    elif s["surface"] == "face" and t:
        f |= {("violation", v["kind"]) for v in t.get("violations") or []}
        f.add(("cell", t.get("expression"), t.get("gesture_played")))
    if rec:
        f.add(("verdict", s["surface"], rec.get("verdict")))
        for p in rec.get("problems") or []:
            f.add(("problem", p.get("property") or p.get("path")))
    return f


class Coverage:
    def __init__(self):
        self.seen: Counter = Counter()
        self.ids: set = set()
        self.total = 0
        self.duplicates = 0
        self.invalid = 0
        self.novelty_sum = 0.0

    def add(self, s: dict, rec: dict | None = None) -> float:
        """Record one scenario; return its novelty in [0, 1]."""
        self.total += 1
        if s["id"] in self.ids:
            self.duplicates += 1
            return 0.0
        self.ids.add(s["id"])
        if rec and rec.get("verdict") == "INVALID":
            self.invalid += 1
        f = features(s, rec)
        new = sum(1 for x in f if x not in self.seen)
        self.seen.update(f)
        novelty = new / len(f) if f else 0.0
        self.novelty_sum += novelty
        return novelty

    def metrics(self) -> dict:
        kinds = Counter(k[0] for k in self.seen)
        return {
            "scenarios": self.total,
            "unique": len(self.ids),
            "duplicate_rate": round(self.duplicates / self.total, 4) if self.total else 0.0,
            "invalid_rate": round(self.invalid / max(1, len(self.ids)), 4),
            "mean_novelty": round(self.novelty_sum / max(1, len(self.ids)), 4),
            "features": dict(kinds),
            "rare_features": sum(1 for k, n in self.seen.items() if n == 1),
        }

    def policy_cells(self) -> tuple[int, int]:
        """(situation x priority) cells reached through derivation, out of 24."""
        cells = {(k[1], k[2]) for k in self.seen if k[0] == "cell" and len(k) == 4}
        return len(cells), 24


# ── G. coverage-guided fuzzing ───────────────────────────────────────────────

def fuzz(seeds: list[dict], count: int, seed: int, sink, *, max_pool: int = 5000) -> Coverage:
    """Mutate the most novel scenarios, keep the children that add coverage.

    `sink(scenario, record)` receives every executed child (write it, count
    it). Parents' traces are kept so each child's relations can be checked.
    """
    from .mutate import check_relations, mutate
    from .runner import run_one, _run_face, verdict_of

    rng = random.Random(seed)
    cov = Coverage()
    pool: list[tuple[float, dict, dict]] = []      # (energy, scenario, trace)
    for s in seeds:
        rec = _run_face([s])[0] if s["surface"] == "face" else run_one(s)
        nov = cov.add(s, rec)
        pool.append((0.1 + nov, s, rec.get("trace")))
    produced = 0
    while produced < count and pool:
        weights = [e for e, _, _ in pool]
        idx = rng.choices(range(len(pool)), weights=weights)[0]
        energy, parent, ptrace = pool[idx]
        child = mutate(parent, rng)
        pool[idx] = (max(0.05, energy * 0.9), parent, ptrace)   # a used seed cools down
        if child is None:
            continue
        rec = _run_face([child])[0] if child["surface"] == "face" else run_one(child)
        rels = check_relations(ptrace, rec.get("trace"), child)
        if rels and rec["verdict"] in ("PASS", "FAIL"):
            rec["problems"] = (rec.get("problems") or []) + rels
            rec["verdict"] = verdict_of(rec["problems"])
        nov = cov.add(child, rec)
        produced += 1
        sink(child, rec)
        if nov > 0 and rec["verdict"] != "INVALID" and len(pool) < max_pool:
            pool.append((0.1 + nov, child, rec.get("trace")))
    return cov

