"""
lint.py — a second opinion on the Python code, never a verdict.

Ruff reads the files (it never runs them) and reports what is almost always a
bug: undefined names, unused imports and variables, syntax errors, `return`
outside a function… — the F (Pyflakes), E9 and PLE rules only, with the
project's own configuration ignored (`--isolated`), so a generated
pyproject.toml cannot switch it off.

Pyright (`run_types`), when installed, adds a type check in "basic" mode —
errors only, missing third-party stubs tolerated — with a configuration of
ours (`--project`), so the project's own pyrightconfig.json is ignored too.

Both are shown in the summary as "analyse" and NEVER enter the fix loop:
a style remark must not spend the attempts meant for real failures, and a
clean analysis proves nothing about behaviour. Not installed → "non lancée".
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RULES = "F,E9,PLE"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _module_env(name: str) -> dict:
    """The environment for `python -m <name>`, with the folder this process
    found <name> in on PYTHONPATH.

    A pip install --user lands under %APPDATA% on Windows (~/.local on Linux):
    the child only sees it while that variable is intact. Passing the folder
    explicitly makes the child find what this process found, whatever the
    environment says (a sandboxed or tested environment rewrites APPDATA)."""
    env = dict(os.environ)
    spec = importlib.util.find_spec(name)
    if spec is not None and spec.origin:
        site = str(Path(spec.origin).resolve().parents[1])
        env["PYTHONPATH"] = os.pathsep.join(p for p in (site, env.get("PYTHONPATH")) if p)
    return env


def _ruff() -> list[str] | None:
    """The ruff binary itself when it can be found: `python -m ruff` only
    re-locates it through the user's folders (%APPDATA%, ~/.local), which a
    sandboxed environment may have rewritten."""
    name = "ruff.exe" if sys.platform == "win32" else "ruff"
    spec = importlib.util.find_spec("ruff")
    if spec is not None and spec.origin:
        site = Path(spec.origin).resolve().parents[1]
        # site-packages → Scripts (Windows, --user too) or bin (venv, ~/.local)
        for base in (site.parent, site.parent.parent, site.parent.parent.parent):
            for sub in ("Scripts", "bin"):
                if (base / sub / name).is_file():
                    return [str(base / sub / name)]
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    return [sys.executable, "-m", "ruff"] if spec is not None else None


def run(project_dir: Path, timeout: int = 60) -> dict:
    """{"ok": True/False/None, "count", "findings": [{path, line, code, message}], "unavailable"}"""
    if not any(p.suffix == ".py" for p in project_dir.rglob("*.py")
               if "_deps" not in p.parts and ".studio" not in p.parts):
        return {"ok": None, "unavailable": "aucun fichier Python", "count": 0, "findings": []}
    cmd = _ruff()
    if cmd is None:
        return {"ok": None, "unavailable": "Ruff n'est pas installé (pip install ruff)", "count": 0,
                "findings": []}
    argv = [*cmd, "check", "--isolated", "--no-cache", "--select", RULES, "--output-format", "json",
            "--exclude", "_deps,.studio,node_modules", "."]
    try:
        r = subprocess.run(argv, cwd=str(project_dir), capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW, env=_module_env("ruff"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": None, "unavailable": f"Ruff n'a pas répondu ({type(e).__name__})", "count": 0,
                "findings": []}
    # 0 = clean, 1 = findings; anything else (2, "No module named ruff"…) is
    # Ruff failing, and its empty output must not read as "no remark".
    if r.returncode not in (0, 1) or (r.returncode == 1 and not r.stdout.strip()):
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        return {"ok": None, "unavailable": f"Ruff a échoué ({err[-1][:120] if err else r.returncode})",
                "count": 0, "findings": []}
    try:
        items = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        return {"ok": None, "unavailable": "sortie de Ruff illisible", "count": 0, "findings": []}
    root = project_dir.resolve()
    findings = []
    for it in items if isinstance(items, list) else []:
        try:
            rel = Path(it.get("filename", "")).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            rel = Path(it.get("filename", "")).name
        findings.append({"path": rel, "line": (it.get("location") or {}).get("row"),
                         "code": it.get("code") or "", "message": str(it.get("message", ""))[:200]})
    return {"ok": not findings, "count": len(findings), "findings": findings[:50]}


def headline(result: dict) -> str:
    if result.get("unavailable"):
        return f"non lancée : {result['unavailable']}"
    if not result.get("count"):
        return "aucune remarque (Ruff : F, E9, PLE)"
    f = result["findings"][0]
    return f"{result['count']} remarque(s) — {f['path']}:{f['line']} {f['code']} {f['message']}"


# ── Pyright ─────────────────────────────────────────────────────────────────

def _pyright() -> list[str] | None:
    if importlib.util.find_spec("pyright") is not None:
        return [sys.executable, "-m", "pyright"]
    exe = shutil.which("pyright")
    return [exe] if exe else None


def _has_python(project_dir: Path) -> bool:
    return any("_deps" not in p.parts and ".studio" not in p.parts for p in project_dir.rglob("*.py"))


def run_types(project_dir: Path, timeout: int = 180) -> dict:
    """Same shape as run(): {"ok", "count", "findings", "unavailable"}."""
    if not _has_python(project_dir):
        return {"ok": None, "unavailable": "aucun fichier Python", "count": 0, "findings": []}
    cmd = _pyright()
    if cmd is None:
        return {"ok": None, "unavailable": "Pyright n'est pas installé (pip install pyright)", "count": 0,
                "findings": []}
    root = project_dir.resolve()
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                   if not ({"_deps", ".studio", "node_modules"} & set(p.relative_to(root).parts)))[:400]
    # Files on the command line: Pyright then ignores the project's own include
    # (and any pyrightconfig.json there — the settings are ours, via --project).
    config = {"extraPaths": [str(root), str(root / "_deps")], "typeCheckingMode": "basic",
              "reportMissingImports": "warning", "reportMissingModuleSource": "none"}
    with tempfile.TemporaryDirectory(prefix="studio-pyright-") as tmp:
        cfg = Path(tmp) / "pyrightconfig.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        try:
            r = subprocess.run([*cmd, "--outputjson", "--level", "error", "--project", str(cfg), *files],
                               cwd=str(root), capture_output=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW, env=_module_env("pyright"))
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"ok": None, "unavailable": f"Pyright n'a pas répondu ({type(e).__name__})", "count": 0,
                    "findings": []}
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {"ok": None, "unavailable": "sortie de Pyright illisible", "count": 0, "findings": []}
    if not (data.get("summary") or {}).get("filesAnalyzed"):
        # "0 errors" over 0 files is not a clean bill of health.
        return {"ok": None, "unavailable": "Pyright n'a analysé aucun fichier", "count": 0, "findings": []}
    findings = []
    for d in data.get("generalDiagnostics") or []:
        if d.get("severity") != "error":
            continue
        try:
            rel = Path(d.get("file", "")).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            rel = Path(d.get("file", "")).name
        findings.append({"path": rel, "line": ((d.get("range") or {}).get("start") or {}).get("line", 0) + 1,
                         "code": d.get("rule") or "pyright", "message": " ".join(str(d.get("message", "")).split())[:200]})
    return {"ok": not findings, "count": len(findings), "findings": findings[:50], "tool": "Pyright"}


def types_headline(result: dict) -> str:
    if result.get("unavailable"):
        return f"non lancée : {result['unavailable']}"
    if not result.get("count"):
        return "aucune erreur de type (Pyright, mode basic)"
    f = result["findings"][0]
    return f"{result['count']} erreur(s) de type — {f['path']}:{f['line']} {f['message']}"
