"""
llm.py — the three things the Studio asks a model: plan, write, fix.

The model only ever PROPOSES. Its plan is validated (paths through the
sandbox, commands through the runner's policy) before anything is created, its
file contents are written by the sandbox, and whether its fix worked is
decided by running the tests — never by what it says.

`StudioLLM` is the default (Gemini, with the key in config/api_keys.json, the
same way actions/dev_agent.py calls it). Anything with the same `complete()`
method can replace it — the selftest uses a scripted fake.
"""

from __future__ import annotations

import json
import re
import time

DEFAULT_MODEL = "gemini-flash-latest"


class LLMError(RuntimeError):
    pass


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z0-9_+-]*\r?\n", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text


def parse_json(text: str) -> dict:
    t = _strip_fences(text)
    try:
        data = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise LLMError("La réponse du modèle n'est pas du JSON.")
        try:
            data = json.loads(m.group(0))
        except ValueError as e:
            raise LLMError(f"JSON illisible : {e}")
    if not isinstance(data, dict):
        raise LLMError("La réponse du modèle n'est pas un objet JSON.")
    return data


def _retry_delay(message: str, fallback: float) -> float:
    """Google says how long to wait ('retryDelay': '37s'); trust it, within reason."""
    m = re.search(r"retry(?:Delay|_delay)?['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", message, re.I) \
        or re.search(r"retry in (\d+(?:\.\d+)?)\s*s", message, re.I)
    return min(90.0, float(m.group(1)) + 1) if m else fallback


class StudioLLM:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model or DEFAULT_MODEL
        self._client = None
        #: Set by the engine: on_wait(seconds, reason) is journaled so the HUD
        #: shows WHY nothing moves; should_stop() makes the wait interruptible.
        self.on_wait = None
        self.should_stop = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            from memory.config_manager import get_gemini_key
            key = get_gemini_key()
            if not key:
                raise LLMError("Aucune clé Gemini dans config/api_keys.json.")
            self._client = genai.Client(api_key=key)
        return self._client

    def complete(self, prompt: str, json_mode: bool = False) -> str:
        from google.genai import types
        client = self._get_client()
        cfg = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        delay = 5.0
        attempts = 6
        for attempt in range(attempts):
            try:
                r = client.models.generate_content(model=self.model, contents=prompt, config=cfg)
                text = getattr(r, "text", None) or ""
                if text.strip():
                    return text
                raise LLMError("Réponse vide du modèle.")
            except Exception as e:
                raw = str(e)
                msg = raw.lower()
                quota = "429" in msg or "quota" in msg or "resource_exhausted" in msg
                if attempt < attempts - 1 and (quota or "503" in msg or "unavailable" in msg):
                    wait = _retry_delay(raw, delay)
                    if callable(self.on_wait):
                        self.on_wait(wait, "quota Gemini atteint" if quota else "Gemini indisponible")
                    end = time.monotonic() + wait
                    while time.monotonic() < end:
                        if callable(self.should_stop) and self.should_stop():
                            raise LLMError("Arrêté pendant l'attente du modèle.")
                        time.sleep(0.5)
                    delay = min(delay * 2, 60.0)
                    continue
                if isinstance(e, LLMError):
                    raise
                if quota:
                    raise LLMError("Quota Gemini épuisé : réessayez plus tard ou vérifiez votre "
                                   "offre sur https://ai.google.dev.") from e
                raise LLMError(f"Appel au modèle impossible : {e}") from e
        raise LLMError("Le modèle n'a pas répondu.")


# ── prompts ─────────────────────────────────────────────────────────────────

_RULES = """Constraints of the workspace (enforced by code, plan accordingly):
- Every path is RELATIVE to the project folder, forward slashes, no '..', no absolute path.
- Commands run WITHOUT a shell. Allowed: `python <file.py>`, `python -m unittest ...`,
  `python -m pytest ...` (only if pytest is a dependency), `node <file.js>`, `npm test`.
- Prefer the Python standard library. Every third-party dependency needs the user's
  approval to install and slows everything down: use one only when truly needed.
- A web application = static files (index.html, CSS, JS) with NO build step, NO CDN
  required to work offline is a plus; data can live in localStorage.
- ALWAYS include automated tests that run headless and fast (< 20 s) with the standard
  library: tests/__init__.py (empty) + tests/test_*.py, run by
  `python -m unittest discover -s tests -t . -v` (so tests can import project modules). Test the
  logic (keep logic in importable modules, separate from UI). For a pure web app, put the
  logic in a small Python or JS module you can test, or test the files' structure in Python.
- The program must never block waiting for keyboard input when run by the tests.
- Keep it small and complete: usually 3 to 12 files. Include a README.md explaining how to run it.
"""

PLAN_PROMPT = """You are the senior engineer of "Jarvis Code Studio". Plan a NEW project.

User request (may be in French):
{request}

{rules}
Return ONLY a JSON object:
{{
  "project_name": "short_snake_case_folder_name",
  "title": "Human title in the user's language",
  "summary": "2 sentences, in the user's language",
  "kind": "web | script | cli | gui | library",
  "stack": ["python", "html", ...],
  "entry_point": "main.py or index.html",
  "run_command": "python main.py   (or empty for a web page)",
  "test_command": "python -m unittest discover -s tests -t . -v",
  "dependencies": [],
  "features": ["feature, in the user's language", ...],
  "tasks": [{{"title": "task in the user's language", "files": ["path", ...]}}, ...],
  "files": [{{"path": "relative/path.ext", "purpose": "what it contains"}}, ...]
}}
Every file in "files" must appear in exactly one task. Order tasks so a file's
dependencies are written before it."""

CHANGE_PROMPT = """You are the senior engineer of "Jarvis Code Studio". Plan a CHANGE to an
existing project.

Project map:
{project_map}

Current files (path: size):
{files}

User request (may be in French):
{request}

{rules}
Return ONLY a JSON object:
{{
  "summary": "what will change, in the user's language",
  "test_command": "command to run the tests after the change (keep the existing one if fine)",
  "run_command": "unchanged unless needed",
  "dependencies": [],
  "tasks": [{{"title": "task in the user's language", "files": ["path", ...]}}, ...],
  "files": [{{"path": "relative/path.ext", "action": "create | modify | delete", "purpose": "..."}}, ...]
}}"""

WRITE_PROMPT = """You are writing ONE file of the project "{title}".

Project plan:
{plan}

Files already written (excerpts):
{context}

{current}
Write the COMPLETE content of: {path}
Purpose: {purpose}

Return ONLY the raw file content. No markdown fences, no explanation."""

FIX_PROMPT = """You are fixing the project "{title}" in Jarvis Code Studio.

The check `{check}` failed. Error type: {kind}.
Output:
{output}

{located}
Project files (path: size): {files}

Relevant file contents:
{contents}

Previous attempts that did NOT fix it:
{history}

Find the root cause. Fix the CODE (or the test if the test is clearly wrong).
Return ONLY a JSON object:
{{
  "diagnosis": "one sentence, in French",
  "files": [{{"path": "relative/path", "content": "the COMPLETE new content of the file"}}],
  "needs_dependency": "pypi package name if a third-party package is genuinely missing, else empty",
  "give_up": "non-empty explanation ONLY if this cannot be fixed without the user"
}}
Change at most 3 files."""


def plan_new(llm, request: str) -> dict:
    return parse_json(llm.complete(PLAN_PROMPT.format(request=request, rules=_RULES), json_mode=True))


def plan_change(llm, request: str, project_map: dict, files: list[dict]) -> dict:
    listing = "\n".join(f"{f['path']}: {f['size']}" for f in files[:200]) or "(vide)"
    return parse_json(llm.complete(CHANGE_PROMPT.format(
        project_map=json.dumps(project_map, ensure_ascii=False, indent=1)[:6000],
        files=listing, request=request, rules=_RULES), json_mode=True))


def write_file(llm, plan: dict, path: str, purpose: str, context: str, current: str | None) -> str:
    cur = (f"Current content of {path} (modify it, keep what still applies):\n{current[:12000]}\n"
           if current else "")
    brief = {k: plan.get(k) for k in ("title", "summary", "kind", "stack", "entry_point",
                                      "run_command", "test_command", "features", "files")}
    text = llm.complete(WRITE_PROMPT.format(
        title=plan.get("title") or plan.get("project_name", ""),
        plan=json.dumps(brief, ensure_ascii=False, indent=1)[:6000],
        context=context[:14000] or "(aucun)", current=cur, path=path, purpose=purpose))
    return _strip_fences(text) + ("\n" if not text.endswith("\n") else "")


def fix(llm, title: str, check: str, kind: str, output: str, located: str,
        files: list[dict], contents: str, history: list[str]) -> dict:
    return parse_json(llm.complete(FIX_PROMPT.format(
        title=title, check=check, kind=kind, output=output, located=located,
        files=", ".join(f"{f['path']}: {f['size']}" for f in files[:120]),
        contents=contents[:24000], history="\n".join(history[-4:]) or "(aucune)"), json_mode=True))
