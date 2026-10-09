"""
core/code_workspace/selftest.py — proves the Code Studio before JARVIS uses it.

    python -m core.code_workspace.selftest

No Gemini key, no network, no HUD: the model is a scripted fake, the root is a
temporary folder. Same shape as context/selftest.py.

What it checks:
  1-4   the sandbox refuses every way out (absolute, drive, UNC, ADS, '..',
        reserved names, trailing dots, .studio, junctions, hard links) and
        the root refuses the JARVIS repo, a drive root, the profile, Windows
  5-7   the terminal's policy (no shell, allow / confirm / deny, pip into
        _deps), secrets stripped from the environment, the Job Object
  8-10  real execution: exit codes, output streamed to the journal, timeout
        and stop kill the process tree
  11-12 checkpoints: changed files, diff, restore (itself undoable); the
        journal survives a torn line
  13-16 the autonomous loop with a fake model: plan → write → failing test →
        diagnose → fix → passing test → real summary; going in circles stops
        early; stop is honoured; an escaping plan writes nothing outside
  17-19 the tool: manual write is undoable; the action is discovered by the
        real loader and refuses on the Oracle server; the HUD cannot set the
        root inside the JARVIS repo
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.code_workspace import diagnose                                   # noqa: E402
from core.code_workspace.checkpoints import Checkpoints                    # noqa: E402
from core.code_workspace.journal import Journal                            # noqa: E402
from core.code_workspace.runner import (                                   # noqa: E402
    CommandRefused, Runner, _Job, classify, clean_env, parse,
)
from core.code_workspace.sandbox import (                                  # noqa: E402
    SandboxError, Workspace, validate_root,
)
from core.code_workspace.service import Studio                             # noqa: E402

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


# The temp folder lives under %LOCALAPPDATA%, which a real root may not. The
# rule is tested on its own (check 4); here it is lifted for the fixtures.
_saved_env = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "APPDATA", "GEMINI_API_KEY")}
os.environ["LOCALAPPDATA"] = "Z:\\__nowhere_local"
os.environ["APPDATA"] = "Z:\\__nowhere_roaming"
os.environ["GEMINI_API_KEY"] = "secret-should-not-leak"

TMP = Path(tempfile.mkdtemp(prefix="jarvis-studio-test-")).resolve()
ROOT = TMP / "ws"
OUTSIDE = TMP / "outside"
OUTSIDE.mkdir()
(OUTSIDE / "secret.txt").write_text("outside secret", encoding="utf-8")
WS = Workspace(ROOT)
WS.project_dir("demo", create=True)


def refuses(fn, *a, **k) -> bool:
    try:
        fn(*a, **k)
    except (SandboxError, CommandRefused):
        return True
    return False


@check("1 la sandbox refuse toutes les sorties de chemin")
def _():
    bad = ["C:/Windows/win.ini", "/etc/passwd", "\\\\server\\share\\x", "\\\\?\\C:\\x", "\\\\.\\PhysicalDrive0",
           "D:relative", "notes.txt:hidden", "..", "../x", "a/../../x", "CON", "nul.txt", "com1.py",
           "a.py.", "a /b.py", ".studio/journal.jsonl", "~/x", "a\x00b", "src/<x>.py"]
    leaked = [b for b in bad if not refuses(WS.resolve, "demo", b, for_write=True)]
    assert not leaked, f"acceptés à tort : {leaked}"
    ok = WS.resolve("demo", "src/app.py", for_write=True)
    assert ok.parent.parent.name == "demo"
    return f"{len(bad)} chemins refusés, src/app.py accepté"


@check("2 une jonction ou un lien dans le projet n'ouvre pas de porte")
def _():
    link = WS.project_dir("demo") / "door"
    made = False
    if sys.platform == "win32":
        import _winapi
        try:
            _winapi.CreateJunction(str(OUTSIDE), str(link))
            made = True
        except OSError:
            pass
    if not made:
        try:
            os.symlink(OUTSIDE, link, target_is_directory=True)
            made = True
        except OSError:
            return "lien non créable sur ce système (ignoré)"
    try:
        assert refuses(WS.read_text, "demo", "door/secret.txt"), "lecture via la jonction acceptée"
        assert refuses(WS.write_text, "demo", "door/evil.txt", "x"), "écriture via la jonction acceptée"
        assert not (OUTSIDE / "evil.txt").exists()
        assert all(not f["path"].startswith("door") for f in WS.list_files("demo")), "listing suit la jonction"
    finally:
        os.rmdir(link) if link.is_dir() else link.unlink()
    return "jonction refusée en lecture, écriture et listing"


@check("3 un lien physique n'est ni lu ni écrit au travers")
def _():
    target = WS.project_dir("demo") / "hard.txt"
    try:
        os.link(OUTSIDE / "secret.txt", target)
    except OSError:
        return "liens physiques non créables ici (ignoré)"
    assert refuses(WS.read_text, "demo", "hard.txt"), "lecture d'un lien physique acceptée"
    WS.write_text("demo", "hard.txt", "replaced")
    assert (OUTSIDE / "secret.txt").read_text(encoding="utf-8") == "outside secret", "fichier extérieur modifié"
    target.unlink()
    return "lecture refusée, écriture détache le lien"


@check("4 la racine refuse le dépôt JARVIS, un lecteur, le profil, Windows")
def _():
    os.environ["LOCALAPPDATA"] = _saved_env["LOCALAPPDATA"] or "C:\\Users\\x\\AppData\\Local"
    try:
        bad = [str(BASE_DIR), str(BASE_DIR / "sub"), str(BASE_DIR.parent), "C:\\", str(Path.home()),
               os.environ.get("SystemRoot", "C:\\Windows"), os.environ["LOCALAPPDATA"] + "\\X", "relative\\dir",
               "\\\\server\\share\\ws"]
        leaked = [b for b in bad if not refuses(validate_root, b)]
    finally:
        os.environ["LOCALAPPDATA"] = "Z:\\__nowhere_local"
    assert not leaked, f"racines acceptées à tort : {leaked}"
    assert validate_root(str(ROOT)) == ROOT
    return f"{len(bad)} racines refusées"


@check("5 le terminal : pas de shell, et allow / confirm / deny")
def _():
    pdir = WS.project_dir("demo")
    (pdir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    for c in ["python main.py | more", "python main.py && del x", "python main.py > out.txt", "a; b", "x `y`"]:
        assert refuses(parse, c), f"shell accepté : {c}"
    cases = {
        "python main.py": "allow", "python -m unittest discover -s tests -t . -v": "allow",
        "pytest -q": "allow", "python missing.py": "deny", "python ../x.py": "deny",
        "python C:/Windows/x.py": "deny", "python -m pip install requests": "confirm",
        "python -m pip install --user requests": "deny", "python -m pip list": "allow",
        "python -c print(1)": "confirm", "python -m http.server": "deny", "powershell -c x": "deny",
        "cmd /c dir": "deny", "curl http://x": "deny", "python -m venv .venv": "confirm",
    }
    wrong = {}
    for c, want in cases.items():
        try:
            got = classify(parse(c), pdir).decision
        except CommandRefused:
            got = "deny"
        if got != want:
            wrong[c] = got
    assert not wrong, f"mauvaises décisions : {wrong}"
    v = classify(parse("python -m pip install requests"), pdir)
    assert v.argv[-2:] == ["--target", "_deps"], v.argv
    return f"{len(cases)} commandes classées, pip vers _deps/"


@check("6 l'environnement d'exécution ne contient aucun secret")
def _():
    env = clean_env(WS.project_dir("demo"), TMP / "tmp")
    leaked = [k for k in env if "KEY" in k.upper() or "TOKEN" in k.upper() or "SECRET" in k.upper()]
    assert not leaked, leaked
    assert env["TEMP"].startswith(str(TMP))
    return f"{len(env)} variables, TEMP dans l'espace"


@check("7 le Job Object Windows est créé (mémoire, processus, arbre)")
def _():
    if sys.platform != "win32":
        return "hors Windows (ignoré)"
    job = _Job()
    ok = job.handle is not None
    job.close()
    assert ok, "CreateJobObject a échoué"
    return "job créé avec limites"


RUNNER = Runner()


def _run(code: str, timeout=20, stop=None):
    pdir = WS.project_dir("demo")
    (pdir / "t.py").write_text(code, encoding="utf-8")
    j = Journal(WS.meta_dir("demo"))
    return RUNNER.run([sys.executable, "t.py"], pdir, clean_env(pdir, TMP / "tmp"), j,
                      timeout=timeout, stop_event=stop), j


@check("8 une commande réelle : code de sortie et sortie dans le journal")
def _():
    r, j = _run("import os\nprint('bonjour')\nprint(os.environ.get('GEMINI_API_KEY'))\nraise SystemExit(3)\n")
    assert r.code == 3 and "bonjour" in r.output, (r.code, r.output)
    assert "secret-should-not-leak" not in r.output, "la clé a fuité dans le processus"
    assert any("bonjour" in t["t"] for t in j.terminal_tail()), "sortie absente du terminal"
    assert any(e["kind"] == "command" and e.get("data", {}).get("code") == 3 for e in j.recent())
    return f"code {r.code} en {r.seconds} s, clé absente"


@check("9 le délai maximal tue le processus")
def _():
    t0 = time.monotonic()
    r, _ = _run("import time\ntime.sleep(30)\n", timeout=2)
    assert r.timed_out and time.monotonic() - t0 < 12, (r.timed_out, time.monotonic() - t0)
    return f"arrêté après {r.seconds} s"


@check("10 le bouton arrêt tue l'arbre de processus")
def _():
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    code = ("import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "time.sleep(60)\n")
    t0 = time.monotonic()
    r, _ = _run(code, timeout=60, stop=stop)
    assert r.stopped and time.monotonic() - t0 < 12, (r.stopped, time.monotonic() - t0)
    return f"arrêté en {r.seconds} s"


@check("11 points de sauvegarde : modifications, diff, restauration réversible")
def _():
    j = Journal(WS.meta_dir("cp"))
    WS.project_dir("cp", create=True)
    WS.write_text("cp", "a.py", "x = 1\n")
    cps = Checkpoints(WS, "cp", j)
    first = cps.create("initial")
    WS.write_text("cp", "a.py", "x = 2\n")
    WS.write_text("cp", "b.py", "y = 1\n")
    changed = {c["path"]: c["op"] for c in cps.changed_since(first["id"])}
    assert changed == {"a.py": "modified", "b.py": "added"}, changed
    assert "+x = 2" in cps.diff("a.py", first["id"])
    r = cps.restore(first["id"])
    assert WS.read_text("cp", "a.py") == "x = 1\n" and not (WS.project_dir("cp") / "b.py").exists()
    cps.restore(r["safety"])
    assert WS.read_text("cp", "a.py") == "x = 2\n", "la restauration n'est pas annulable"
    return f"{len(cps.list())} points, restauration annulée avec succès"


@check("12 le journal est persistant et survit à une ligne coupée")
def _():
    meta = WS.meta_dir("jr")
    j = Journal(meta)
    j.add("file", "+ créé x.py", path="x.py", op="create")
    with (meta / "journal.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "kind": "fi')
    j2 = Journal(meta)
    assert len(j2.recent()) == 1 and j2.files_touched()[0]["path"] == "x.py"
    return "rechargé, ligne coupée ignorée"


# ── the autonomous loop, with a scripted model ─────────────────────────────

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = ("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")


class FakeLLM:
    def __init__(self, plan_files=None, fix_content=FIXED, delay=0.0):
        self.plan_files = plan_files
        self.fix_content = fix_content
        self.delay = delay
        self.calls = {"plan": 0, "write": 0, "fix": 0}

    def complete(self, prompt, json_mode=False):
        time.sleep(self.delay)
        if "Plan a NEW project" in prompt:
            self.calls["plan"] += 1
            files = self.plan_files or [
                {"path": "calc.py", "purpose": "add"}, {"path": "tests/__init__.py", "purpose": ""},
                {"path": "tests/test_calc.py", "purpose": "tests"}, {"path": "README.md", "purpose": "doc"}]
            return json.dumps({
                "project_name": "calc", "title": "Calculatrice", "summary": "Additionne.",
                "kind": "library", "stack": ["python"], "entry_point": "calc.py", "run_command": "",
                "test_command": "python -m unittest discover -s tests -t . -v", "dependencies": [],
                "features": ["addition"],
                "tasks": [{"title": "Logique", "files": [f["path"] for f in files[:1]]},
                          {"title": "Tests et doc", "files": [f["path"] for f in files[1:]]}],
                "files": files})
        if "Write the COMPLETE content of:" in prompt:
            self.calls["write"] += 1
            path = prompt.split("Write the COMPLETE content of:")[1].split("\n")[0].strip()
            return {"calc.py": BUGGY, "tests/test_calc.py": TEST, "README.md": "# Calc\n",
                    "tests/__init__.py": ""}.get(path, "# x\n")
        if "You are fixing the project" in prompt:
            self.calls["fix"] += 1
            return json.dumps({"diagnosis": "soustraction au lieu d'addition",
                               "files": [{"path": "calc.py", "content": self.fix_content}]})
        raise AssertionError("prompt inattendu")


def _studio(llm) -> Studio:
    cfg = {"root": str(ROOT), "max_fix_attempts": 4, "command_timeout": 60}
    st = Studio(config_get=lambda: cfg, config_save=cfg.update)
    st.llm_factory = lambda: llm
    said: list[str] = []
    st.say = said.append                  # type: ignore[method-assign]
    st.said = said                        # type: ignore[attr-defined]
    return st


def _wait(st: Studio, limit=120.0):
    t0 = time.monotonic()
    while st.job is not None and st.job.running and time.monotonic() - t0 < limit:
        time.sleep(0.2)
    return st.last_job


@check("13 boucle autonome : plan, fichiers, test en échec, correction, test vert, bilan réel")
def _():
    llm = FakeLLM()
    st = _studio(llm)
    r = st.start_job("create", "Crée une calculatrice qui additionne")
    job = _wait(st)
    assert job is not None and job.phase == "termine", (job and job.phase, job and job.result)
    name = r["project"]
    assert WS.read_text(name, "calc.py") == FIXED
    evs = st.project_ctx(name).journal.recent(400)
    kinds = [e["kind"] for e in evs]
    for k in ("plan", "file", "command", "test", "error", "checkpoint", "summary"):
        assert k in kinds, f"événement {k} absent"
    fails = [e for e in evs if e["kind"] == "test" and e.get("data", {}).get("ok") is False]
    passes = [e for e in evs if e["kind"] == "test" and e.get("data", {}).get("ok") is True
              and e.get("data", {}).get("name") == "tests"]
    assert fails and passes, "il faut un échec réel puis un succès réel"
    files_on_disk = {f["path"] for f in WS.list_files(name)}
    journaled = {e["data"]["path"] for e in evs if e["kind"] == "file"}
    assert journaled == files_on_disk, f"journal {journaled} ≠ disque {files_on_disk}"
    tasks = st.project_ctx(name).tasks.all()
    assert all(t["status"] == "done" for t in tasks), tasks
    assert job.result["checks"][-1]["ok"] and "1 tests" in job.result["checks"][-1]["tests"]
    assert st.said and "Calculatrice" in st.said[-1]
    return f"{llm.calls}, {len(evs)} événements, {len(st.project_ctx(name).checkpoints.list())} points"


@check("14 tourner en rond : même erreur → arrêt et demande d'aide avant la limite")
def _():
    llm = FakeLLM(fix_content=BUGGY)
    st = _studio(llm)
    st.start_job("create", "calc qui ne sera jamais réparée")
    job = _wait(st)
    assert job.phase == "bloque", job.phase
    assert llm.calls["fix"] < 4, f"{llm.calls['fix']} corrections : la boucle n'a pas été détectée"
    assert any(e["kind"] == "blocked" for e in st.project_ctx(job.project).journal.recent())
    return f"bloqué après {llm.calls['fix']} corrections : {job.result.get('remaining', '')[:60]}"


@check("15 le bouton arrêt interrompt le travail")
def _():
    llm = FakeLLM(delay=0.6)
    st = _studio(llm)
    st.start_job("create", "calc lente")
    time.sleep(1.0)
    st.stop()
    job = _wait(st, 20)
    assert job.phase == "arrete", job.phase
    return f"arrêté en phase {job.phase}"


@check("16 un plan qui veut sortir n'écrit rien dehors")
def _():
    evil = [{"path": "../evil.py", "purpose": ""}, {"path": "C:/evil.py", "purpose": ""},
            {"path": "\\\\?\\C:\\evil.py", "purpose": ""}, {"path": ".studio/x.py", "purpose": ""},
            {"path": "calc.py", "purpose": ""}, {"path": "tests/__init__.py", "purpose": ""},
            {"path": "tests/test_calc.py", "purpose": ""}]
    st = _studio(FakeLLM(plan_files=evil))
    st.start_job("create", "plan malveillant")
    job = _wait(st)
    assert not (ROOT / "evil.py").exists() and not (TMP / "evil.py").exists()
    errs = [e for e in st.project_ctx(job.project).journal.recent(400)
            if e["kind"] == "error" and "refusé" in e["msg"]]
    assert len(errs) >= 4, [e["msg"] for e in errs]
    return f"{len(errs)} fichiers du plan refusés, projet {job.phase}"


@check("17 une écriture manuelle est journalisée et annulable")
def _():
    from core import undo
    st = _studio(FakeLLM())
    st.current_project = "demo"
    st.tool({"action": "write_file", "project": "demo", "path": "note.md", "content": "v1"})
    st.tool({"action": "write_file", "project": "demo", "path": "note.md", "content": "v2"})
    undo.undo_last()
    assert WS.read_text("demo", "note.md") == "v1"
    undo.undo_last()
    assert not (WS.project_dir("demo") / "note.md").exists()
    out = st.tool({"action": "write_file", "project": "demo", "path": "../x.md", "content": "x"})
    assert out.startswith("Studio refused"), out
    return "v2 → v1 → absent ; '../' refusé"


@check("18 l'action est découverte par le vrai chargeur et refuse sur le serveur Oracle")
def _():
    from core.action_loader import discover_actions
    reg = discover_actions(BASE_DIR / "actions", logger=lambda _m: None)
    assert reg.has("code_studio"), "code_studio non découvert"
    import importlib
    mod = importlib.import_module("actions.code_workspace")

    class HeadlessUI:
        local_mode = False
    out = mod.code_studio({"action": "status"}, player=HeadlessUI())
    assert "only works when JARVIS runs on this PC" in out, out
    return f"{len(reg.names())} actions chargées, refus côté serveur"


@check("19 le HUD ne peut pas placer la racine dans le dépôt JARVIS")
def _():
    st = _studio(FakeLLM())
    r = st.hud("studio.set_root", {"path": str(BASE_DIR / "workspace")})
    assert "error" in r, r
    s = st.hud("studio.status", {"project": "demo"})
    for k in ("root", "projects", "tasks", "events", "terminal", "checkpoints", "safety"):
        assert k in s, f"{k} absent du statut"
    return f"refusé : {r['error'][:60]}"


@check("20 quota Gemini : délai respecté, et une panne du modèle arrête le travail sans insister")
def _():
    from core.code_workspace.llm import LLMError, _retry_delay
    assert _retry_delay("429 ... 'retryDelay': '37s' ...", 5) == 38
    assert _retry_delay("no hint", 7) == 7

    class Broken(FakeLLM):
        def complete(self, prompt, json_mode=False):
            if "Write the COMPLETE content of:" in prompt:
                self.calls["write"] += 1
                raise LLMError("Quota Gemini épuisé")
            return super().complete(prompt, json_mode)
    llm = Broken()
    st = _studio(llm)
    st.start_job("create", "calc sans quota")
    job = _wait(st)
    assert job.phase == "erreur" and "Quota" in job.result.get("error", ""), (job.phase, job.result)
    assert llm.calls["write"] == 1, f"{llm.calls['write']} appels : le moteur a insisté"
    return "retryDelay lu, 1 seul appel avant arrêt"


@check("21 Groq : meilleur modèle disponible, limite 429 respectée, JSON, clé refusée")
def _():
    from core.code_workspace import llm as L

    class Resp:
        def __init__(self, code, data=None, headers=None):
            self.status_code, self._d, self.headers = code, data or {}, headers or {}
            self.ok, self.text = code < 400, json.dumps(data or {})

        def json(self):
            return self._d

    class FakeHTTP:
        def __init__(self, posts):
            self.posts, self.bodies, self.auth = list(posts), [], None

        def get(self, url, headers=None, timeout=None):
            self.auth = headers["Authorization"]
            return Resp(200, {"data": [{"id": "whisper-large-v3"}, {"id": "llama-3.3-70b-versatile"},
                                       {"id": "openai/gpt-oss-120b"}]})

        def post(self, url, headers=None, json=None, timeout=None):
            self.bodies.append(json)
            return self.posts.pop(0)

    ok = Resp(200, {"choices": [{"message": {"content": '{"a": 1}'}}]})
    http = FakeHTTP([Resp(429, {}, {"retry-after": "0.2"}), ok])
    waits = []
    g = L.GroqLLM(key="gsk_test", http=http)
    g.on_wait = lambda s, why: waits.append(why)
    assert g.model == "openai/gpt-oss-120b", g.model
    assert L.parse_json(g.complete("plan", json_mode=True)) == {"a": 1}
    assert waits == ["limite Groq atteinte"] and http.bodies[-1]["response_format"] == {"type": "json_object"}
    assert http.auth == "Bearer gsk_test"
    bad = L.GroqLLM(key="gsk_bad", http=FakeHTTP([Resp(401)]), model="openai/gpt-oss-120b")
    bad._model = "x"
    try:
        bad.complete("x")
        raise AssertionError("401 non signalé")
    except L.LLMError as e:
        assert "401" in str(e)
    picked = L.make_llm({"provider": "gemini"})
    assert isinstance(picked, L.StudioLLM)
    os.environ["GROQ_API_KEY"] = "gsk_env"
    try:
        assert isinstance(L.make_llm({}), L.GroqLLM) or L.groq_key() != "gsk_env"
    finally:
        os.environ.pop("GROQ_API_KEY", None)
    return f"modèle {g.model}, 429 puis OK, 401 refusé"


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    width = max(len(n) for n, _, _ in _results) + 2
    for name, ok, detail in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)} {detail}")
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n  {passed}/{len(_results)} passed")
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    RUNNER.stop_all()
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
