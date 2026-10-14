"""
e2e/hud_flows.py — la vraie page du HUD, dans un vrai Chromium (Playwright).

La page tourne comme dans Qt, avec le simulateur qu'elle charge elle-même
quand QWebChannel est absent (js/sim.js). Les panneaux qui demandent des
données à Python les reçoivent des VRAIS modules :

    GRAPHE  HUD.showGraph(payload), construit exactement comme
            client_desktop/hud/window.py le fait, depuis capabilities/graphs/ ;
    NUIT    rpc('nightshift.latest') → nightshift/fetch.py, servi d'un rapport
            jarvis.nightly/1 construit par nightshift/report.py — cache dans le
            dossier de travail, jamais memory/nightshift.json.

Ce qui est vérifié : la page se charge sans erreur JS, une commande et une
confirmation font le tour, chaque panneau s'ouvre et montre ce qu'on lui donne,
aucune requête ne casse. Le rendu 3D n'est pas jugé : Chromium sans GPU le
dessine en logiciel, et l'œil reste le seul juge d'un dessin.
"""
from __future__ import annotations

import functools
import http.server
import io
import json
import threading
import time
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path

from e2e.checks import NotApplicable, Report, Skip

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "client_desktop" / "hud" / "web"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a) -> None:
        pass


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                             functools.partial(_Quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, name="e2e-hud-http", daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/index.html"


def night_report() -> dict:
    """Un rapport de nuit réaliste : les vraies suites, une rouge sans référence, ruff propre."""
    from nightshift import report as R
    from nightshift.suites import SUITES

    suites = [{"name": s.name, "platform": "linux", "passed": 10, "total": 10, "status": "ok",
               "failures": [], "failed": [], "duration_s": 1.0} for s in SUITES]
    suites[-1] = {**suites[-1], "status": "failing", "passed": 9,
                  "failed": [{"id": "4", "label": "contrôle e2e", "detail": "rouge exprès"}],
                  "failures": ["4 contrôle e2e"]}
    started = datetime.now().astimezone().replace(microsecond=0) - timedelta(hours=5)
    return R.build(meta={"run_id": "e2e", "run_url": None, "repository": None, "event": "schedule"},
                   git={"commit": "e2e0001", "commit_full": "e2e0001", "ref": "main"},
                   env={"runner": "github", "os": "Ubuntu 24.04", "python": "3.12"},
                   started_at=started, finished_at=started + timedelta(seconds=93), suites=suites,
                   lint={"tool": "ruff", "available": True, "count": 0, "findings": [], "version": "0.17.0"},
                   previous=None, suspects=None, known={})


def night_rpc(work: Path) -> dict:
    """Ce que nightshift/hud.py répond, le réseau remplacé par le rapport ci-dessus."""
    from nightshift import DATA_BRANCH, fetch

    body = json.dumps(night_report(), ensure_ascii=False).encode("utf-8")

    class _Resp(io.BytesIO):
        status = 200
        headers = Message()

    def opener(_req, timeout=None):
        return _Resp(body)

    st = fetch.status(force=True, opener=opener, path=work / "nightshift.json")
    repo = st["repo"]
    return {**st, "actions_url": f"https://github.com/{repo}/actions/workflows/nightly.yml",
            "data_url": f"https://github.com/{repo}/tree/{DATA_BRANCH}"}


def graph_payload(start: str = "general", focus: str = "") -> dict:
    """Le même dict que client_desktop/hud/window.py:show_graph envoie à la page."""
    import capabilities as cap

    cap.reset()
    return {"graphs": cap.load_all(), "start": start, "focus": focus, "kinds": cap.KINDS,
            "statuses": cap.STATUSES, "relations": cap.RELATIONS}


class Page:
    """Chromium sur la page du HUD, avec ce qui s'y passe de travers."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.errors: list[str] = []
        self.rpc_calls: list[str] = []
        self._pw = self._browser = self.page = self._server = None

    def open(self) -> float:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Skip("Playwright absent (pip install playwright)")
        self._server, url = serve(WEB)
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as e:
            raise Skip(f"Chromium de Playwright absent ou incomplet — python -m playwright install chromium "
                       f"({str(e).splitlines()[0][:120]})")
        page = self.page = self._browser.new_page(viewport={"width": 1600, "height": 900})
        page.set_default_timeout(15000)
        page.on("pageerror", lambda exc: self.errors.append(f"exception JS : {exc}"))
        page.on("console", lambda msg: msg.type == "error" and self.errors.append(f"console : {msg.text}"))
        page.on("requestfailed", lambda req: self.errors.append(f"requête : {req.url} ({req.failure})"))
        page.on("response", lambda r: r.status >= 400 and not r.url.endswith("favicon.ico")
                and self.errors.append(f"HTTP {r.status} : {r.url}"))
        page.expose_function("__pyRpc", self._rpc)
        page.add_init_script(
            "window.__simRpc = (op, data) => window.__pyRpc(op, JSON.stringify(data || {})).then(JSON.parse);")
        t0 = time.monotonic()
        page.goto(url)
        page.wait_for_function("document.getElementById('link-text')?.textContent.includes('CONNECTÉ')")
        page.wait_for_function("!document.body.classList.contains('booting')")
        return time.monotonic() - t0

    def _rpc(self, op: str, data_json: str) -> str:
        self.rpc_calls.append(op)
        try:
            if op == "nightshift.latest":
                return json.dumps(night_rpc(self.work), ensure_ascii=False)
            return json.dumps({"error": f"e2e : {op} n'est pas branché ici"})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    def text(self, selector: str) -> str:
        return self.page.inner_text(selector)

    def close(self) -> None:
        for step in (lambda: self._browser.close(), lambda: self._pw.stop(), lambda: self._server.shutdown()):
            try:
                step()
            except Exception:
                pass


def _favicon_free(errors: list[str]) -> list[str]:
    return [e for e in errors if "favicon" not in e]


def run(report: Report, work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    p = Page(work)
    broken = ""

    def load() -> str:
        took = p.open()
        early = _favicon_free(p.errors)
        assert not early, f"erreurs au chargement : {early[:3]}"
        return f"chargée et « CONNECTÉ » en {took:.1f} s, aucune erreur JS"

    def command() -> str:
        p.page.fill("#cmd", "e2e : quelle météo à Paris ?")
        p.page.press("#cmd", "Enter")
        p.page.wait_for_function("document.getElementById('chat').innerText.includes('quelle météo à Paris')")
        p.page.wait_for_function("document.getElementById('chat').innerText.includes('Ciel dégagé')", timeout=10000)
        return "la commande s'affiche, la réponse arrive dans la conversation"

    def confirm() -> str:
        p.page.fill("#cmd", "éteins l'ordinateur")
        p.page.press("#cmd", "Enter")
        p.page.wait_for_selector("#confirm:not([hidden])", state="visible", timeout=10000)
        title = p.text("#cf-title")
        assert "Arrêt" in title, f"bandeau : {title!r}"
        p.page.click("#cf-no")
        p.page.wait_for_selector("#confirm", state="hidden")
        p.page.wait_for_function("document.getElementById('chat').innerText.includes('annule')")
        return f"bandeau « {title} », ANNULER le ferme et l'annulation est dite"

    def graph() -> str:
        payload = graph_payload("general", "f.rappels")
        general = payload["graphs"].get("general") or {}
        p.page.evaluate("(pl) => window.HUD.showGraph(pl)", payload)
        p.page.wait_for_selector("#graph", state="visible")
        title = p.text("#graph-title")
        assert title == (general.get("title") or "general"), f"titre affiché : {title!r}"
        p.page.fill("#graph-q", "rappel")
        p.page.wait_for_function("document.getElementById('graph-count').textContent.includes('trouvé')")
        found = p.text("#graph-count")
        sub = graph_payload("nightshift")
        p.page.evaluate("(pl) => window.HUD.showGraph(pl)", sub)
        p.page.wait_for_function("(t) => document.getElementById('graph-title').textContent === t",
                                 arg=sub["graphs"]["nightshift"].get("title") or "nightshift")
        p.page.click("#graph-close")
        p.page.wait_for_selector("#graph", state="hidden")
        return f"{len(payload['graphs'])} graphes ; « {title} », recherche « rappel » : {found} ; sous-graphe nightshift"

    def night() -> str:
        try:
            import nightshift  # noqa: F401
        except ImportError:
            raise NotApplicable("paquet nightshift retiré : pas d'onglet NUIT à tester")
        p.page.click("#btn-mind")
        p.page.wait_for_selector("#mind", state="visible")
        p.page.click("#mind-tabs button[data-tab=night]")
        p.page.wait_for_function("document.getElementById('night-suites').innerText.includes('capabilities.selftest')")
        tiles = p.text("#night-tiles")
        assert "RÉSULTAT" in tiles, tiles[:120]
        assert "nightshift.latest" in p.rpc_calls, "l'onglet n'a rien demandé à Python"
        suites = p.text("#night-suites")
        assert "SANS RÉFÉRENCE" in suites, "la suite rouge n'est pas marquée « sans référence »"
        p.page.click("#mind-close")
        p.page.wait_for_selector("#mind", state="hidden")
        return "rapport lu par nightshift/fetch.py : tuiles, les suites, la rouge marquée « sans référence »"

    def settings() -> str:
        p.page.click("#btn-settings")
        p.page.wait_for_selector("#settings", state="visible")
        tabs = p.page.eval_on_selector_all("#settings nav button[data-tab]", "els => els.map(e => e.dataset.tab)")
        assert tabs, "aucun onglet de réglages"
        for tab in tabs:
            p.page.click(f"#settings nav button[data-tab={tab}]")
            p.page.wait_for_selector(f"#settings section.set-tab[data-tab={tab}]", state="visible")
        p.page.click("#s-cancel")
        p.page.wait_for_selector("#settings", state="hidden")
        return f"{len(tabs)} onglets ouverts un à un : {', '.join(tabs)}"

    def clean() -> str:
        errors = _favicon_free(p.errors)
        assert not errors, f"{len(errors)} erreur(s) : {errors[:4]}"
        return f"aucune erreur JS ni requête cassée ; {len(p.rpc_calls)} appel(s) à Python"

    steps = [
        ("31", "la page du HUD se charge dans Chromium et se connecte, sans erreur JS", load, True),
        ("32", "une commande tapée s'affiche dans la conversation, la réponse arrive", command, False),
        ("33", "une confirmation montre son bandeau ; ANNULER le ferme", confirm, False),
        ("34", "le panneau GRAPHE affiche les vrais graphes de capabilities/, recherche comprise", graph, False),
        ("35", "l'onglet NUIT affiche un vrai rapport jarvis.nightly lu par nightshift/fetch.py", night, False),
        ("36", "les réglages s'ouvrent et chaque onglet s'affiche", settings, False),
        ("37", "aucune erreur JS ni requête cassée pendant tout le parcours", clean, False),
    ]
    skipped = ""
    try:
        for cid, label, fn, blocking in steps:
            if broken:
                report.not_run(cid, label, broken)
                continue
            if skipped:
                report.run(cid, label, lambda why=skipped: (_ for _ in ()).throw(Skip(why)))
                continue
            ok = report.run(cid, label, fn)
            if blocking and p.page is None:
                skipped = "pas de navigateur (voir le n°31)"
            elif not ok and blocking:
                broken = "la page ne s'est pas chargée"
    finally:
        p.close()
