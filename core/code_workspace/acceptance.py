"""
acceptance.py — what the user asked for, criterion by criterion, and what proves it.

The plan carries a short list of acceptance criteria ("AC1: on peut ajouter une
dépense"). Each one says how it is verified:

    test     a test of the project whose NAME contains the id (test_ac1_…)
    web      a scripted browser check (`web_checks`, run by webcheck.py)
    manual   only a person can judge it (look, feel, sound…)

After the checks ran, `coverage()` matches the real results to the criteria.
A criterion is "passed" only when a test or a browser check that names it
really passed — never because the model says so. No matching result means
"unverified", and the summary says it.

Pure functions: the selftest feeds them real unittest / pytest output.
"""

from __future__ import annotations

import re

VERIFY = ("test", "web", "manual")
MAX_CRITERIA = 12
MAX_WEB_CHECKS = 8
MAX_STEPS = 12
STEP_ACTIONS = ("click", "fill", "press", "check", "uncheck", "select", "wait")

_UNITTEST_HEAD = re.compile(r"^(test\w*) \([\w.]+\)")
_UNITTEST_FAIL = re.compile(r"^(?:FAIL|ERROR): (test\w*) \(", re.M)
_PYTEST = re.compile(r"::(test\w+)(?:\[[^\]]*\])?\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)")


def _str(value, limit: int = 300) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _criteria(raw) -> list[dict]:
    criteria, ids = [], set()
    for c in _list(raw):
        if isinstance(c, str):
            c = {"criterion": c}
        if not isinstance(c, dict) or not _str(c.get("criterion")):
            continue
        cid = _str(c.get("id"), 12).upper().replace(" ", "")
        if not re.fullmatch(r"AC\d{1,2}", cid) or cid in ids:
            cid = next(f"AC{n}" for n in range(1, 100) if f"AC{n}" not in ids)
        ids.add(cid)
        verify = _str(c.get("verify"), 10).lower()
        criteria.append({"id": cid, "criterion": _str(c.get("criterion")),
                         "verify": verify if verify in VERIFY else "test"})
        if len(criteria) >= MAX_CRITERIA:
            break
    return criteria


def _step(s) -> dict | None:
    if not isinstance(s, dict) or _str(s.get("action"), 10).lower() not in STEP_ACTIONS:
        return None
    step = {"action": _str(s["action"], 10).lower(), "selector": _str(s.get("selector"), 200)}
    if s.get("value") is not None:
        step["value"] = _str(s.get("value"), 500)
    if s.get("key"):
        step["key"] = _str(s.get("key"), 30)
    if step["action"] == "wait":
        step["ms"] = _int(s.get("ms") or 300, 300, 0, 3000)
        return step
    return step if step["selector"] else None


def _expect(e) -> dict | None:
    if not isinstance(e, dict) or not _str(e.get("selector"), 200):
        return None
    exp: dict = {"selector": _str(e["selector"], 200)}
    for key in ("text", "value"):
        if e.get(key) is not None:
            exp[key] = _str(e[key], 200)
    if e.get("count") is not None:
        exp["count"] = _int(e["count"], 0, 0, 10_000)
    if e.get("visible") is not None:
        exp["visible"] = bool(e["visible"])
    return exp


def _web_checks(raw, ids: set[str]) -> list[dict]:
    checks = []
    for c in _list(raw):
        if not isinstance(c, dict):
            continue
        steps = [s for s in map(_step, _list(c.get("steps"))[:MAX_STEPS]) if s]
        expects = [e for e in map(_expect, _list(c.get("expect"))[:MAX_STEPS]) if e]
        if not expects:
            continue
        cid = _str(c.get("id"), 12).upper().replace(" ", "")
        checks.append({"id": cid if cid in ids else "", "name": _str(c.get("name") or cid or "parcours", 80),
                       "steps": steps, "expect": expects})
        if len(checks) >= MAX_WEB_CHECKS:
            break
    return checks


def normalize(plan: dict) -> dict:
    """Clean `acceptance` and `web_checks` in place; ids are AC1, AC2…"""
    plan["acceptance"] = _criteria(plan.get("acceptance"))
    plan["web_checks"] = _web_checks(plan.get("web_checks"), {c["id"] for c in plan["acceptance"]})
    return plan


def test_results(output: str) -> dict[str, str]:
    """{test name: passed | failed | skipped} from unittest -v or pytest -v output."""
    out: dict[str, str] = {}
    current = None
    for line in (output or "").splitlines():
        m = _UNITTEST_HEAD.match(line)
        if m:
            current = m.group(1)
        if current and " ... " in line:
            verdict = line.rsplit(" ... ", 1)[1].strip().lower()
            out[current] = ("passed" if verdict in ("ok", "expected failure") else
                            "skipped" if verdict.startswith("skipped") else "failed")
            current = None
    for name in _UNITTEST_FAIL.findall(output or ""):
        out[name] = "failed"
    for name, verdict in _PYTEST.findall(output or ""):
        out[name] = ("passed" if verdict in ("PASSED", "XFAIL") else
                     "skipped" if verdict == "SKIPPED" else "failed")
    return out


def _names(cid: str, results: dict[str, str]) -> list[str]:
    pat = re.compile(rf"(?:^|_){re.escape(cid.lower())}(?:_|$)")
    return [n for n in results if pat.search(n.lower())]


def coverage(criteria: list[dict], tests: dict[str, str], web: dict[str, dict] | None = None) -> list[dict]:
    """One line per criterion: passed / failed / unverified / manual, with its evidence."""
    web = web or {}
    out = []
    for c in criteria:
        row = {"id": c["id"], "criterion": c["criterion"], "verify": c["verify"],
               "status": "unverified", "evidence": ""}
        names = _names(c["id"], tests)
        w = web.get(c["id"])
        if names:
            states = [tests[n] for n in names]
            if "failed" in states:
                row["status"] = "failed"
            elif "passed" in states:
                row["status"] = "passed"
            row["evidence"] = ", ".join(f"{n} ({tests[n]})" for n in names[:4])
        if w is not None:
            if not w.get("ok"):
                row["status"] = "failed"
            elif row["status"] != "failed":
                row["status"] = "passed"
            row["evidence"] = "; ".join(x for x in (row["evidence"],
                                                    f"navigateur : {w.get('detail') or ('OK' if w.get('ok') else 'échec')}")
                                        if x)
        if row["status"] == "unverified":
            if c["verify"] == "manual":
                row["status"] = "manual"
                row["evidence"] = "à vérifier par vous"
            elif not row["evidence"]:
                row["evidence"] = ("aucun parcours navigateur ne le vérifie" if c["verify"] == "web"
                                   else f"aucun test nommé test_{c['id'].lower()}_… n'a tourné")
        out.append(row)
    return out
