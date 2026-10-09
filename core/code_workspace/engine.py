"""
engine.py — the autonomous development loop.

    1. Planification   the model proposes; paths and commands are validated
    2. Création        checkpoint, then every file written through the sandbox
    3. Vérification    syntax checks + the project's tests, really run; on failure:
                       locate → checkpoint → fix → run again, a bounded number of
                       times; the same error twice means it is going in circles,
                       so it stops and asks for help instead of burning attempts
    4. Présentation    a summary built from the journal — the tests that really
                       passed, the errors really left, how to launch it

Runs on its own thread. `stop()` is honoured between every step and kills the
running command. Risky steps (installing a package, validating the plan when
the user asked to) wait for the HUD's CONFIRM, never for the model's say-so.
"""

from __future__ import annotations

import ast
import json
import sys
import threading
import time
import traceback
from pathlib import Path

from . import diagnose, llm as llm_mod
from .runner import CommandRefused, classify, clean_env, parse
from .sandbox import SandboxError, safe_project_name

PHASES = {
    "planification": "Planification",
    "creation": "Création",
    "verification": "Vérification et corrections",
    "presentation": "Présentation",
    "termine": "Terminé",
    "bloque": "Bloqué — aide demandée",
    "arrete": "Arrêté",
    "erreur": "Erreur",
}
TEXT_EXT = {".py", ".js", ".mjs", ".ts", ".html", ".htm", ".css", ".json", ".md", ".txt",
            ".toml", ".cfg", ".ini", ".yml", ".yaml", ".csv", ".svg", ".xml", ".sh", ".bat"}


class Stopped(Exception):
    pass


def _stdlib() -> set[str]:
    return set(getattr(sys, "stdlib_module_names", ()))


def scan_dependencies(ws, project: str) -> list[str]:
    """Third-party imports actually present in the code (Python) + package.json."""
    files = ws.list_files(project)
    local = {Path(f["path"]).parts[0].removesuffix(".py") for f in files}
    found: set[str] = set()
    std = _stdlib()
    for f in files:
        if f["path"].endswith(".py"):
            src = ws.read_optional(project, f["path"]) or ""
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for n in names:
                    top = n.split(".")[0]
                    if top and top not in std and top not in local and top != "__future__":
                        found.add(top)
    pkg = ws.read_optional(project, "package.json")
    if pkg:
        try:
            data = json.loads(pkg)
            for k in ("dependencies", "devDependencies"):
                found.update(f"npm:{n}" for n in (data.get(k) or {}))
        except ValueError:
            pass
    return sorted(found)


class StudioJob:
    def __init__(self, studio, project: str, request: str, mode: str = "create",
                 llm=None, options: dict | None = None) -> None:
        self.studio = studio
        self.project = project
        self.request = request.strip()
        self.mode = mode
        self.llm = llm
        self.options = dict(options or {})
        self.stop_event = threading.Event()
        self.phase = "planification"
        self.step = ""
        self.attempt = 0
        self.started = time.time()
        self.finished: float | None = None
        self.result: dict = {}
        self.plan: dict = {}
        self._thread: threading.Thread | None = None

    # -- lifecycle --
    def start(self) -> None:
        self._thread = threading.Thread(target=self._main, daemon=True,
                                        name=f"studio-{self.project}")
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self.stop_event.set()
        self.studio.runner.stop_current()

    def info(self) -> dict:
        return {"project": self.project, "mode": self.mode, "phase": self.phase,
                "phase_label": PHASES.get(self.phase, self.phase), "step": self.step,
                "attempt": self.attempt, "max_attempts": self._max_attempts(),
                "started": self.started, "finished": self.finished, "running": self.running,
                "request": self.request[:300], "result": self.result}

    # -- helpers --
    @property
    def ctx(self):
        return self.studio.project_ctx(self.project)

    @property
    def ws(self):
        return self.studio.workspace()

    def _max_attempts(self) -> int:
        return max(1, min(int(self.options.get("max_fix_attempts", 4)), 10))

    def _check(self) -> None:
        if self.stop_event.is_set():
            raise Stopped()

    def _phase(self, phase: str, step: str = "") -> None:
        self._check()
        self.phase, self.step = phase, step
        self.ctx.journal.add("phase", PHASES.get(phase, phase) + (f" — {step}" if step else ""),
                             phase=phase)
        self.studio.log(f"Studio · {self.project} — {PHASES.get(phase, phase)}")

    def _step(self, text: str) -> None:
        self._check()
        self.step = text

    # -- the loop --
    def _main(self) -> None:
        j = None
        try:
            j = self.ctx.journal
            j.add("info", f"Demande : {self.request[:400]}", mode=self.mode)
            if hasattr(self.llm, "on_wait"):
                self.llm.on_wait = lambda secs, why: j.add(
                    "info", f"⏳ {why} — nouvelle tentative dans {int(secs)} s")
                self.llm.should_stop = self.stop_event.is_set
            if self.mode == "create":
                self._plan_new()
            else:
                self._plan_change()
            self._create()
            report = self._verify_and_fix()
            self._present(report)
        except Exception as e:
            if not isinstance(e, Stopped) and not self.stop_event.is_set():
                self._failed(j, e)
                return
            self.phase = "arrete"
            self.studio.runner.stop_current()
            if j:
                j.add("info", "Travail arrêté à votre demande. Les fichiers restent tels quels ; "
                              "restaurez un point de sauvegarde si besoin.")
            self.result = {"status": "stopped"}
            self.studio.say(f"Tell the user, in their language and in one short sentence, that work "
                            f"on the project '{self.project}' was stopped as requested.")
        finally:
            self.finished = time.time()
            self.studio.job_finished(self)

    def _failed(self, j, e: Exception) -> None:
        traceback.print_exc()
        self.phase = "erreur"
        if j:
            j.add("error", f"Le travail s'est interrompu : {e}")
        self.result = {"status": "error", "error": str(e)}
        self.studio.say(f"Tell the user briefly, in their language, that the Code Studio hit an "
                        f"error on '{self.project}': {str(e)[:200]}")

    # 1 ── plan ──────────────────────────────────────────────────────────────
    def _validate_plan(self, plan: dict, existing: bool) -> dict:
        ws = self.ws
        files, seen = [], set()
        for f in plan.get("files") or []:
            if not isinstance(f, dict):
                continue
            path = str(f.get("path", "")).strip()
            try:
                ws.resolve(self.project, path, for_write=False)
                from .sandbox import split_relative
                path = "/".join(split_relative(path))
            except SandboxError as e:
                self.ctx.journal.add("error", f"Fichier du plan refusé « {path} » : {e}")
                continue
            if path in seen:
                continue
            seen.add(path)
            action = str(f.get("action") or ("modify" if existing and
                                               ws.read_optional(self.project, path) is not None
                                               else "create")).lower()
            files.append({"path": path, "purpose": str(f.get("purpose", ""))[:400],
                          "action": action if action in ("create", "modify", "delete") else "create"})
        if not files:
            raise RuntimeError("Le plan ne contient aucun fichier valide.")
        plan["files"] = files
        tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
        covered = {p for t in tasks for p in (t.get("files") or [])}
        orphans = [f["path"] for f in files if f["path"] not in covered]
        if orphans:
            tasks.append({"title": "Fichiers complémentaires", "files": orphans})
        plan["tasks"] = [{"title": t.get("title", "Tâche"),
                          "files": [p for p in (t.get("files") or []) if p in seen]} for t in tasks]
        for key in ("test_command", "run_command"):
            cmd = str(plan.get(key) or "").strip()
            if cmd:
                try:
                    v = classify(parse(cmd), self.ws.project_dir(self.project, create=True))
                    if v.decision == "deny" and "n'est pas un fichier du projet" not in v.reason:
                        self.ctx.journal.add("error", f"Commande du plan refusée ({key}) : {v.reason}")
                        plan[key] = ""
                except CommandRefused as e:
                    self.ctx.journal.add("error", f"Commande du plan refusée ({key}) : {e}")
                    plan[key] = ""
        return plan

    def _plan_new(self) -> None:
        self._phase("planification", "découpage de la demande")
        plan = llm_mod.plan_new(self.llm, self.request)
        self._check()
        plan = self._validate_plan(plan, existing=False)
        self.plan = plan
        self._record_plan(plan)

    def _plan_change(self) -> None:
        self._phase("planification", "analyse du projet existant")
        pmap = self.studio.project_map(self.project)
        plan = llm_mod.plan_change(self.llm, self.request, pmap, self.ws.list_files(self.project))
        self._check()
        for k in ("title", "kind", "stack", "entry_point", "features"):
            plan.setdefault(k, pmap.get(k))
        plan.setdefault("project_name", self.project)
        for k in ("run_command", "test_command"):
            if not plan.get(k):
                plan[k] = pmap.get(k, "")
        plan = self._validate_plan(plan, existing=True)
        self.plan = plan
        self._record_plan(plan)

    def _record_plan(self, plan: dict) -> None:
        j = self.ctx.journal
        j.add("plan", f"Plan proposé par le modèle : {plan.get('summary', '')}",
              source="model", features=plan.get("features") or [],
              files=[f["path"] for f in plan["files"]],
              test_command=plan.get("test_command", ""), run_command=plan.get("run_command", ""),
              stack=plan.get("stack") or [])
        self.ctx.tasks.replace(plan["tasks"])
        self.studio.update_map(self.project, plan, request=self.request)
        if self.options.get("confirm_plan"):
            self._step("validation du plan")
            detail = (f"{len(plan['files'])} fichiers, {len(plan['tasks'])} tâches. "
                      + "; ".join(t["title"] for t in plan["tasks"][:6]))
            if not self.studio.ask("studio-plan", f"Studio : valider le plan de « {self.project} »",
                                   detail, self.stop_event):
                self.ctx.journal.add("blocked", "Plan non validé : rien n'a été écrit.")
                raise Stopped()

    # 2 ── create ────────────────────────────────────────────────────────────
    def _create(self) -> None:
        self._phase("creation", "point de sauvegarde")
        self.ctx.checkpoints.create("avant " + ("création" if self.mode == "create" else "modification"))
        by_path = {f["path"]: f for f in self.plan["files"]}
        written: dict[str, str] = {}
        for task in self.ctx.tasks.all():
            self._check()
            self.ctx.tasks.set(task["id"], "doing")
            failed = []
            for path in task["files"]:
                self._check()
                f = by_path.get(path)
                if not f:
                    continue
                self._step(f"écriture de {path}")
                try:
                    if f["action"] == "delete":
                        self.studio.delete_file(self.project, path, reason="plan")
                        continue
                    current = self.ws.read_optional(self.project, path) if f["action"] == "modify" else None
                    content = llm_mod.write_file(self.llm, self.plan, path, f["purpose"],
                                                 self._context(written, path), current)
                    self._check()
                    self.studio.write_file(self.project, path, content, reason="création")
                    written[path] = content
                except (Stopped, llm_mod.LLMError):
                    raise                       # no model: every next file would fail the same way
                except Exception as e:
                    failed.append(path)
                    self.ctx.journal.add("error", f"Écriture de {path} impossible : {e}", path=path)
            self.ctx.tasks.set(task["id"], "blocked" if failed else "done",
                               f"échec : {', '.join(failed)}" if failed else "")
        self._install_dependencies()

    def _context(self, written: dict[str, str], current_path: str) -> str:
        parts, budget = [], 14000
        for p, c in reversed(list(written.items())):
            if p == current_path:
                continue
            snippet = c if len(c) < 3500 else c[:2500] + "\n…\n" + c[-800:]
            block = f"--- {p} ---\n{snippet}\n"
            if len(block) > budget:
                break
            parts.append(block)
            budget -= len(block)
        if self.mode == "change":
            for f in self.ws.list_files(self.project):
                if f["path"] in written or f["path"] == current_path or Path(f["path"]).suffix not in TEXT_EXT:
                    continue
                c = self.ws.read_optional(self.project, f["path"]) or ""
                block = f"--- {f['path']} (existant) ---\n{c[:2000]}\n"
                if len(block) > budget:
                    break
                parts.append(block)
                budget -= len(block)
        return "\n".join(parts)

    def _install_dependencies(self, extra: list[str] | None = None) -> bool:
        deps = [d for d in (self.plan.get("dependencies") or []) if isinstance(d, str)]
        deps += list(extra or [])
        py = sorted({d.strip() for d in deps if d.strip() and not d.startswith("npm:")
                     and all(ch.isalnum() or ch in "-_.[]=<>~!" for ch in d.strip())})
        if not py:
            return False
        self._step("installation des dépendances")
        argv = ["python", "-m", "pip", "install", *py]
        return self.studio.run_command(self.project, argv, ask_blocking=True,
                                       stop_event=self.stop_event).get("ok", False)

    # 3 ── verify / fix ──────────────────────────────────────────────────────
    def _checks(self) -> list[dict]:
        """Run every check; return one dict per check, failures included."""
        out: list[dict] = []
        ws, project = self.ws, self.project
        files = ws.list_files(project)
        # syntax, in-process: compile() never executes anything
        bad = []
        for f in files:
            path = f["path"]
            if path.endswith(".py"):
                src = ws.read_optional(project, path)
                if src is None:
                    continue
                try:
                    compile(src, path, "exec", dont_inherit=True)
                except SyntaxError as e:
                    bad.append(f'  File "{path}", line {e.lineno}\n{type(e).__name__}: {e.msg}')
            elif path.endswith(".json"):
                try:
                    json.loads(ws.read_optional(project, path) or "")
                except ValueError as e:
                    bad.append(f'  File "{path}", line {getattr(e, "lineno", 1)}\nJSONDecodeError: {e}')
        check = {"name": "syntaxe", "ok": not bad, "output": "\n".join(bad), "timed_out": False}
        self.ctx.journal.add("test", f"{'✓' if not bad else '✗'} Vérification de la syntaxe "
                                     f"({sum(1 for f in files if f['path'].endswith(('.py', '.json')))} fichiers)",
                             name="syntaxe", ok=not bad, headline=diagnose.headline(check["output"]))
        out.append(check)
        if bad:
            return out
        self._check()
        cmd = (self.plan.get("test_command") or "").strip()
        smoke = False
        if not cmd and (self.plan.get("kind") in ("script", "cli")) and self.plan.get("run_command"):
            cmd, smoke = self.plan["run_command"], True
        if cmd:
            r = self.studio.run_command(project, cmd, ask_blocking=False, stop_event=self.stop_event,
                                        timeout=int(self.options.get("command_timeout", 120)) if not smoke else 25)
            self._check()
            if r.get("refused"):
                self.ctx.journal.add("test", f"Tests non lancés : {r['refused']}", name=cmd, ok=None)
            else:
                ok = bool(r.get("ok")) or (smoke and r.get("timed_out"))
                name = "exécution" if smoke else "tests"
                self.ctx.journal.add("test", f"{'✓' if ok else '✗'} {name} : {cmd}"
                                             + ("" if ok else f" — {diagnose.headline(r.get('output', ''))}"),
                                     name=name, command=cmd, ok=ok, code=r.get("code"),
                                     summary=_test_summary(r.get("output", "")))
                out.append({"name": cmd, "ok": ok, "output": r.get("output", ""),
                            "timed_out": bool(r.get("timed_out")) and not smoke})
        else:
            self.ctx.journal.add("test", "Aucune commande de test définie pour ce projet.", ok=None)
        return out

    def _verify_and_fix(self) -> dict:
        self._phase("verification", "contrôles")
        seen: dict[str, int] = {}
        history: list[str] = []
        max_attempts = self._max_attempts()
        tid = self.ctx.tasks.add("Vérifier et corriger (tests réels)")
        self.ctx.tasks.set(tid, "doing")
        checks: list[dict] = []
        while True:
            self._check()
            checks = self._checks()
            failing = next((c for c in checks if not c["ok"]), None)
            if failing is None:
                self.ctx.tasks.set(tid, "done", "tous les contrôles passent")
                return {"ok": True, "checks": checks, "attempts": self.attempt}
            sig = diagnose.signature(failing["output"])
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] >= 3 or (seen[sig] >= 2 and self.attempt >= 2):
                msg = ("Je tourne en rond : la même erreur revient après correction — "
                       f"{diagnose.headline(failing['output'])}")
                self.ctx.journal.add("blocked", msg, signature=sig)
                self.ctx.tasks.set(tid, "blocked", "même erreur répétée")
                return {"ok": False, "blocked": msg, "checks": checks, "attempts": self.attempt}
            if self.attempt >= max_attempts:
                msg = f"{max_attempts} tentatives de correction épuisées."
                self.ctx.journal.add("blocked", msg)
                self.ctx.tasks.set(tid, "blocked", msg)
                return {"ok": False, "blocked": msg, "checks": checks, "attempts": self.attempt}
            self.attempt += 1
            self._step(f"correction {self.attempt}/{max_attempts}")
            history.append(self._fix(failing, history))

    def _fix(self, failing: dict, history: list[str]) -> str:
        ws, project = self.ws, self.project
        files = ws.list_files(project)
        paths = [f["path"] for f in files]
        kind = diagnose.classify(failing["output"], failing.get("timed_out", False))
        path, line = diagnose.locate(failing["output"], paths)
        self.ctx.journal.add("error", f"Diagnostic : {kind}"
                                      + (f" dans {path} ligne {line}" if path else "")
                                      + f" — {diagnose.headline(failing['output'])}",
                             kind=kind, path=path, line=line)
        if kind == "dependency":
            mod = diagnose.missing_module(failing["output"])
            if mod and mod not in paths and f"{mod}.py" not in paths and mod not in _stdlib():
                if self._install_dependencies([mod]):
                    return f"tentative {self.attempt}: installation de {mod}"
        self.ctx.checkpoints.create(f"avant correction {self.attempt}")
        wanted = [p for p in [path] if p]
        wanted += [p for p in paths if p.startswith("tests/") or p.endswith(".py")][:6]
        wanted += [p for p in paths if Path(p).suffix in (".js", ".html")][:3]
        contents, seen = [], set()
        for p in wanted:
            if p in seen or Path(p).suffix not in TEXT_EXT:
                continue
            seen.add(p)
            c = ws.read_optional(project, p) or ""
            numbered = "\n".join(f"{i+1:4d}| {l}" for i, l in enumerate(c.splitlines()[:600]))
            contents.append(f"--- {p} ---\n{numbered}")
        located = f"The error points at {path}, line {line}." if path else "No project frame located."
        proposal = llm_mod.fix(self.llm, self.plan.get("title") or project, failing["name"], kind,
                               diagnose.excerpt(failing["output"]), located, files,
                               "\n\n".join(contents), history)
        self._check()
        if proposal.get("give_up"):
            self.ctx.journal.add("blocked", f"Le correcteur demande de l'aide : {proposal['give_up']}")
        diag = str(proposal.get("diagnosis", ""))[:300]
        changed = []
        for f in (proposal.get("files") or [])[:3]:
            if not isinstance(f, dict) or not isinstance(f.get("content"), str):
                continue
            try:
                self.studio.write_file(project, str(f.get("path", "")), f["content"],
                                       reason=f"correction {self.attempt}")
                changed.append(str(f.get("path")))
            except SandboxError as e:
                self.ctx.journal.add("error", f"Correction refusée pour {f.get('path')} : {e}")
        dep = str(proposal.get("needs_dependency") or "").strip()
        if dep and not changed:
            self._install_dependencies([dep])
        self.ctx.journal.add("info", f"Correction {self.attempt} : {diag or 'sans explication'} "
                                     f"({', '.join(changed) or 'aucun fichier modifié'})")
        return f"attempt {self.attempt}: {diag} — changed {changed} — error was: " \
               f"{diagnose.headline(failing['output'])}"

    # 4 ── present ───────────────────────────────────────────────────────────
    def _present(self, report: dict) -> None:
        self._phase("presentation", "bilan")
        ws, project = self.ws, self.project
        files = ws.list_files(project)
        touched = self.ctx.journal.files_touched()
        tests = [c for c in report.get("checks", [])]
        launch = self.plan.get("run_command") or ""
        if self.plan.get("kind") == "web" or (self.plan.get("entry_point", "").endswith(".html")):
            launch = "Aperçu du Studio (bouton APERÇU) ou ouvrir " + (self.plan.get("entry_point") or "index.html")
        deps = scan_dependencies(ws, project)
        summary = {
            "status": "ok" if report.get("ok") else "blocked",
            "title": self.plan.get("title") or project,
            "files": len(files),
            "written": [t["path"] for t in touched][:60],
            "checks": [{"name": c["name"], "ok": c["ok"], "headline": diagnose.headline(c["output"]) if not c["ok"] else "",
                        "tests": _test_summary(c["output"])} for c in tests],
            "remaining": report.get("blocked", ""),
            "launch": launch,
            "fix_attempts": report.get("attempts", 0),
            "dependencies": deps,
            "folder": str(ws.project_dir(project)),
            "seconds": round(time.time() - self.started),
        }
        self.result = summary
        self.studio.update_map(project, self.plan, last_run=summary, dependencies=deps)
        status_txt = "terminé, tous les contrôles passent" if report.get("ok") else \
            f"terminé avec un problème restant : {report.get('blocked', '')}"
        self.ctx.journal.add("summary", f"Bilan : {summary['title']} — {status_txt}", **summary)
        self.phase = "termine" if report.get("ok") else "bloque"
        tests_txt = "; ".join(f"{c['name']}: {'OK' if c['ok'] else 'FAILED'}" for c in summary["checks"])
        self.studio.say(
            "The Code Studio finished a job. Tell the user in their own language, in 2 or 3 short "
            f"sentences, the REAL outcome: project '{summary['title']}' in {summary['folder']}, "
            f"{summary['files']} files, checks: {tests_txt or 'none'}. "
            + (f"Remaining problem: {report.get('blocked')}. Ask them for help with it. " if not report.get("ok") else "")
            + f"How to launch: {launch or 'see the README'}. The full report is in the HUD's Studio panel. "
              "Do not invent anything beyond these facts.")


def _test_summary(output: str) -> str:
    import re
    m = re.search(r"Ran (\d+) tests? in [\d.]+s\s*\n\s*\n?(OK.*|FAILED.*)", output or "")
    if m:
        return f"{m.group(1)} tests — {m.group(2).strip()}"
    m = re.search(r"=+ ([^=]*(passed|failed)[^=]*) =+", output or "")
    return m.group(1).strip() if m else ""


def new_project_name(studio, plan_name: str | None, request: str) -> str:
    base = plan_name or "projet"
    try:
        name = safe_project_name(base)
    except SandboxError:
        name = "projet"
    existing = set(studio.workspace().projects())
    if name not in existing:
        return name
    for i in range(2, 100):
        cand = f"{name}_{i}"[:48]
        if cand not in existing:
            return cand
    return f"{name}_{int(time.time())}"[:48]
