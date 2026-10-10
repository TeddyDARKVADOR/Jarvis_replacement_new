"""
service.py — the one Studio object both the tool and the HUD talk to.

    actions/code_workspace.py   (the model)   → Studio.tool(params, player)
    local_hud.LocalBackend      (the HUD)     → Studio.hud(op, data)

Both go through the same methods, so the HUD can never show something the
tool did not do, and the tool can never do something the HUD cannot see.

What only the HUD can do: choose the workspace root and change the options.
The model can read them, not move its own fence.

The preview window (client_desktop/hud/preview.py) is the HUD's: the host sets
`preview_opener(project, url, title)`; without a HUD, a preview is started but
nothing opens by itself.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unicodedata

from .checkpoints import Checkpoints
from .journal import Journal
from .runner import CommandRefused, Runner, classify, clean_env, free_port, is_admin, parse
from .sandbox import SandboxError, Workspace, default_root, safe_project_name, validate_root
from .tasks import TaskList

CONFIG_NS = "code_studio"
DEFAULT_OPTIONS = {"max_fix_attempts": 4, "command_timeout": 120, "confirm_plan": False,
                   "auto_preview": True, "auto_run_scripts": True, "provider": "auto", "local_model": "",
                   "escalate": True}
_BOOL_OPTIONS = ("confirm_plan", "auto_preview", "auto_run_scripts", "escalate")
#: Who writes the code — see llm.make_llm.
PROVIDERS = ("auto", "groq", "groq+local", "local", "gemini")

SAFETY_NOTE = ("Les outils de fichiers de JARVIS sont strictement limités à ce dossier. "
               "En revanche, un programme qu'il exécute tourne avec les droits de votre compte "
               "Windows : pour une isolation totale, utilisez Windows Sandbox ou une machine virtuelle.")


class _Ctx:
    def __init__(self, ws: Workspace, project: str) -> None:
        meta = ws.meta_dir(project)
        self.meta = meta
        self.journal = Journal(meta)
        self.tasks = TaskList(meta, self.journal)
        self.checkpoints = Checkpoints(ws, project, self.journal)


class Studio:
    def __init__(self, config_get=None, config_save=None) -> None:
        self._get = config_get or _config_get
        self._save = config_save or _config_save
        self._lock = threading.RLock()
        self._ws: Workspace | None = None
        self._ws_root: str | None = None
        self._ctx: dict[str, _Ctx] = {}
        self.runner = Runner()
        self.job = None
        self.last_job = None
        self.current_project: str | None = None
        self.player = None
        self.llm_factory = None          # tests inject a fake model here
        self.escalation_factory = None   # ... and the stronger one, for escalation
        #: Set by the HUD host: opens the preview window (any thread). Returns
        #: True when a window really opened.
        self.preview_opener = None

    # ── configuration ───────────────────────────────────────────────────────
    def root_setting(self) -> str:
        return str(self._get().get("root") or default_root())

    def options(self) -> dict:
        cfg = self._get()
        out = dict(DEFAULT_OPTIONS)
        for k in DEFAULT_OPTIONS:
            if k in cfg:
                out[k] = cfg[k]
        out["max_fix_attempts"] = max(1, min(int(out["max_fix_attempts"]), 10))
        out["command_timeout"] = max(10, min(int(out["command_timeout"]), 900))
        for k in _BOOL_OPTIONS:
            out[k] = bool(out[k])
        out["provider"] = str(out["provider"] or "auto").lower()
        if out["provider"] not in PROVIDERS:
            out["provider"] = "auto"
        out["local_model"] = str(out["local_model"] or "")
        return out

    def set_root(self, path: str) -> dict:
        if self.job is not None and self.job.running:
            raise SandboxError("Un travail est en cours : arrêtez-le avant de changer de dossier.")
        root = validate_root(path)
        Workspace(root)                                   # creates + re-validates
        self._save({"root": str(root)})
        with self._lock:
            self._ws, self._ws_root, self._ctx = None, None, {}
        return {"ok": True, "root": str(root)}

    def set_options(self, data: dict) -> dict:
        clean = {}
        if "max_fix_attempts" in data:
            clean["max_fix_attempts"] = max(1, min(int(data["max_fix_attempts"]), 10))
        if "command_timeout" in data:
            clean["command_timeout"] = max(10, min(int(data["command_timeout"]), 900))
        for k in _BOOL_OPTIONS:
            if k in data:
                clean[k] = bool(data[k])
        if "provider" in data:
            provider = str(data["provider"] or "auto").lower()
            if provider not in PROVIDERS:
                raise SandboxError(f"Fournisseur inconnu « {provider} ».")
            clean["provider"] = provider
        if "local_model" in data:
            model = str(data["local_model"] or "").strip()
            if model and not re.fullmatch(r"[\w.:/@-]{1,80}", model):
                raise SandboxError("Nom de modèle local invalide.")
            clean["local_model"] = model
        if clean:
            self._save(clean)
        return {"ok": True, "options": self.options()}

    def workspace(self) -> Workspace:
        root = self.root_setting()
        with self._lock:
            if self._ws is None or self._ws_root != root:
                self._ws = Workspace(root)
                self._ws_root = root
                self._ctx = {}
            return self._ws

    def project_ctx(self, project: str) -> _Ctx:
        name = safe_project_name(project)
        ws = self.workspace()
        with self._lock:
            if name not in self._ctx:
                self._ctx[name] = _Ctx(ws, name)
            return self._ctx[name]

    def _existing(self, project: str | None) -> str:
        if not project:
            project = self.current_project
        if not project:
            raise SandboxError("Quel projet ? Aucun projet n'est sélectionné.")
        name = safe_project_name(project)
        if not self.workspace().project_dir(name).is_dir():
            # tolerate a spoken name: case / accents / spaces
            want = _slug(project)
            for p in self.workspace().projects():
                if _slug(p) == want:
                    name = p
                    break
            else:
                raise SandboxError(f"Le projet « {project} » n'existe pas dans l'espace de travail.")
        self.current_project = name
        return name

    # ── the outside world ───────────────────────────────────────────────────
    def bind(self, player) -> None:
        if player is not None:
            self.player = player

    def log(self, text: str) -> None:
        p = self.player
        if p is not None and hasattr(p, "write_log"):
            try:
                p.write_log(f"SYS: {text}")
            except Exception:
                pass

    def say(self, instruction: str) -> None:
        p = self.player
        fn = getattr(p, "request_say", None) if p is not None else None
        if callable(fn):
            try:
                fn(instruction)
            except Exception:
                pass

    def ask(self, key: str, title: str, detail: str, stop_event=None) -> bool:
        """Blocking HUD confirmation, for the background job only."""
        from core import confirm as gate
        deadline = time.monotonic() + gate.TIMEOUT_SECONDS
        while gate.pending_title() and time.monotonic() < deadline:   # never replace another banner
            if stop_event is not None and stop_event.is_set():
                return False
            time.sleep(0.5)
        decided = threading.Event()
        answer = {"ok": False}

        def accept() -> str:
            answer["ok"] = True
            decided.set()
            return "OK"

        msg = gate.request(key, title, detail, accept)
        if not msg.startswith("[CONFIRMATION_PENDING]"):
            return False
        self.say(f"Tell the user, in their language, in one short sentence, that the Code Studio "
                 f"needs their confirmation on the HUD: {title}.")
        end = time.monotonic() + gate.TIMEOUT_SECONDS + 3
        while time.monotonic() < end:
            if decided.wait(0.5):
                return answer["ok"]
            if stop_event is not None and stop_event.is_set():
                return False
            if not gate.pending_title() and not decided.is_set():
                return False                      # cancelled or expired
        return False

    # ── project map ─────────────────────────────────────────────────────────
    def project_map(self, project: str) -> dict:
        p = self.project_ctx(project).meta / "project.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {"name": project}

    def update_map(self, project: str, plan: dict | None = None, request: str | None = None,
                   last_run: dict | None = None, dependencies: list | None = None) -> dict:
        data = self.project_map(project)
        data.setdefault("name", project)
        data.setdefault("created", time.time())
        if plan:
            for k in ("title", "summary", "kind", "stack", "entry_point", "run_command",
                      "test_command", "features", "acceptance", "web_checks"):
                if plan.get(k):
                    data[k] = plan[k]
        if request:
            data.setdefault("history", []).append({"ts": time.time(), "request": request[:500]})
            data["history"] = data["history"][-30:]
        if last_run is not None:
            data["last_run"] = last_run
        if dependencies is not None:
            data["dependencies"] = dependencies
        data["files"] = [f["path"] for f in self.workspace().list_files(project)][:300]
        data["updated"] = time.time()
        (self.project_ctx(project).meta / "project.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data

    # ── journaled file operations (the ONLY way files change) ───────────────
    def write_file(self, project: str, rel: str, content: str, reason: str = "manuel") -> dict:
        ws = self.workspace()
        path, previous = ws.write_text(project, rel, content)
        rel_clean = ws.relpath(project, path)
        lines = str(content).count("\n") + 1
        op = "create" if previous is None else "modify"
        self.project_ctx(project).journal.add(
            "file", f"{'+ créé' if op == 'create' else '~ modifié'} {rel_clean} ({lines} lignes) — {reason}",
            path=rel_clean, op=op, lines=lines, reason=reason)
        if reason == "manuel":
            self._push_undo(project, rel_clean, previous)
        return {"path": rel_clean, "op": op, "lines": lines}

    def delete_file(self, project: str, rel: str, reason: str = "manuel") -> dict:
        ws = self.workspace()
        previous = ws.delete(project, rel)
        self.project_ctx(project).journal.add("file", f"- supprimé {rel} — {reason}",
                                              path=rel, op="delete", reason=reason)
        if reason == "manuel":
            self._push_undo(project, rel, previous, deleted=True)
        return {"path": rel, "op": "delete"}

    def _push_undo(self, project: str, rel: str, previous: str | None, deleted: bool = False) -> None:
        try:
            from core.undo import push_undo
        except Exception:
            return

        def undo() -> str:
            if previous is None:
                self.delete_file(project, rel, reason="annulation")
                return f"{rel} retiré."
            self.write_file(project, rel, previous, reason="annulation")
            return f"{rel} rétabli."

        push_undo(f"Studio : {'suppression' if deleted else 'écriture'} de {rel}", undo)

    # ── commands ────────────────────────────────────────────────────────────
    def run_command(self, project: str, command, ask_blocking: bool = False, stop_event=None,
                    timeout: int | None = None) -> dict:
        ws = self.workspace()
        ctx = self.project_ctx(project)
        pdir = ws.project_dir(project, create=True)
        try:
            argv = parse(command)
            verdict = classify(argv, pdir)
        except CommandRefused as e:
            ctx.journal.add("error", f"Commande refusée : {e}")
            return {"ok": False, "refused": str(e)}
        shown = command if isinstance(command, str) else " ".join(command)
        if verdict.decision == "deny":
            ctx.journal.add("error", f"Commande refusée « {shown} » : {verdict.reason}")
            return {"ok": False, "refused": verdict.reason}
        env = clean_env(pdir, ctx.meta / "tmp")
        timeout = timeout or self.options()["command_timeout"]

        def execute(stop=None) -> dict:
            try:
                r = self.runner.run(verdict.argv, pdir, env, ctx.journal, timeout=timeout,
                                    stop_event=stop, label=shown)
            except CommandRefused as e:
                ctx.journal.add("error", str(e))
                return {"ok": False, "refused": str(e)}
            return {"ok": r.ok, "code": r.code, "output": r.output, "seconds": r.seconds,
                    "timed_out": r.timed_out, "stopped": r.stopped, "command": shown}

        if verdict.decision == "confirm":
            ctx.journal.add("confirm", f"Confirmation demandée : {verdict.reason}", command=shown)
            if ask_blocking:
                if not self.ask("studio-cmd", f"Studio : {verdict.reason}", f"Projet {project} — {shown}",
                                stop_event):
                    ctx.journal.add("confirm", f"Refusé ou sans réponse : {shown}", command=shown, ok=False)
                    return {"ok": False, "refused": "non confirmé par l'utilisateur"}
                ctx.journal.add("confirm", f"Confirmé : {shown}", command=shown, ok=True)
                return execute(stop_event)
            from core import confirm as gate

            def later() -> str:
                ctx.journal.add("confirm", f"Confirmé : {shown}", command=shown, ok=True)
                r = execute()
                self.say(f"Tell the user briefly, in their language, the result of the confirmed "
                         f"command '{shown}': {'success' if r.get('ok') else 'failure'}"
                         f" (exit code {r.get('code')}).")
                return "terminé" if r.get("ok") else f"échec (code {r.get('code')})"

            msg = gate.request("studio-cmd", f"Studio : {verdict.reason}", f"Projet {project} — {shown}",
                               lambda: (threading.Thread(target=later, daemon=True).start(), "lancé")[1])
            return {"ok": False, "pending": True, "message": msg}
        return execute(stop_event)

    # ── jobs ────────────────────────────────────────────────────────────────
    def _llm(self):
        if callable(self.llm_factory):
            return self.llm_factory()
        from .llm import make_llm
        return make_llm(self._get())

    def _escalation_llm(self):
        """The stronger coder a stuck LOCAL model hands its fixes to: Groq, else Gemini."""
        if callable(self.escalation_factory):
            return self.escalation_factory()
        if callable(self.llm_factory):
            return None                               # a scripted model: nothing real to escalate to
        cfg = self._get()
        if str(cfg.get("provider") or "auto").lower() not in ("local", "ollama"):
            return None
        from .llm import GroqLLM, StudioLLM, groq_key
        if groq_key():
            return GroqLLM(cfg.get("groq_model") or None)
        try:
            from memory.config_manager import get_gemini_key
            if get_gemini_key():
                return StudioLLM(cfg.get("model") or None)
        except Exception:
            pass
        return None

    def start_job(self, mode: str, request: str, project: str | None = None) -> dict:
        from .engine import StudioJob, new_project_name
        with self._lock:
            if self.job is not None and self.job.running:
                raise SandboxError(f"Un travail est déjà en cours sur « {self.job.project} ». "
                                   "Attendez la fin ou arrêtez-le.")
            if not request.strip():
                raise SandboxError("Décrivez ce qu'il faut faire.")
            if is_admin():
                raise SandboxError("JARVIS tourne en administrateur : le Studio refuse de travailler "
                                   "dans ce mode. Relancez JARVIS sans les droits administrateur.")
            if mode == "create":
                name = new_project_name(self, _slug(project or request), request)
                self.workspace().project_dir(name, create=True)
            else:
                name = self._existing(project)
            job = StudioJob(self, name, request, mode=mode, llm=self._llm(), options=self.options(),
                            escalate=self._escalation_llm)
            self.job = job
            self.current_project = name
            job.start()
        self.log(f"Studio — travail lancé sur « {name} »")
        return {"ok": True, "project": name, "folder": str(self.workspace().project_dir(name))}

    def job_finished(self, job) -> None:
        with self._lock:
            self.last_job = job
            if self.job is job:
                self.job = None
        self.log(f"Studio — « {job.project} » : {job.phase}")

    def stop(self) -> dict:
        job = self.job
        if job is not None and job.running:
            job.stop()
            stopped = True
        else:
            stopped = self.runner.stop_current()
        return {"ok": True, "stopped": stopped}

    # ── preview ─────────────────────────────────────────────────────────────
    def preview(self, project: str | None) -> dict:
        project = self._existing(project)
        ws = self.workspace()
        ctx = self.project_ctx(project)
        pmap = self.project_map(project)
        pdir = ws.project_dir(project)
        entry = str(pmap.get("entry_point") or "")
        kind = pmap.get("kind") or ""
        is_web = kind == "web" or entry.endswith((".html", ".htm")) or (pdir / "index.html").is_file() \
            and not pmap.get("run_command")
        env = clean_env(pdir, ctx.meta / "tmp")
        if is_web:
            port = free_port()
            sub = entry if entry.endswith((".html", ".htm")) else "index.html"
            url = f"http://127.0.0.1:{port}/{sub}"
            self.runner.start_background(f"preview:{project}", [sys.executable, "-m", "http.server", str(port),
                                         "--bind", "127.0.0.1", "--directory", str(pdir)],
                                         pdir, env, ctx.journal, label=f"Aperçu web de {project}", url=url)
            return {"ok": True, "kind": "web", "url": url}
        cmd = pmap.get("run_command") or ""
        if not cmd:
            return {"ok": False, "error": "Aucune commande de lancement connue pour ce projet."}
        argv = parse(cmd)
        verdict = classify(argv, pdir)
        if verdict.decision != "allow":
            return {"ok": False, "error": f"Lancement non autorisé automatiquement : {verdict.reason}"}
        if kind == "gui":
            self.runner.start_background(f"app:{project}", verdict.argv, pdir, env, ctx.journal,
                                         label=f"Application {project} lancée")
            return {"ok": True, "kind": "gui", "message": "Application lancée ; fermez-la ou utilisez ARRÊTER."}
        r = self.run_command(project, cmd, timeout=30)
        return {"ok": bool(r.get("ok")), "kind": "script", "output": (r.get("output") or "")[-6000:],
                "code": r.get("code"), "error": r.get("refused")}

    def open_preview(self, project: str, url: str) -> bool:
        """Show a running web preview in the HUD's preview window, if there is one."""
        fn = self.preview_opener
        if not callable(fn):
            return False
        title = str(self.project_map(project).get("title") or project)
        last = self.project_map(project).get("last_run") or {}
        try:
            return bool(fn(project, url, title, last.get("status") or ""))
        except Exception as e:
            self.log(f"Studio — aperçu non ouvert : {e}")
            return False

    def screenshot(self, project: str | None) -> dict:
        """The browser check's screenshot, as a data URL the HUD can show."""
        import base64
        project = self._existing(project)
        shot = self.project_ctx(project).meta / "reports" / "apercu.png"
        if not shot.is_file():
            return {"ok": False, "error": "Aucune capture : le contrôle navigateur n'a pas tourné."}
        data = shot.read_bytes()
        if len(data) > 6_000_000:
            return {"ok": False, "error": "Capture trop lourde."}
        return {"ok": True, "url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
                "ts": shot.stat().st_mtime}

    def sandbox(self, project: str | None) -> dict:
        """Launch the project inside Windows Sandbox (winsandbox.py). Only on request."""
        from . import winsandbox
        project = self._existing(project)
        if self.job is not None and self.job.running and self.job.project == project:
            raise SandboxError("Le Studio travaille encore sur ce projet : attendez la fin.")
        why = winsandbox.available()
        if why:
            return {"ok": False, "error": why}
        ctx = self.project_ctx(project)
        wsb = winsandbox.prepare(project, self.workspace().project_dir(project), ctx.meta, self.project_map(project))
        winsandbox.launch(wsb)
        ctx.journal.add("preview", "▶ Projet lancé dans Windows Sandbox : machine jetable, réseau coupé, "
                                   "dossier du projet en lecture seule", key=f"sandbox:{project}")
        return {"ok": True, "message": "Windows Sandbox démarre (quelques secondes) : le projet s'y lance seul. "
                                       "Fermer sa fenêtre efface tout."}

    def stop_previews(self, project: str | None = None) -> dict:
        n = 0
        for item in self.runner.background():
            if project is None or item["key"].endswith(":" + project):
                n += int(self.runner.stop_background(item["key"]))
        return {"ok": True, "stopped": n}

    # ── status ──────────────────────────────────────────────────────────────
    def status(self, project: str | None = None) -> dict:
        from .llm import groq_key
        cfg = self._get()
        provider = str(cfg.get("provider") or "auto").lower()
        local = f"Local · {cfg.get('local_model') or 'choix automatique'} (Ollama)"
        if provider in ("local", "ollama"):
            coder = local
        elif provider == "groq+local":
            coder = f"Groq, relais {local}"
        elif provider == "groq" or (provider == "auto" and groq_key()):
            coder = "Groq" + (f" · {cfg['groq_model']}" if cfg.get("groq_model") else "")
        else:
            coder = "Gemini"
        from . import winsandbox
        why = winsandbox.available()
        out: dict = {"root": self.root_setting(), "options": self.options(), "admin": is_admin(), "coder": coder,
                     "safety": SAFETY_NOTE, "projects": [], "job": None,
                     "sandbox": {"available": not why, "reason": why}}
        try:
            ws = self.workspace()
        except (SandboxError, OSError) as e:
            out["root_error"] = str(e)
            return out
        out["projects"] = ws.projects()
        job = self.job if self.job is not None else self.last_job
        if job is not None:
            out["job"] = job.info()
        name = project or (self.job.project if self.job is not None else None) or self.current_project \
            or (out["projects"][0] if out["projects"] else None)
        if not name or name not in out["projects"]:
            return out
        ctx = self.project_ctx(name)
        pmap = self.project_map(name)
        cps = ctx.checkpoints.list()
        out.update({
            "project": name,
            "folder": str(ws.project_dir(name)),
            "map": {k: pmap.get(k) for k in ("title", "summary", "kind", "stack", "entry_point",
                                              "run_command", "test_command", "features",
                                              "acceptance", "dependencies", "last_run", "updated")},
            "tasks": ctx.tasks.all(),
            "counts": ctx.tasks.counts(),
            "touched": ctx.journal.files_touched()[:80],
            "files": ws.list_files(name)[:200],
            "events": ctx.journal.recent(140),
            "terminal": ctx.journal.terminal_tail(220),
            "checkpoints": cps[:25],
            "changed": ctx.checkpoints.changed_since(cps[0]["id"]) if cps else [],
            "previews": [b for b in self.runner.background() if b["key"].endswith(":" + name)],
            "seq": ctx.journal.seq,
        })
        return out

    # ── HUD entry point ─────────────────────────────────────────────────────
    def hud(self, op: str, data: dict | None = None) -> dict:
        data = data or {}
        try:
            if op == "studio.ping":
                job = self.job if self.job is not None else self.last_job
                return {"running": bool(job is not None and job.running),
                        "job": job.info() if job is not None else None}
            if op == "studio.status":
                return self.status(data.get("project") or None)
            if op == "studio.set_root":
                return self.set_root(str(data.get("path", "")))
            if op == "studio.options":
                return self.set_options(data)
            if op == "studio.stop":
                return self.stop()
            if op == "studio.restore":
                project = self._existing(data.get("project"))
                if self.job is not None and self.job.running:
                    raise SandboxError("Arrêtez le travail en cours avant de restaurer.")
                return {"ok": True, **self.project_ctx(project).checkpoints.restore(str(data.get("id", "")))}
            if op == "studio.checkpoint":
                project = self._existing(data.get("project"))
                return {"ok": True, **self.project_ctx(project).checkpoints.create(
                    str(data.get("label") or "manuel (HUD)"))}
            if op == "studio.diff":
                project = self._existing(data.get("project"))
                return {"ok": True, "diff": self.project_ctx(project).checkpoints.diff(
                    str(data.get("path", "")), data.get("id") or None)}
            if op == "studio.file":
                project = self._existing(data.get("project"))
                return {"ok": True, "content": self.workspace().read_text(project, str(data.get("path", "")))}
            if op == "studio.preview":
                r = self.preview(data.get("project"))
                if r.get("url"):
                    r["opened"] = self.open_preview(self._existing(data.get("project")), r["url"])
                return r
            if op == "studio.screenshot":
                return self.screenshot(data.get("project"))
            if op == "studio.sandbox":
                return self.sandbox(data.get("project"))
            if op == "studio.local_models":
                return local_models()
            if op == "studio.write":
                # The user's own edit from the HUD: same journaled, undoable path as the tool.
                project = self._existing(data.get("project"))
                self._guard_running("enregistrer")
                content = str(data.get("content", ""))
                if len(content) > 1_000_000:
                    raise SandboxError("Fichier trop gros pour l'éditeur du HUD (1 Mo max).")
                return {"ok": True, **self.write_file(project, str(data.get("path", "")), content)}
            if op == "studio.test":
                project = self._existing(data.get("project"))
                self._guard_running("tests")
                cmd = self.project_map(project).get("test_command") or "python -m unittest discover -s tests -t . -v"
                r = self.run_command(project, cmd)
                return {"ok": bool(r.get("ok")), "command": cmd, "code": r.get("code"),
                        "output": (r.get("output") or r.get("refused") or r.get("message") or "")[-12000:],
                        "timed_out": r.get("timed_out")}
            if op == "studio.stop_preview":
                return self.stop_previews(data.get("project") or None)
            if op == "studio.open_folder":
                project = data.get("project")
                target = self.workspace().project_dir(self._existing(project)) if project else self.workspace().root
                if sys.platform == "win32":
                    os.startfile(str(target))           # noqa: S606 - Explorer on our own folder
                return {"ok": True, "folder": str(target)}
            return {"error": f"opération inconnue : {op}"}
        except (SandboxError, CommandRefused) as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # ── tool entry point (the model) ────────────────────────────────────────
    def tool(self, p: dict, player=None) -> str:
        self.bind(player)
        action = str(p.get("action") or "status").strip().lower()
        project = (p.get("project") or "").strip() or None
        try:
            if action in ("create", "build", "new"):
                req = str(p.get("request") or p.get("description") or "").strip()
                r = self.start_job("create", req, project)
                return (f"[STUDIO_STARTED] Project '{r['project']}' started in {r['folder']}. The work runs "
                        "in the background and every real step is shown live in the HUD's STUDIO panel. "
                        "Tell the user in ONE short sentence, in their language, that you have started "
                        "and they can follow it in the Studio panel. Do NOT claim any file or test result "
                        "yet: you will be told when it finishes.")
            if action in ("modify", "change", "improve", "continue", "resume"):
                req = str(p.get("request") or p.get("description") or "").strip()
                r = self.start_job("change", req, project)
                return (f"[STUDIO_STARTED] Change started on '{r['project']}'. Progress is live in the HUD's "
                        "STUDIO panel. Say ONE short sentence; do not claim results yet.")
            if action == "stop":
                r = self.stop()
                return "Studio work stopped." if r["stopped"] else "Nothing was running in the Studio."
            if action in ("list", "list_projects", "projects"):
                ps = self.workspace().projects()
                return (f"Workspace {self.workspace().root}: " + (", ".join(ps) if ps else "no project yet."))
            if action == "workspace":
                return f"Workspace root: {self.root_setting()}. {SAFETY_NOTE}"
            if action == "status":
                return _status_text(self.status(project))
            name = self._existing(project)
            ws = self.workspace()
            if action in ("info", "project_info", "map"):
                st = self.status(name)
                return json.dumps({"map": st.get("map"), "files": [f["path"] for f in st.get("files", [])],
                                   "tasks": [(t["title"], t["status"]) for t in st.get("tasks", [])]},
                                  ensure_ascii=False)[:6000]
            if action in ("files", "list_files"):
                return "\n".join(f"{f['path']} ({f['size']} o)" for f in ws.list_files(name)) or "(vide)"
            if action == "read_file":
                text = ws.read_text(name, str(p.get("path", "")))
                return text[:20000] + ("\n…[tronqué]" if len(text) > 20000 else "")
            self._guard_running(action)
            if action == "write_file":
                r = self.write_file(name, str(p.get("path", "")), str(p.get("content", "")))
                return f"{r['op']} {r['path']} ({r['lines']} lines). Recorded in the Studio journal."
            if action == "delete_file":
                r = self.delete_file(name, str(p.get("path", "")))
                return f"Deleted {r['path']} (undoable)."
            if action in ("run", "run_command"):
                r = self.run_command(name, str(p.get("command", "")))
                return _run_text(r)
            if action in ("test", "tests", "run_tests"):
                cmd = self.project_map(name).get("test_command") or "python -m unittest discover -s tests -t . -v"
                return _run_text(self.run_command(name, cmd))
            if action == "checkpoint":
                r = self.project_ctx(name).checkpoints.create(str(p.get("label") or "manuel"))
                return f"Checkpoint {r['id']} created ({r['files']} files)."
            if action == "checkpoints":
                cps = self.project_ctx(name).checkpoints.list()
                return "\n".join(f"{c['id']} — {c['label']} ({c['files']} files)" for c in cps) or "No checkpoint."
            if action == "restore":
                cid = str(p.get("checkpoint") or "").strip()
                cps = self.project_ctx(name).checkpoints.list()
                if not cid or cid in ("last", "latest", "dernier"):
                    if not cps:
                        return "No checkpoint to restore."
                    cid = cps[0]["id"]
                r = self.project_ctx(name).checkpoints.restore(cid)
                return (f"Restored '{name}' to checkpoint {cid}: {r['written']} files restored, "
                        f"{r['removed']} removed. A safety checkpoint {r['safety']} was taken first.")
            if action == "diff":
                return self.project_ctx(name).checkpoints.diff(str(p.get("path", "")), p.get("checkpoint") or None)[:8000]
            if action == "sandbox":
                r = self.sandbox(name)
                return r.get("message") or f"Windows Sandbox unavailable: {r.get('error')}"
            if action == "preview":
                r = self.preview(name)
                if r.get("url"):
                    if self.open_preview(name, r["url"]):
                        return f"Preview running at {r['url']}, shown in the HUD's preview window."
                    _open_url(r["url"])
                    return f"Preview running at {r['url']} (opened in the browser)."
                return json.dumps(r, ensure_ascii=False)[:4000]
            return f"Unknown Studio action '{action}'."
        except (SandboxError, CommandRefused) as e:
            return f"Studio refused: {e}"

    def _guard_running(self, action: str) -> None:
        if self.job is not None and self.job.running:
            raise SandboxError(f"Un travail automatique est en cours sur « {self.job.project} » : "
                               f"« {action} » attendra qu'il soit fini (ou arrêtez-le).")


# ── helpers ─────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", t.lower())
    stop = {"cree", "creer", "moi", "une", "un", "des", "de", "la", "le", "les", "du", "avec", "et",
            "application", "app", "projet", "fais", "fait", "make", "create", "a", "an", "the", "me",
            "pour", "qui", "dans", "ton", "mon", "espace", "travail", "stp", "please"}
    keep = [w for w in words if w not in stop][:4] or words[:3] or ["projet"]
    return "_".join(keep)[:40]


def _run_text(r: dict) -> str:
    if r.get("pending"):
        return r.get("message", "Waiting for the user's confirmation on the HUD.")
    if r.get("refused"):
        return f"Command refused: {r['refused']}"
    from .diagnose import excerpt, headline
    status = "TIMED OUT" if r.get("timed_out") else "STOPPED" if r.get("stopped") else f"exit code {r.get('code')}"
    out = excerpt(r.get("output", ""), 5000)
    return f"{status} in {r.get('seconds')} s.\n{out}" + (
        f"\nMain error: {headline(r.get('output', ''))}" if not r.get("ok") else "")


def _status_text(st: dict) -> str:
    if st.get("root_error"):
        return f"The workspace is not usable: {st['root_error']}"
    parts = [f"Workspace: {st['root']}. Projects: {', '.join(st['projects']) or 'none'}."]
    job = st.get("job")
    if job:
        state = "running" if job["running"] else "finished"
        parts.append(f"Job on '{job['project']}' ({state}): phase {job['phase_label']}"
                     + (f", step {job['step']}" if job.get("step") else "")
                     + (f", fix attempt {job['attempt']}/{job['max_attempts']}" if job.get("attempt") else "") + ".")
        res = job.get("result") or {}
        if res.get("status_text"):
            parts.append(f"Verdict: {res['status']} ({res['status_text']}"
                         + (f" — {res['why']}" if res.get("why") else "") + ").")
        if res.get("checks"):
            state = {True: "OK", False: "FAILED", None: "NOT RUN"}
            parts.append("Checks: " + "; ".join(f"{c['name']}={state.get(c['ok'], 'FAILED')}"
                                                for c in res["checks"]) + ".")
        if res.get("criteria"):
            parts.append("Criteria: " + "; ".join(f"{c['id']} {c['status']}" for c in res["criteria"]) + ".")
        if res.get("remaining"):
            parts.append(f"Remaining problem: {res['remaining']}.")
    if st.get("tasks"):
        c = st.get("counts", {})
        parts.append(f"Tasks on '{st.get('project')}': {c.get('done', 0)} done, {c.get('doing', 0)} in progress, "
                     f"{c.get('todo', 0)} to do, {c.get('blocked', 0)} blocked.")
    return " ".join(parts)


def local_models() -> dict:
    """What Ollama has installed, for the HUD's model picker."""
    from .llm import LOCAL_URL
    try:
        import requests
        r = requests.get(f"{LOCAL_URL}/api/tags", timeout=3)
        models = [{"name": m.get("name", ""), "gb": round((m.get("size") or 0) / 2**30, 1)}
                  for m in (r.json().get("models") or [])] if r.ok else []
    except Exception:
        return {"ok": False, "models": [], "error": "Ollama ne répond pas (lancez Ollama)."}
    return {"ok": True, "models": models}


def _open_url(url: str) -> None:
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _config_get() -> dict:
    try:
        from memory.config_manager import get_plugin_config
        return get_plugin_config(CONFIG_NS)
    except Exception:
        return {}


def _config_save(values: dict) -> None:
    from memory.config_manager import save_plugin_config
    save_plugin_config(CONFIG_NS, values)


_studio: Studio | None = None
_studio_lock = threading.Lock()


def get_studio() -> Studio:
    global _studio
    with _studio_lock:
        if _studio is None:
            _studio = Studio()
        return _studio
