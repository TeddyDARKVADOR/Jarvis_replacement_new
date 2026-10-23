"""
cognition/usage.py — ce que Matthew a coûté et réussi, semaine par semaine.

Lu par l'onglet USAGE du panneau ESPRIT (cognition.usage). Rien n'est mesuré
ici : tout est relu dans ce qui est déjà enregistré.

    memory/token_usage.jsonl   chaque réponse de Gemini (core/token_usage.py)
    cognition.db tool_outcomes chaque appel d'outil, réussi ou non (selfmodel)
    memory/reminders.json      chaque rappel et sa livraison (reminders/)

La question à laquelle ça répond d'abord : les économies tiennent-elles ? Le
contexte moyen par appel Live (ce que chaque tour refacture) est comparé
d'une semaine sur l'autre.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
USAGE_PATH = BASE / "memory" / "token_usage.jsonl"
REMINDERS_PATH = BASE / "memory" / "reminders.json"
LIVE_SOURCE = "live_voice"


def _price(model: str, u: dict) -> float | None:
    try:
        from core import token_usage
    except Exception:
        return None
    return token_usage._paid_estimate(model, u.get("input_modalities") or {}, u.get("output_modalities") or {},
                          int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                          int(u.get("cached_input_tokens") or 0))


def _events(path: Path, since: datetime):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["timestamp"])
            except Exception:
                continue
            if ts >= since:
                yield ts, e


def report(store=None, *, days: int = 7, now: datetime | None = None,
           usage_path: Path = USAGE_PATH, reminders_path: Path = REMINDERS_PATH) -> dict:
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_start = start - timedelta(days=days)

    per_day = {(start + timedelta(days=i)).date().isoformat():
               {"calls": 0, "input": 0, "output": 0, "max_input": 0, "usd": 0.0, "sessions": set()}
               for i in range(days)}
    prev_calls = prev_input = 0
    sources: dict[str, int] = defaultdict(int)
    for ts, e in _events(usage_path, prev_start):
        u = e.get("usage") or {}
        tokens_in, tokens_out = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
        live = e.get("source") == LIVE_SOURCE
        if ts < start:
            if live:
                prev_calls += 1
                prev_input += tokens_in
            continue
        day = per_day.get(ts.date().isoformat())
        if day is None:
            continue
        sources[e.get("source") or "?"] += tokens_in + tokens_out
        day["sessions"].add(e.get("session_id"))
        usd = u.get("paid_tier_usd_estimate")
        if usd is None:
            usd = _price(e.get("model", ""), u)
        day["usd"] += usd or 0.0
        day["input"] += tokens_in
        day["output"] += tokens_out
        if live:
            day["calls"] += 1
            day["max_input"] = max(day["max_input"], tokens_in)

    live_calls = sum(d["calls"] for d in per_day.values())
    live_input = sum(d["input"] for d in per_day.values() if d["calls"])
    days_out = []
    for date, d in per_day.items():
        days_out.append({"date": date, "calls": d["calls"], "sessions": len(d["sessions"] - {None}),
                         "input": d["input"], "output": d["output"], "max_input": d["max_input"],
                         "avg_input": round(d["input"] / d["calls"]) if d["calls"] else None,
                         "usd": round(d["usd"], 4)})

    tools = []
    if store is not None:
        rows = store.query(
            "SELECT tool, count(*) n, sum(outcome) ok, max(CASE WHEN outcome = 0 THEN ts END) last_fail "
            "FROM tool_outcomes WHERE ts >= ? GROUP BY tool ORDER BY n DESC",
            (start.isoformat(timespec="seconds"),))
        tools = [{"tool": r["tool"], "calls": r["n"], "ok": r["ok"] or 0,
                  "rate": round((r["ok"] or 0) / r["n"], 3), "last_fail": r["last_fail"]} for r in rows]

    reminders = {"delivered": 0, "missed": 0, "upcoming": 0}
    try:
        data = json.loads(reminders_path.read_text(encoding="utf-8"))
        local_now = datetime.now()
        for r in data.values():
            due = datetime.fromisoformat(r["due"])
            if r.get("delivered"):
                if due.date() >= start.date():
                    reminders["delivered"] += 1
            elif due > local_now:
                reminders["upcoming"] += 1
            elif due.date() >= start.date():
                reminders["missed"] += 1
    except Exception:
        pass

    avg_now = round(live_input / live_calls) if live_calls else None
    avg_prev = round(prev_input / prev_calls) if prev_calls else None
    return {
        "days": days_out,
        "totals": {
            "live_calls": live_calls,
            "sessions": len(set().union(*(d["sessions"] for d in per_day.values())) - {None}),
            "avg_input": avg_now, "avg_input_prev": avg_prev,
            "change": (round((avg_now - avg_prev) / avg_prev, 3) if avg_now and avg_prev else None),
            "input": sum(d["input"] for d in per_day.values()),
            "output": sum(d["output"] for d in per_day.values()),
            "usd": round(sum(d["usd"] for d in per_day.values()), 4),
        },
        "sources": sorted(([k, v] for k, v in sources.items()), key=lambda kv: -kv[1]),
        "tools": tools,
        "reminders": reminders,
        "since": start.date().isoformat(),
    }
