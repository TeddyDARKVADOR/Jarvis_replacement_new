"""
diagnose.py — read an error the way a developer does.

    locate()      the project file and line a traceback / test failure points at
    classify()    syntax, import, dependency, assertion, runtime, timeout
    signature()   a stable fingerprint of "the same error", so the fix loop
                  notices it is going round in circles and asks for help
    excerpt()     the part of a long output worth showing the fixer
"""

from __future__ import annotations

import hashlib
import re

_PY_FRAME = re.compile(r'File ["\']([^"\']+)["\'],\s+line\s+(\d+)')
_JS_FRAME = re.compile(r'([\w./\\-]+\.(?:js|mjs|cjs|ts)):(\d+)(?::\d+)?')
_ERR_LINE = re.compile(r"^\s*(\w+(?:Error|Exception|Exit|Interrupt)|FAILED|ERROR|AssertionError)\b.*$", re.M)


def locate(output: str, project_files: list[str]) -> tuple[str | None, int | None]:
    """The deepest frame that belongs to the project (not the stdlib)."""
    names = {f.replace("\\", "/"): f for f in project_files}
    frames = [(m.group(1), int(m.group(2))) for m in _PY_FRAME.finditer(output or "")]
    frames += [(m.group(1), int(m.group(2))) for m in _JS_FRAME.finditer(output or "")]
    for raw, line in reversed(frames):
        r = raw.replace("\\", "/")
        for rel in names:
            if r == rel or r.endswith("/" + rel):
                return names[rel], line
    return None, None


def classify(output: str, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    low = (output or "").lower()
    if "modulenotfounderror" in low or "no module named" in low or "cannot find module" in low:
        return "dependency"
    if "syntaxerror" in low or "indentationerror" in low or "taberror" in low:
        return "syntax"
    if "importerror" in low or "cannot import name" in low:
        return "import"
    if "assertionerror" in low or re.search(r"\bfailed\b", low):
        return "test_failure"
    if "traceback" in low or "error" in low:
        return "runtime"
    return "unknown"


def missing_module(output: str) -> str | None:
    m = re.search(r"No module named ['\"]([\w.]+)['\"]", output or "")
    return m.group(1).split(".")[0] if m else None


def _normalise(output: str) -> str:
    text = re.sub(r"0x[0-9a-fA-F]+", "0x", output or "")
    text = re.sub(r"\d+(\.\d+)?\s*s\b", "Ns", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "<path>", text)
    return text


def signature(output: str) -> str:
    """Same error type + same message + same place → same signature."""
    lines = [m.group(0).strip() for m in _ERR_LINE.finditer(_normalise(output))]
    frames = _PY_FRAME.findall(output or "")[-2:]
    key = "|".join(lines[-4:]) + "|" + "|".join(f"{f.split('/')[-1].split(chr(92))[-1]}:{n}" for f, n in frames)
    if not key.strip("|"):
        key = _normalise(output)[-400:]
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:12]


def excerpt(output: str, limit: int = 3500) -> str:
    text = output or ""
    if len(text) <= limit:
        return text
    head = text[: limit // 4]
    tail = text[-(limit - len(head) - 20):]
    return head + "\n…[tronqué]…\n" + tail


def headline(output: str) -> str:
    """The one line a human wants: the last 'XxxError: …' line, if any."""
    lines = [m.group(0).strip() for m in _ERR_LINE.finditer(output or "")]
    if lines:
        return lines[-1][:240]
    tail = [l for l in (output or "").strip().splitlines() if l.strip()]
    return tail[-1][:240] if tail else ""
