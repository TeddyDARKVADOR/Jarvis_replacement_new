"""
webcheck.py — a real browser opens the web project and tries it.

    run(project_dir, entry, checks, reports_dir)   from the Studio (this process)
    python webcheck.py <spec.json>                 the browser side (its own process)

What it proves, and only that:
  • the page loads from a local server and draws something;
  • no JavaScript error is thrown, no error in the console, no local file
    missing (404);
  • each scripted path of `web_checks` (click, fill, press… then expect a text,
    a count, a value) really behaves as expected.
It also keeps a screenshot, so the HUD shows what the page looked like.

What it does NOT do: isolate the project from the PC. The page runs in a
headless Chromium (Chromium's own sandbox), served on 127.0.0.1 only, and every
request that is not for that local server is BLOCKED and counted — a page that
needs a CDN is told so instead of silently reaching the Internet.

The browser side runs in a separate process so a hung page, a crashed
browser or Playwright's own event loop never touch Matthew; it is killed at the
deadline. No Playwright, no Chromium: the check says "unavailable", it never
pretends to pass.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

#: Read once, at import: the Studio rewrites TEMP/LOCALAPPDATA-like variables
#: for the commands it runs, the browsers stay where Playwright installed them.
_BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (
    str(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright") if os.environ.get("LOCALAPPDATA") else "")
_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION)", re.I)
DEADLINE = 90
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def available() -> str:
    """'' when the browser check can run, else why not (in French)."""
    if importlib.util.find_spec("playwright") is None:
        return "Playwright n'est pas installé (pip install playwright)."
    return ""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, seconds: float = 6.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not _SECRET_RE.search(k)}
    for k in ("PYTHONSTARTUP", "PYTHONHOME", "PYTHONINSPECT", "PYTHONPATH"):
        env.pop(k, None)
    if _BROWSERS:
        env["PLAYWRIGHT_BROWSERS_PATH"] = _BROWSERS
    tmp.mkdir(parents=True, exist_ok=True)
    env.update({"TEMP": str(tmp), "TMP": str(tmp), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return env


def run(project_dir: Path, entry: str, checks: list[dict], reports_dir: Path,
        stop_event=None, deadline: int = DEADLINE) -> dict:
    """Serve the project on 127.0.0.1, let the browser try it, return the report.

    report["ok"] is True / False, or None when the check could not run at all.
    """
    why = available()
    if why:
        return {"ok": None, "unavailable": why}
    reports_dir.mkdir(parents=True, exist_ok=True)
    shot = reports_dir / "apercu.png"
    report_path = reports_dir / "navigateur.json"
    for p in (shot, report_path):
        try:
            p.unlink()
        except OSError:
            pass
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    page = (entry or "index.html").lstrip("/")
    env = _env(reports_dir / "tmp")
    py_flags = ["-P"] if sys.version_info >= (3, 11) else []
    server = subprocess.Popen(
        [sys.executable, *py_flags, "-m", "http.server", str(port), "--bind", "127.0.0.1",
         "--directory", str(project_dir)],
        cwd=str(project_dir), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    try:
        if not _wait_port(port):
            return {"ok": None, "unavailable": "le serveur local de test n'a pas démarré"}
        spec = {"url": f"{origin}/{page}", "origin": origin, "checks": checks,
                "screenshot": str(shot), "report": str(report_path)}
        spec_path = reports_dir / "navigateur.spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        browser = subprocess.Popen([sys.executable, *py_flags, str(Path(__file__).resolve()), str(spec_path)],
                                   cwd=str(reports_dir), env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=_NO_WINDOW)
        ended = _wait(browser, stop_event, deadline)
        if ended == "stopped":
            return {"ok": None, "unavailable": "arrêté"}
        if ended == "timeout":
            return {"ok": False, "loaded": False,
                    "page_errors": [f"TimeoutError: le navigateur n'a pas fini en {deadline} s"],
                    "console_errors": [], "failed_requests": [], "blocked": [], "checks": []}
        tail = (browser.stdout.read() if browser.stdout else b"").decode("utf-8", "replace")[-2000:]
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": None, "unavailable": "le navigateur de test n'a rien rendu : " + (tail.strip()[-300:] or "?")}
        report["screenshot"] = str(shot) if shot.is_file() else ""
        return report
    finally:
        _kill(server)


def _wait(proc: subprocess.Popen, stop_event, deadline: int) -> str:
    """'done', or 'stopped' / 'timeout' after killing it."""
    end = time.monotonic() + deadline
    while proc.poll() is None:
        stopped = stop_event is not None and stop_event.is_set()
        if stopped or time.monotonic() > end:
            _kill(proc)
            return "stopped" if stopped else "timeout"
        time.sleep(0.2)
    return "done"


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=_NO_WINDOW)
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def as_output(report: dict) -> str:
    """The report as text the fix loop (diagnose.py) can read: Error lines + file:line."""
    # Symptoms first, root causes last: diagnose.headline() keeps the LAST error
    # line, and a JavaScript exception explains a failed path, not the reverse.
    lines: list[str] = []
    if report.get("unavailable"):
        return f"Contrôle navigateur non lancé : {report['unavailable']}"
    for c in report.get("checks") or []:
        if not c.get("ok"):
            lines.append(f"AssertionError: parcours {c.get('id') or c.get('name')} — {c.get('detail')}")
    if report.get("empty"):
        lines.append("AssertionError: la page s'affiche vide (aucun élément dans <body>)")
    for r in report.get("failed_requests") or []:
        lines.append(f"FileNotFoundError: fichier local manquant {r}")
    for e in report.get("console_errors") or []:
        lines.append(f"ConsoleError: {e}")
    for e in report.get("page_errors") or []:
        lines.append(e if re.match(r"^\w+(Error|Exception)\b", e) else f"PageError: {e}")
    if not report.get("loaded", True):
        lines.append(f"PageLoadError: la page ne se charge pas ({report.get('status') or 'aucune réponse'})")
    if report.get("blocked"):
        lines.append(f"(info) {len(report['blocked'])} requête(s) vers Internet bloquée(s) : "
                     + ", ".join(report["blocked"][:3]))
    ok_checks = [c for c in report.get("checks") or [] if c.get("ok")]
    if ok_checks:
        lines.append(f"{len(ok_checks)} parcours navigateur réussi(s) : "
                     + ", ".join(c.get("id") or c.get("name") for c in ok_checks))
    return "\n".join(lines) or "Page chargée sans erreur."


# ── the browser side (its own process) ─────────────────────────────────────

def _short(url: str, origin: str) -> str:
    return url[len(origin) + 1:] if url.startswith(origin) else url


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _poll(fn, seconds: float = 3.0):
    """Retry an expectation for a moment: a page updates after a click, not during it."""
    end = time.monotonic() + seconds
    while True:
        ok, detail = fn()
        if ok or time.monotonic() > end:
            return ok, detail
        time.sleep(0.15)


def _expectation(page, e: dict) -> tuple[bool, str]:
    loc = page.locator(e["selector"])
    n = loc.count()
    if "count" in e:
        return n == e["count"], f"attendu {e['count']} × « {e['selector']} », trouvé {n}"
    if n == 0:
        return False, f"« {e['selector']} » introuvable"
    first = loc.first
    if "visible" in e and first.is_visible() != e["visible"]:
        return False, f"« {e['selector']} » {'invisible' if e['visible'] else 'visible'}"
    if "value" in e:
        v = first.input_value()
        if _norm(v) != _norm(e["value"]):
            return False, f"« {e['selector']} » vaut « {v[:60]} », attendu « {e['value']} »"
    if "text" in e:
        txt = " ".join(loc.all_inner_texts()) if n <= 50 else first.inner_text()
        if _norm(e["text"]) not in _norm(txt):
            return False, f"« {e['selector']} » ne contient pas « {e['text']} » (texte : « {txt.strip()[:80]} »)"
    return True, ""


def _do(page, s: dict) -> None:
    a, sel = s["action"], s.get("selector", "")
    if a == "wait":
        page.wait_for_timeout(int(s.get("ms") or 300))
        return
    loc = page.locator(sel).first
    if a == "click":
        loc.click(timeout=5000)
    elif a == "fill":
        loc.fill(s.get("value", ""), timeout=5000)
    elif a == "press":
        loc.press(s.get("key") or "Enter", timeout=5000)
    elif a == "check":
        loc.check(timeout=5000)
    elif a == "uncheck":
        loc.uncheck(timeout=5000)
    elif a == "select":
        loc.select_option(s.get("value", ""), timeout=5000)
    page.wait_for_timeout(120)


class _Session:
    """One headless Chromium; every page it opens is watched the same way."""

    OPTIONS = {"viewport": {"width": 1280, "height": 800}, "service_workers": "block", "accept_downloads": False}

    def __init__(self, browser, spec: dict, report: dict, error_cls) -> None:
        self.browser, self.spec, self.report, self.Error = browser, spec, report, error_cls
        self.origin = spec["origin"]

    def _route(self, r) -> None:
        u = r.request.url
        if u.startswith(self.origin) or u.startswith(("data:", "blob:", "about:")):
            r.continue_()
            return
        if u not in self.report["blocked"]:
            self.report["blocked"].append(u[:200])
        r.abort("blockedbyclient")

    def _on_console(self, sink: dict, m) -> None:
        text = m.text or ""
        if m.type != "error" or "ERR_BLOCKED_BY_CLIENT" in text or "favicon" in text or "status of 404" in text:
            return                                   # blocked / missing files are counted elsewhere
        loc = m.location or {}
        where = _short(loc.get("url") or "", self.origin)
        sink["console"].append(text[:300] + (f" ({where}:{loc.get('lineNumber', 0) + 1})" if where else ""))

    def _on_error(self, sink: dict, e) -> None:
        stack = getattr(e, "stack", "") or ""
        m = re.search(re.escape(self.origin) + r"/([^\s:)]+):(\d+)", stack)
        where = f" ({m.group(1)}:{m.group(2)})" if m else ""
        msg = getattr(e, "message", "") or str(e)
        sink["errors"].append(f"{getattr(e, 'name', '') or 'Error'}: {msg[:300]}{where}")

    def _on_response(self, sink: dict, r) -> None:
        if r.status >= 400 and r.url.startswith(self.origin) and not r.url.endswith("favicon.ico"):
            sink["failed"].append(f"{r.status} {_short(r.url, self.origin)}")

    def open(self):
        """A fresh context (no state carried over) and a watched page in it."""
        ctx = self.browser.new_context(**self.OPTIONS)
        ctx.route("**/*", self._route)
        page = ctx.new_page()
        sink: dict = {"errors": [], "console": [], "failed": []}
        page.on("console", lambda m: self._on_console(sink, m))
        page.on("pageerror", lambda e: self._on_error(sink, e))
        page.on("response", lambda r: self._on_response(sink, r))
        return ctx, page, sink

    def first_load(self) -> None:
        ctx, page, sink = self.open()
        rep = self.report
        try:
            resp = page.goto(self.spec["url"], wait_until="load", timeout=15000)
            rep["status"] = resp.status if resp else None
            rep["loaded"] = bool(resp) and resp.status < 400
        except self.Error as e:
            sink["errors"].append(f"PageLoadError: {str(e).splitlines()[0][:200]}")
        if rep["loaded"]:
            page.wait_for_timeout(700)
            rep["title"] = (page.title() or "")[:120]
            try:
                rep["empty"] = not page.evaluate(
                    "() => !!document.body && document.body.querySelectorAll('*').length > 0")
                page.screenshot(path=self.spec["screenshot"], full_page=False)
            except self.Error:
                pass
        ctx.close()
        rep["page_errors"] += sink["errors"]
        rep["console_errors"] += sink["console"]
        rep["failed_requests"] += sorted(set(sink["failed"]))

    def play(self, c: dict) -> None:
        ctx, page, sink = self.open()
        row = {"id": c.get("id", ""), "name": c.get("name", ""), "ok": False, "detail": ""}
        try:
            page.goto(self.spec["url"], wait_until="load", timeout=15000)
            page.wait_for_timeout(300)
            for i, s in enumerate(c.get("steps") or [], 1):
                try:
                    _do(page, s)
                except self.Error as e:
                    raise AssertionError(f"étape {i} ({s['action']} « {s.get('selector', '')} ») : "
                                         f"{str(e).splitlines()[0][:160]}")
            for e in c.get("expect") or []:
                ok, detail = _poll(lambda e=e: _expectation(page, e))
                if not ok:
                    raise AssertionError(detail)
            if sink["errors"]:
                raise AssertionError(f"erreur JavaScript pendant le parcours : {sink['errors'][0]}")
            row["ok"] = True
            row["detail"] = f"{len(c.get('steps') or [])} étapes, {len(c.get('expect') or [])} vérifications"
        except AssertionError as e:
            row["detail"] = str(e)
        except self.Error as e:
            row["detail"] = str(e).splitlines()[0][:200]
        self.report["checks"].append(row)
        self.report["page_errors"] += [e for e in sink["errors"] if e not in self.report["page_errors"]]
        ctx.close()


def _browser_main(spec_path: str) -> int:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    report: dict = {"ok": False, "loaded": False, "status": None, "title": "", "empty": False,
                    "page_errors": [], "console_errors": [], "failed_requests": [], "blocked": [],
                    "checks": [], "seconds": 0}
    t0 = time.monotonic()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError as e:
                raise RuntimeError("navigateur Chromium absent : python -m playwright install chromium"
                                   if "Executable doesn't exist" in str(e) else f"Chromium : {str(e)[:200]}")
            session = _Session(browser, spec, report, PWError)
            session.first_load()
            for c in (spec.get("checks") or []) if report["loaded"] else []:
                session.play(c)
            browser.close()
        report["ok"] = (report["loaded"] and not report["empty"] and not report["page_errors"]
                        and not report["console_errors"] and not report["failed_requests"]
                        and all(c["ok"] for c in report["checks"]))
    except Exception as e:                  # Playwright itself broke: say so, never "passed"
        report["ok"] = None
        report["unavailable"] = str(e)[:240] if isinstance(e, RuntimeError) else f"{type(e).__name__}: {str(e)[:200]}"
    report["seconds"] = round(time.monotonic() - t0, 2)
    Path(spec["report"]).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_browser_main(sys.argv[1]))
