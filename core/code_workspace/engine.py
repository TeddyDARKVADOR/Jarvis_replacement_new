"""
engine.py — the autonomous development loop.

    1. Planification   the model proposes; paths and commands are validated
    2. Création        checkpoint, then every file written through the sandbox
    3. Vérification    syntax checks + the project's tests, really run, + a real
                       browser for a web page (webcheck.py); on failure:
                       locate → checkpoint → fix → run again, a bounded number of
                       times; the same error twice means it is going in circles,
                       so it stops and asks for help instead of burning attempts
    4. Présentation    a summary built from the journal — the tests that really
                       passed, the acceptance criteria they prove, the errors
                       really left, a static analysis (lint.py, informative), how
                       to launch it — then the preview opens by itself (a web
                       page in the Studio's preview window, a script's output)

THE VERDICT IS EARNED, NEVER ASSUMED
    validé        functional checks ran and passed, every criterion is proven
    partiel       checks passed, but some criterion has no test that proves it,
                  or the program crashed when launched
    non vérifié   nothing functional ran (no test command, tests refused…):
                  syntax alone proves nothing
    bloqué        a check still fails after the fix attempts

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

from . import acceptance, diagnose, lint as lint_mod, llm as llm_mod, webcheck
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
STATUS_TEXT = {
    "ok": "validé : tous les contrôles passent et chaque critère est prouvé",
    "partial": "partiellement vérifié",
    "unverified": "non vérifié : aucun contrôle fonctionnel n'a tourné",
    "blocked": "bloqué",
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
            j.add("info", f"Modèle de code : {llm_mod.describe(self.llm)}")
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
        return acceptance.normalize(plan)

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
        for k in ("acceptance", "web_checks"):           # a change keeps the criteria it did not touch
            if not isinstance(plan.get(k), list) or not plan[k]:
                plan[k] = pmap.get(k) or []
        plan = self._validate_plan(plan, existing=True)
        self.plan = plan
        self._record_plan(plan)

    def _record_plan(self, plan: dict) -> None:
        j = self.ctx.journal
        j.add("plan", f"Plan proposé par le modèle : {plan.get('summary', '')}",
              source="model", features=plan.get("features") or [],
              files=[f["path"] for f in plan["files"]],
              test_command=plan.get("test_command", ""), run_command=plan.get("run_command", ""),
              stack=plan.get("stack") or [], acceptance=plan.get("acceptance") or [])
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
        done: set[str] = set()              # a file listed in two tasks is written once
        for task in self.ctx.tasks.all():
            self._check()
            self.ctx.tasks.set(task["id"], "doing")
            failed = []
            for path in task["files"]:
                self._check()
                f = by_path.get(path)
                if not f or path in done:
                    continue
                done.add(path)
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
    def _is_web(self) -> bool:
        return self.plan.get("kind") == "web" or str(self.plan.get("entry_point") or "").endswith((".html", ".htm"))

    def _checks(self) -> list[dict]:
        """Run every check; one dict per check: kind, ok (True / False / None = not run)."""
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
        check = {"name": "syntaxe", "kind": "syntax", "ok": not bad, "output": "\n".join(bad), "timed_out": False}
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
            out.append(self._run_check(cmd, smoke))
        elif not self._is_web():
            self.ctx.journal.add("test", "Aucune commande de test définie : le projet ne sera pas "
                                         "déclaré validé.", ok=None)
        if out[-1]["ok"] is False:
            return out
        if self._is_web():
            out.append(self._web_check())
        return out

    def _run_check(self, cmd: str, smoke: bool) -> dict:
        r = self.studio.run_command(self.project, cmd, ask_blocking=False, stop_event=self.stop_event,
                                    timeout=int(self.options.get("command_timeout", 120)) if not smoke else 25)
        self._check()
        name = "exécution" if smoke else "tests"
        if r.get("refused") or r.get("pending"):
            why = r.get("refused") or "en attente de confirmation"
            self.ctx.journal.add("test", f"Contrôle non lancé ({cmd}) : {why}", name=name, ok=None)
            return {"name": cmd, "kind": "run" if smoke else "tests", "ok": None,
                    "output": f"non lancé : {why}", "timed_out": False}
        output = r.get("output", "")
        if smoke and r.get("timed_out"):
            # Still running after 25 s: it started, which proves nothing more.
            self.ctx.journal.add("test", f"• exécution : {cmd} — toujours active après 25 s, arrêtée ; "
                                         "démarrage constaté, comportement non vérifié",
                                 name=name, command=cmd, ok=None)
            return {"name": cmd, "kind": "run", "ok": None, "output": output, "timed_out": True,
                    "note": "toujours active après 25 s : démarrage constaté, comportement non vérifié"}
        ok = bool(r.get("ok"))
        results = acceptance.test_results(output) if not smoke else {}
        self.ctx.journal.add("test", f"{'✓' if ok else '✗'} {name} : {cmd}"
                                     + ("" if ok else f" — {diagnose.headline(output)}"),
                             name=name, command=cmd, ok=ok, code=r.get("code"),
                             summary=_test_summary(output))
        return {"name": cmd, "kind": "run" if smoke else "tests", "ok": ok, "output": output,
                "timed_out": bool(r.get("timed_out")), "code": r.get("code"), "results": results}

    def _web_check(self) -> dict:
        self._step("contrôle dans un vrai navigateur")
        pdir = self.ws.project_dir(self.project)
        entry = str(self.plan.get("entry_point") or "")
        entry = entry if entry.endswith((".html", ".htm")) else "index.html"
        checks = self.plan.get("web_checks") or []
        self.ctx.journal.add("command", f"$ navigateur headless → {entry}"
                                        + (f" ({len(checks)} parcours)" if checks else ""),
                             command="navigateur", state="start")
        report = webcheck.run(pdir, entry, checks, self.ctx.meta / "reports", stop_event=self.stop_event)
        self._check()
        output = webcheck.as_output(report)
        for line in output.splitlines():
            self.ctx.journal.terminal(line, "navigateur")
        ok = report.get("ok")
        if ok is None:
            self.ctx.journal.add("test", f"Contrôle navigateur non lancé : {report.get('unavailable')}",
                                 name="navigateur", ok=None)
        else:
            passed = sum(1 for c in report.get("checks") or [] if c.get("ok"))
            self.ctx.journal.add("test", f"{'✓' if ok else '✗'} navigateur : page chargée"
                                         + (f", {passed}/{len(report.get('checks') or [])} parcours" if checks else "")
                                         + ("" if ok else f" — {diagnose.headline(output)}"),
                                 name="navigateur", ok=ok, blocked=len(report.get("blocked") or []))
        return {"name": "navigateur", "kind": "web", "ok": ok, "output": output, "timed_out": False,
                "report": report}

    def _verify_and_fix(self) -> dict:
        self._phase("verification", "contrôles")
        seen: dict[str, int] = {}
        history: list[str] = []
        max_attempts = self._max_attempts()
        tid = self.ctx.tasks.add("Vérifier et corriger (tests réels)")
        self.ctx.tasks.set(tid, "doing")
        checks: list[dict] = []
        covered = False
        while True:
            self._check()
            checks = self._checks()
            failing = next((c for c in checks if c["ok"] is False), None)
            if failing is None and not covered:
                covered = True                      # once: the criteria are the user's, the tests prove them
                if self._cover_missing(checks):
                    continue
            if failing is None:
                ran = any(c["ok"] for c in checks if c["kind"] != "syntax")
                self.ctx.tasks.set(tid, "done", "tous les contrôles lancés passent" if ran
                                   else "aucun contrôle fonctionnel n'a tourné")
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

    def _coverage(self, checks: list[dict]) -> list[dict]:
        tests: dict[str, str] = {}
        for c in checks:
            tests.update(c.get("results") or {})
        web_report = next((c.get("report") for c in checks if c["kind"] == "web"), None) or {}
        web = {c["id"]: c for c in web_report.get("checks") or [] if c.get("id")}
        return acceptance.coverage(self.plan.get("acceptance") or [], tests, web)

    def _cover_missing(self, checks: list[dict]) -> bool:
        """Checks are green but some criterion has no test naming it: ask once for
        the missing tests (tests/ only), then the loop runs everything again."""
        if not any(c["kind"] == "tests" and c["ok"] for c in checks):
            return False                            # no test suite ran: nothing to extend
        missing = [c for c in self._coverage(checks) if c["verify"] == "test" and c["status"] == "unverified"]
        if not missing:
            return False
        ws, project = self.ws, self.project
        self._step("tests manquants pour les critères")
        self.ctx.journal.add("info", f"{len(missing)} critère(s) sans test qui le prouve ("
                                     + ", ".join(c["id"] for c in missing) + ") : demande des tests manquants")
        self.ctx.checkpoints.create("avant tests manquants")
        tests, code = [], []
        for f in ws.list_files(project):
            p = f["path"]
            if Path(p).suffix not in TEXT_EXT or p.endswith(".md"):
                continue
            c = ws.read_optional(project, p) or ""
            (tests if llm_mod.is_test_file(p) else code).append(f"--- {p} ---\n{c[:4000]}")
        proposal = llm_mod.cover(self.llm, self.plan.get("title") or project, missing,
                                 "\n".join(tests), "\n".join(code))
        self._check()
        changed = []
        for f in (proposal.get("files") or [])[:3]:
            path = str((f or {}).get("path", "")) if isinstance(f, dict) else ""
            if not llm_mod.is_test_file(path) or not path.replace("\\", "/").startswith("tests/") \
                    or not isinstance(f.get("content"), str):
                self.ctx.journal.add("error", f"Fichier refusé pour les tests manquants : « {path} » "
                                              "(seul tests/*.py peut être écrit à cette étape)")
                continue
            try:
                self.studio.write_file(project, path, f["content"], reason="tests des critères")
                changed.append(path)
            except SandboxError as e:
                self.ctx.journal.add("error", f"Tests refusés pour {path} : {e}")
        return bool(changed)

    def _fix(self, failing: dict, history: list[str]) -> str:
        ws, project = self.ws, self.project
        files = ws.list_files(project)
        paths = [f["path"] for f in files]
        kind = diagnose.classify(failing["output"], failing.get("timed_out", False))
        if (failing.get("report") or {}).get("page_errors"):
            kind = "runtime"                 # a JS exception, not a wrong expectation
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
        web = failing.get("kind") == "web"
        wanted = [p for p in [path] if p]
        if web:
            wanted += [p for p in paths if Path(p).suffix in (".html", ".htm", ".js", ".mjs", ".css")][:8]
        else:
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
                               "\n\n".join(contents), history,
                               web_checks=self.plan.get("web_checks") if web else None)
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
    def _verdict(self, report: dict, criteria: list[dict], run: dict | None) -> tuple[str, str]:
        """(status, why) — see THE VERDICT at the top of this file."""
        if not report.get("ok"):
            return "blocked", report.get("blocked", "")
        checks = report.get("checks", [])
        if not any(c["ok"] for c in checks if c["kind"] != "syntax"):
            skipped = [c.get("note") or c["output"] for c in checks if c["kind"] != "syntax" and c["ok"] is None]
            return "unverified", (skipped[0][:200] if skipped else "aucune commande de test, aucun contrôle navigateur")
        missing = [c for c in criteria if c["status"] in ("unverified", "failed")]
        if missing:
            return "partial", (f"{len(missing)} critère(s) sans preuve : "
                               + ", ".join(f"{c['id']} {c['criterion'][:60]}" for c in missing[:4]))
        if run and run.get("crashed"):
            return "partial", "les tests passent, mais le programme lancé s'est arrêté sur une erreur"
        return "ok", ""

    def _launch(self, report: dict) -> tuple[dict | None, dict | None]:
        """After the checks: the program's own run (scripts), the preview (web), the button (gui)."""
        kind = self.plan.get("kind") or ""
        cmd = self.plan.get("run_command") or ""
        run = preview = None
        if self._is_web():
            preview = {"kind": "web", "opened": False}
            if report.get("ok") and self.options.get("auto_preview", True):
                try:
                    r = self.studio.preview(self.project)
                    if r.get("url"):
                        preview["url"] = r["url"]
                        preview["opened"] = self.studio.open_preview(self.project, r["url"])
                except Exception as e:                       # the preview is a convenience, not a check
                    self.ctx.journal.add("error", f"Aperçu non démarré : {e}")
        elif kind == "gui" and cmd:
            preview = {"kind": "gui", "command": cmd}         # a window of its own: only on request
        elif kind in ("script", "cli") and cmd and report.get("ok"):
            run = self._run_program(report, cmd)
        return run, preview

    def _run_program(self, report: dict, cmd: str) -> dict | None:
        smoke = next((c for c in report.get("checks", []) if c["kind"] == "run"), None)
        run = None
        if smoke is not None:
            run = {"command": cmd, "ok": smoke["ok"], "code": smoke.get("code"),
                   "timed_out": smoke.get("timed_out"), "output": smoke["output"][-4000:], "source": "contrôle"}
        elif self.options.get("auto_run_scripts", True):
            self._step("lancement du programme")
            r = self.studio.run_command(self.project, cmd, stop_event=self.stop_event, timeout=30)
            if not r.get("refused") and not r.get("pending"):
                run = {"command": cmd, "ok": bool(r.get("ok")), "code": r.get("code"),
                       "timed_out": bool(r.get("timed_out")), "output": (r.get("output") or "")[-4000:],
                       "source": "lancement"}
        if run is not None:
            run["crashed"] = (not run["ok"] and not run["timed_out"]
                              and "Traceback (most recent call last)" in run["output"])
        return run

    def _summary_checks(self, checks: list[dict], web_report: dict) -> list[dict]:
        rows = []
        for c in checks:
            detail = ""
            if c["kind"] == "tests":
                detail = _test_summary(c["output"])
            elif c["kind"] == "web" and web_report.get("checks"):
                done = sum(1 for x in web_report["checks"] if x.get("ok"))
                detail = f"{done}/{len(web_report['checks'])} parcours"
            rows.append({"name": c["name"], "kind": c["kind"], "ok": c["ok"], "tests": detail,
                         "headline": c.get("note") or (diagnose.headline(c["output"]) if c["ok"] is not True else "")})
        return rows

    def _present(self, report: dict) -> None:
        self._phase("presentation", "bilan")
        ws, project = self.ws, self.project
        checks = report.get("checks", [])
        web_report = next((c.get("report") for c in checks if c["kind"] == "web"), None) or {}
        criteria = self._coverage(checks)

        self._step("analyse du code")
        analysis = lint_mod.run(ws.project_dir(project))
        self.ctx.journal.add("test", f"Analyse statique (informative) : {lint_mod.headline(analysis)}",
                             name="analyse", ok=analysis.get("ok"), informative=True)
        self.studio.update_map(project, self.plan)          # the preview reads the map
        run, preview = self._launch(report)
        status, why = self._verdict(report, criteria, run)

        files = ws.list_files(project)
        touched = self.ctx.journal.files_touched()
        launch = self.plan.get("run_command") or ""
        if self._is_web():
            launch = "Aperçu du Studio (bouton APERÇU) ou ouvrir " + (self.plan.get("entry_point") or "index.html")
        deps = scan_dependencies(ws, project)
        summary = {
            "status": status,
            "status_text": STATUS_TEXT[status],
            "why": why,
            "title": self.plan.get("title") or project,
            "kind": self.plan.get("kind") or "",
            "files": len(files),
            "written": [t["path"] for t in touched][:60],
            "checks": self._summary_checks(checks, web_report),
            "criteria": criteria,
            "analysis": {"ok": analysis.get("ok"), "count": analysis.get("count", 0),
                         "headline": lint_mod.headline(analysis), "findings": analysis.get("findings", [])[:20]},
            "browser": {"blocked": web_report.get("blocked") or [], "title": web_report.get("title", ""),
                        "screenshot": bool(web_report.get("screenshot"))} if web_report else None,
            "run": run,
            "preview": preview,
            "remaining": report.get("blocked", ""),
            "launch": launch,
            "fix_attempts": report.get("attempts", 0),
            "dependencies": deps,
            "folder": str(ws.project_dir(project)),
            "seconds": round(time.time() - self.started),
        }
        self.result = summary
        self.studio.update_map(project, self.plan, last_run=summary, dependencies=deps)
        self.ctx.journal.add("summary", f"Bilan : {summary['title']} — {STATUS_TEXT[status]}"
                                        + (f" ({why})" if why and status != "ok" else ""),
                             **{k: v for k, v in summary.items() if k not in ("run", "analysis")})
        self.phase = "bloque" if status == "blocked" else "termine"
        self._announce(summary, report)

    def _announce(self, summary: dict, report: dict) -> None:
        status = summary["status"]
        state = {True: "OK", False: "FAILED", None: "NOT RUN"}
        tests_txt = "; ".join(f"{c['name']}: {state[c['ok']]}" for c in summary["checks"])
        criteria = summary["criteria"]
        proven = sum(1 for c in criteria if c["status"] == "passed")
        preview = summary.get("preview") or {}
        self.studio.say(
            "The Code Studio finished a job. Tell the user in their own language, in 2 or 3 short "
            f"sentences, the REAL outcome: project '{summary['title']}' in {summary['folder']}, "
            f"{summary['files']} files. Verdict: {status} ({summary['status_text']}"
            + (f" — {summary['why']}" if summary["why"] else "") + f"). Checks: {tests_txt or 'none'}. "
            + (f"Acceptance criteria proven by a real check: {proven}/{len(criteria)}. " if criteria else "")
            + ("Never call it validated or fully working: the verdict is not 'ok'. " if status != "ok" else "")
            + (f"Remaining problem: {report.get('blocked')}. Ask them for help with it. " if status == "blocked" else "")
            + ("The preview window is open on the HUD. " if preview.get("opened") else "")
            + f"How to launch: {summary['launch'] or 'see the README'}. The full report is in the HUD's Studio "
              "panel. Do not invent anything beyond these facts.")


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
