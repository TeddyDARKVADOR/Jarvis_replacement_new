"""
capabilities/verify.py — vérifier ce que JARVIS sait faire, et l'écrire dans son graphe.

    python -m capabilities.verify              sondes des outils + selftests, rapport
    python -m capabilities.verify --quick      sondes seulement (quelques secondes)
    python -m capabilities.verify --write      … et met à jour les états des graphes

Le graphe disait jusqu'ici ce qu'une personne avait écrit. Ici, l'état d'un
outil et celui d'un nœud qui porte un `test` viennent d'une exécution :

  sonde réelle   l'outil est appelé pour de vrai, sans effet sur le PC : dans un
                 dossier temporaire, en lecture seule, ou une capture d'écran.
                 Réussite → ok. Échec → bug, sauf si l'erreur est un quota ou le
                 réseau : l'état reste à tester, la cause est notée.
  sonde à vide   l'outil a un effet que rien ne permet d'annuler sans risque
                 (envoyer un message, éteindre, programmer une tâche, ouvrir une
                 application). On vérifie que le module et ses dépendances se
                 chargent : → à tester, jamais ok. Une dépendance absente → bug.
  selftest       un nœud dont le `test` passe → ok ; partiellement → partiel ;
                 échoue → bug. Les outils couverts par une suite (Studio,
                 objectifs, mémoire cognitive) en prennent le résultat.

Chaque nœud vérifié reçoit `verified` : la date, le moyen et le résultat. Le
rapport complet est écrit dans capabilities/verification.json.

Rien ici ne s'exécute au démarrage de JARVIS : c'est un outil de développement.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import capabilities as cap

REPO = cap.REPO
REPORT = cap.ROOT / "verification.json"

#: Une erreur qui dit quelque chose du réseau ou du quota, pas de l'outil.
_EXTERNAL = re.compile(r"429|RESOURCE_EXHAUSTED|quota|timed? ?out|getaddrinfo|ConnectionError|"
                       r"Name or service|network|unreachable|503|UNAVAILABLE|paused", re.IGNORECASE)
#: Un texte d'outil qui annonce son propre échec.
_FAILED = re.compile(r"^\s*(error|erreur|❌)|failed|échec|traceback|not found|introuvable|could not|"
                     r"cannot|unable|no longer|impossible", re.IGNORECASE)


# ── sondes ───────────────────────────────────────────────────────────────────

def _call(module: str, func: str, *args, **kwargs) -> str:
    return str(getattr(importlib.import_module(module), func)(*args, **kwargs))


def _expect(text: str, *needles: str) -> str:
    """Réussite si le texte ne s'annonce pas en échec et contient chaque indice."""
    low = text.lower()
    if _FAILED.search(text[:200]):
        raise AssertionError(text[:200])
    missing = [n for n in needles if n.lower() not in low]
    if missing:
        raise AssertionError(f"attendu {missing} dans : {text[:160]}")
    return text


def _file_controller(tmp: Path) -> str:
    fc = "actions.file_controller"
    _expect(_call(fc, "file_controller", {"action": "create_file", "path": str(tmp), "name": "a.txt",
                                          "content": "bonjour"}))
    _expect(_call(fc, "file_controller", {"action": "read", "path": str(tmp), "name": "a.txt"}), "bonjour")
    _expect(_call(fc, "file_controller", {"action": "rename", "path": str(tmp), "name": "a.txt",
                                          "new_name": "b.txt"}))
    listed = _expect(_call(fc, "file_controller", {"action": "list", "path": str(tmp)}), "b.txt")
    _call(fc, "file_controller", {"action": "delete", "path": str(tmp), "name": "b.txt"})
    assert not (tmp / "b.txt").exists(), "delete n'a rien supprimé"
    return f"créer, lire, renommer, lister, supprimer dans un dossier temporaire ({len(listed)} car.)"


def _file_processor(tmp: Path) -> str:
    f = tmp / "note.txt"
    f.write_text("Un deux trois quatre cinq.", encoding="utf-8")
    out = _call("actions.file_processor", "file_processor",
                {"file_path": str(f), "action": "word_count", "save": False})
    _expect(out, "5")
    return "word_count sur un fichier texte : 5 mots"


def _code_helper(tmp: Path) -> str:
    script = tmp / "hello.py"
    script.write_text("print('sonde-ok')\n", encoding="utf-8")
    out = _call("actions.code_helper", "code_helper", {"action": "run", "file_path": str(script)})
    _expect(out, "sonde-ok")
    return "run d'un script temporaire"


def _computer_control(tmp: Path) -> str:
    cc = "actions.computer_control"
    _expect(_call(cc, "computer_control", {"action": "random_data", "type": "email"}), "@")
    shot = tmp / "shot.png"
    _call(cc, "computer_control", {"action": "screenshot", "path": str(shot)})
    assert shot.exists() and shot.stat().st_size > 1000, "capture absente"
    return f"random_data, screenshot ({shot.stat().st_size // 1024} Ko)"


def _screen_process(tmp: Path) -> str:
    data, fmt = importlib.import_module("actions.screen_processor")._capture_screen("primary")
    assert data and len(data) > 1000, "capture vide"
    return f"capture de l'écran principal ({len(data) // 1024} Ko, {fmt}) ; l'analyse passe par Gemini Live"


def _system_status(tmp: Path) -> str:
    st = importlib.import_module("actions.system_monitor").get_system_status()
    assert isinstance(st, dict) and st, st
    return "métriques : " + ", ".join(sorted(st)[:6])


def _recall_memory(tmp: Path) -> str:
    out = _call("memory.memory_manager", "search_memory", "")
    return f"recherche locale ({len(out)} car.)"


def _undo(tmp: Path) -> str:
    undo = importlib.import_module("core.undo")
    f = tmp / "x.txt"
    f.write_text("avant", encoding="utf-8")
    undo.push_undo("sonde : écrire x.txt", lambda: (f.write_text("avant", encoding="utf-8"), "restauré")[1])
    f.write_text("après", encoding="utf-8")
    out = undo.undo_last()
    assert f.read_text(encoding="utf-8") == "avant", f"pas restauré : {out}"
    return "une modification enregistrée puis annulée"


def _manage_monitor(tmp: Path) -> str:
    topics = importlib.import_module("actions.background_monitor").list_monitors()
    return f"{len(topics)} sujet(s) suivi(s), liste lue"


def _game_updater(tmp: Path) -> str:
    out = _call("actions.game_updater", "game_updater", {"action": "list", "platform": "both"})
    return f"liste des jeux installés lue ({len(out)} car.)"


def _browser_control(tmp: Path) -> str:
    out = _call("actions.browser_control", "browser_control", {"action": "list_browsers"})
    _expect(out)
    return f"navigateurs détectés : {out[:80]}"


def _desktop_control(tmp: Path) -> str:
    out = _call("actions.desktop", "desktop_control", {"action": "stats"})
    _expect(out)
    return f"statistiques du bureau ({len(out)} car.)"


def _web_search(tmp: Path) -> str:
    out = _call("actions.web_search", "web_search", {"query": "Paris", "mode": "search"})
    assert len(out) > 80 and "no results" not in out.lower(), out[:120]
    return f"recherche réelle ({len(out)} car.)"


def _youtube(tmp: Path) -> str:
    # play = chercher la vidéo, puis ouvrir le navigateur : seule la recherche est faite
    url = importlib.import_module("actions.youtube_video")._scrape_first_video_url("lofi hip hop")
    assert url and "watch?v=" in url, f"aucune vidéo trouvée : {url}"
    return "recherche de vidéo pour play (le navigateur n'est pas ouvert) ; trending n'existe plus chez YouTube"


def _repository_review(tmp: Path) -> str:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "README.md").write_text("Calculatrice.", encoding="utf-8")
    out = _call("actions.repository_review", "inspect_repository", {"repo_path": str(repo), "mode": "overview"})
    _expect(out)
    return f"vue d'ensemble d'un dépôt temporaire ({len(out)} car.)"


def _usage_report(tmp: Path) -> str:
    return _call("actions.usage_report", "usage_report", {})[:80]


def _capabilities(tmp: Path) -> str:
    mod = importlib.import_module("plugins.capabilities")
    _expect(mod.run({"action": "find", "query": "recherche web"}), "web_search")
    return "find répond depuis le graphe"


def _mcp(tmp: Path) -> str:
    out = importlib.import_module("plugins.mcp_bridge").run({"action": "list"})
    assert out and ("Aucun serveur MCP" in out or "outil(s)" in out or "indisponible" in out), out[:200]
    return "list répond (" + ("aucun serveur configuré" if "Aucun serveur" in out else "serveurs listés") + ")"


def _skills(tmp: Path) -> str:
    mod = importlib.import_module("plugins.skills")
    _expect(mod.run({"action": "list"}), "preparer-un-voyage")
    _expect(mod.run({"action": "use", "name": "fin-de-journee"}), "recall_episodes")
    return "index et lecture d'un skill livré"


def _health(tmp: Path) -> str:
    out = importlib.import_module("plugins.health_diagnostic").run({})
    return f"diagnostic lu ({len(out)} car.)"


def _itinerary(tmp: Path) -> str:
    url = importlib.import_module("plugins.itinerary")._route_url("Paris", "Lyon", "driving")
    assert url.startswith("https://") and "Lyon" in url, url
    return "URL d'itinéraire construite (le navigateur n'est pas ouvert)"


def _meeting(tmp: Path) -> str:
    importlib.import_module("plugins.meeting")
    from meeting import detect
    usage = detect.in_use()
    return f"plugin chargé ; détection ({usage.source}) : {usage.describe()}" + \
        (f" — {usage.error}" if usage.error else "")


def _pilot(tmp: Path) -> str:
    importlib.import_module("plugins.pilot")
    from pilot import a11y, discover
    obs = a11y.observe(with_image=False)              # lecture seule : rien n'est cliqué ni tapé
    return (f"plugin chargé ; {obs.backend or 'pas d arbre'} : « {obs.window.title[:30]} », "
            f"{len(obs.elements)} éléments ; {len(discover.all_schemes())} schémas d'URL")


def _quotas(tmp: Path) -> str:
    text = importlib.import_module("plugins.quotas").run({})
    assert "Remise à zéro" in text, text
    return text.splitlines()[-1]


def _briefing(tmp: Path) -> str:
    importlib.import_module("plugins.briefing")
    import daily
    b = daily.compose({}, topics=lambda: [], reminders_of=lambda d: [], meeting_of=lambda n: None, state={})
    return f"briefing composé (sans réseau) : {b.text().splitlines()[0]}"


def _incidents(tmp: Path) -> str:
    importlib.import_module("plugins.incidents")
    import incidents
    out = incidents.generic(r"open C:\Users\x\confidentiel.txt key AIza" + "x" * 30)
    assert "confidentiel" not in out and "AIza" not in out, out
    return f"anonymisation vérifiée ; {incidents.summary().splitlines()[0]}"


def _extensions(tmp: Path) -> str:
    tool = importlib.import_module("plugins.extensions_manager")
    from extensions import manager
    f = tmp / "demo.py"
    f.write_text("\n".join(["PLUGIN = {'name': 'demo', 'description': 'd', 'parameters': {'type': 'OBJECT'}}",
                            "import subprocess", "def run(p, **k):", "    return 'ok'", ""]), encoding="utf-8")
    rep = manager.inspect(str(f))
    assert rep.ok and rep.undeclared == ["shell"], rep
    return f"examen sans exécution : « shell » non déclaré repéré ; {tool.run({'action': 'list'})[:60]}"


def _portable(tmp: Path) -> str:
    import os
    p = importlib.import_module("plugins.portable")
    # Hors mode portable : l'outil le dit sans se tromper.
    saved = {k: os.environ.get(k) for k in ("JARVIS_PORTABLE", "JARVIS_DATA_DIR", "USERPROFILE")}
    for k in ("JARVIS_PORTABLE", "JARVIS_DATA_DIR"):
        os.environ.pop(k, None)
    try:
        off = p.run({"action": "status"})
        assert "pas" in off.lower() or "portable" in off.lower(), off
        # Mode portable simulé : les données doivent pointer sur la clé (ici tmp).
        data = tmp / "data"
        data.mkdir(parents=True, exist_ok=True)
        os.environ["JARVIS_PORTABLE"] = "1"
        os.environ["JARVIS_DATA_DIR"] = str(data)
        os.environ["USERPROFILE"] = str(data / "home")
        on = p.run({"action": "status"})
        assert str(data) in on and "portable" in on.lower(), on
        ej = p.run({"action": "eject"})
        assert "éject" in ej.lower() or "retir" in ej.lower(), ej
        doc = p.run({"action": "doctor"})
        assert "bilan" in doc.lower(), doc
        note = p.run({"action": "note", "text": "le script de déploiement"})
        assert "not" in note.lower(), note
        rez = p.run({"action": "resume"})
        assert isinstance(rez, str) and len(rez) > 10, rez
        aud = p.run({"action": "audit"})
        assert isinstance(aud, str) and aud, aud
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    # Le gardien d'éjection : sans commande, il est sans effet (sort en 0).
    guard = importlib.import_module("portable.eject_guard")
    assert guard.main([]) == 0
    return "mode portable détecté, données sur la clé, éjection expliquée ; gardien à vide inerte"


def _matthew(tmp: Path) -> str:
    import os
    p = importlib.import_module("plugins.matthew")
    real = p.run({"action": "status"})                 # vraies données, lecture seule
    assert real.startswith("Matthew"), real
    saved = os.environ.get("MATTHEW_HOME")
    os.environ["MATTHEW_HOME"] = str(tmp / "matthew")  # écritures : dans tmp seulement
    try:
        from matthew.journal import Journal
        Journal().episode(session="verify", user="salut", tools=[], reply="bonjour")
        assert "Noté" in p.run({"action": "correct", "text": "Bonjour, ici Matthew."})
        assert "plus" in p.run({"action": "pause"}) and "nouveau" in p.run({"action": "resume"})
    finally:
        if saved is None:
            os.environ.pop("MATTHEW_HOME", None)
        else:
            os.environ["MATTHEW_HOME"] = saved
    return real[:110]


def _dry(module: str, *deps: str):
    def probe(tmp: Path) -> str:
        importlib.import_module(module)
        missing = []
        for d in deps:
            try:
                importlib.import_module(d)
            except Exception:
                missing.append(d)
        if missing:
            raise ImportError(f"dépendance absente : {', '.join(missing)}")
        return "module et dépendances chargés ; non exécuté (effet réel sur le PC)"
    return probe


#: outil → (genre, sonde). "reel" ou "vide" ; "suite" = couvert par un selftest.
PROBES = {
    "file_controller": ("reel", _file_controller),
    "file_processor": ("reel", _file_processor),
    "code_helper": ("reel", _code_helper),
    "computer_control": ("reel", _computer_control),
    "screen_process": ("reel", _screen_process),
    "system_status": ("reel", _system_status),
    "recall_memory": ("reel", _recall_memory),
    "undo": ("reel", _undo),
    "manage_monitor": ("reel", _manage_monitor),
    "game_updater": ("reel", _game_updater),
    "browser_control": ("reel", _browser_control),
    "desktop_control": ("reel", _desktop_control),
    "web_search": ("reel", _web_search),
    "youtube_video": ("reel", _youtube),
    "repository_review": ("reel", _repository_review),
    "usage_report": ("reel", _usage_report),
    "capabilities": ("reel", _capabilities),
    "mcp": ("reel", _mcp),
    "skills": ("reel", _skills),
    "local_health_check": ("vide", _health),
    "open_itinerary": ("vide", _itinerary),
    "open_app": ("vide", _dry("actions.open_app")),
    "computer_settings": ("vide", _dry("actions.computer_settings", "pyautogui")),
    "send_message": ("vide", _dry("actions.send_message", "pyautogui")),
    "reminder": ("vide", _dry("actions.reminder")),
    "meeting": ("reel", _meeting),
    "pilot": ("reel", _pilot),
    "quotas": ("reel", _quotas),
    "briefing": ("reel", _briefing),
    "incidents": ("reel", _incidents),
    "extensions": ("reel", _extensions),
    "portable": ("reel", _portable),
    "matthew": ("reel", _matthew),
    "weather_report": ("vide", _dry("actions.weather_report")),
    "flight_finder": ("vide", _dry("actions.flight_finder")),
    "save_memory": ("vide", _dry("memory.memory_manager")),
    "close_camera": ("vide", _dry("actions.screen_processor", "cv2")),
    "shutdown_jarvis": ("vide", _dry("core.confirm")),
}

#: Les suites, et les outils dont elles sont la preuve.
SUITES = {
    "capabilities.selftest": [],
    "context.selftest": [],
    "server.selftest": [],
    "server.routing_selftest": [],
    "client_desktop --selftest": [],
    "cognition.selftest": ["goals", "self_state", "recall_episodes", "forget_about"],
    "eval_lab.selftest": [],
    "core.code_workspace.selftest": ["code_studio"],
    "core.token_usage_selftest": [],
    "actions.repository_review_selftest": [],
    "reminders.selftest": [],
    "nightshift.selftest": [],
}


def run_probes() -> dict[str, dict]:
    results = {}
    with tempfile.TemporaryDirectory(prefix="jarvis-verify-") as tmp:
        for tool, (kind, fn) in PROBES.items():
            t0 = time.perf_counter()
            work = Path(tmp) / tool
            work.mkdir()
            try:
                detail = fn(work)
                outcome = "ok" if kind == "reel" else "vide"
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"[:300]
                outcome = "externe" if _EXTERNAL.search(detail) else "echec"
            results[tool] = {"kind": kind, "outcome": outcome, "detail": str(detail)[:300],
                             "seconds": round(time.perf_counter() - t0, 2)}
            print(f"  {outcome:8} {tool:18} {str(detail)[:90]}")
    return results


def run_suites() -> dict[str, dict]:
    results = {}
    for suite in SUITES:
        module, *extra = suite.split()
        t0 = time.perf_counter()
        try:
            proc = subprocess.run([sys.executable, "-m", module, *extra], cwd=REPO, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=1200,
                                  env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
            text = proc.stdout + proc.stderr
            score = re.findall(r"(\d+)\s*/\s*(\d+)", text)
            passed, total = (int(score[-1][0]), int(score[-1][1])) if score else (None, None)
            if proc.returncode == 0:
                outcome = "ok"
            elif passed is not None and passed > 0:
                outcome = "partiel"
            else:
                outcome = "echec"
            fails = [ln.strip()[:140] for ln in text.splitlines() if "FAIL" in ln][:5]
        except subprocess.TimeoutExpired:
            outcome, passed, total, fails = "echec", None, None, ["délai dépassé"]
        results[suite] = {"outcome": outcome, "passed": passed, "total": total, "failures": fails,
                          "seconds": round(time.perf_counter() - t0, 1)}
        print(f"  {outcome:8} {suite:36} {passed}/{total}")
    return results


# ── du résultat à l'état ─────────────────────────────────────────────────────

_SUITE_STATUS = {"ok": "ok", "partiel": "partiel", "echec": "bug"}


def _probe_status(res: dict, previous: str | None) -> str | None:
    if res["outcome"] == "ok":
        return "ok"
    if res["outcome"] == "echec":
        return "bug"
    if res["outcome"] == "externe":
        return previous if previous in ("partiel", "bug") else "a_tester"
    # vide : jamais mieux qu'à tester, et un défaut déjà connu reste connu
    return previous if previous in ("partiel", "bug") else "a_tester"


def _test_module(test: str) -> str | None:
    m = re.search(r"python -m (\S+)(?:\s+(--selftest))?", test or "")
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)}" if m.group(2) else m.group(1)


def apply(probes: dict, suites: dict, today: str) -> list[str]:
    """Écrit états et `verified` dans les graphes. Rend la liste des changements."""
    changes = []
    suite_of_tool = {t: s for s, tools in SUITES.items() for t in tools}
    for gid in cap.graph_ids():
        g = cap.load(gid)
        touched = False
        for n in g["nodes"]:
            new = note = None
            tool = n.get("tool")
            if tool and tool in probes:
                res = probes[tool]
                # une sonde réelle « ok » est la meilleure preuve ; un défaut
                # déjà décrit (partiel) le reste tant qu'il n'est pas corrigé
                new = _probe_status(res, n.get("status"))
                if new == "ok" and n.get("status") == "partiel":
                    new = "partiel"
                note = f"{today} — sonde {'réelle' if res['kind'] == 'reel' else 'à vide'} : {res['outcome']} — {res['detail']}"
            elif tool and suite_of_tool.get(tool) in suites:
                res = suites[suite_of_tool[tool]]
                new = _SUITE_STATUS[res["outcome"]]
                note = f"{today} — {suite_of_tool[tool]} : {res['passed']}/{res['total']}"
            elif _test_module(n.get("test", "")) in suites:
                suite = _test_module(n["test"])
                res = suites[suite]
                new = _SUITE_STATUS[res["outcome"]]
                note = f"{today} — {suite} : {res['passed']}/{res['total']}"
            if new is None:
                continue
            if n.get("status") != new or n.get("verified") != note:
                if n.get("status") != new:
                    changes.append(f"{gid}/{n['id']}: {n.get('status', 'non évalué')} → {new}")
                n["status"], n["verified"] = new, note
                touched = True
        if touched:
            g["updated"] = today
            cap.save(g)
    return changes


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    quick, write = "--quick" in argv, "--write" in argv
    today = date.today().isoformat()
    print("\n  Sondes des outils")
    probes = run_probes()
    suites = {}
    if not quick:
        print("\n  Selftests")
        suites = run_suites()
    report = {"date": today, "probes": probes, "suites": suites}
    changes = []
    if write:
        changes = apply(probes, suites, today)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    counts = {}
    for r in probes.values():
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    print(f"\n  sondes : {counts}" + (f" · suites : {sum(1 for s in suites.values() if s['outcome'] == 'ok')}/{len(suites)} vertes" if suites else ""))
    if write:
        print(f"  {len(changes)} état(s) changé(s) dans les graphes ; rapport : {REPORT.relative_to(REPO)}")
        for c in changes:
            print(f"    {c}")
    bad = [t for t, r in probes.items() if r["outcome"] == "echec"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
