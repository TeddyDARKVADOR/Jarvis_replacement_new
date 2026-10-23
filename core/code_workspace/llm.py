"""
llm.py — the three things the Studio asks a model: plan, write, fix.

The model only ever PROPOSES. Its plan is validated (paths through the
sandbox, commands through the runner's policy) before anything is created, its
file contents are written by the sandbox, and whether its fix worked is
decided by running the tests — never by what it says.

`StudioLLM` is the default (Gemini, with the key in the active config credentials file, the
same way the other actions call it). Anything with the same `complete()`
method can replace it — the selftest uses a scripted fake.
"""

from __future__ import annotations

import json
import re
import time

DEFAULT_MODEL = "gemini-flash-latest"
NONE = "(aucun)"


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
                raise LLMError("Aucune clé Gemini dans config/api_key.json (ou ancien api_keys.json).")
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
                from core.token_usage import record_response
                record_response(r, self.model, "code_workspace")
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


GROQ_URL = "https://api.groq.com/openai/v1"
#: Best coding models first; the first one this key can actually use wins.
GROQ_PREFERRED = ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct-0905", "moonshotai/kimi-k2-instruct",
                  "qwen/qwen3-32b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"]
_GROQ_SKIP = ("whisper", "guard", "tts", "playai", "orpheus", "compound", "prompt-guard")


def groq_key() -> str:
    import os
    try:
        from memory.config_manager import load_api_keys
        key = str(load_api_keys().get("groq_api_key") or "").strip()
    except Exception:
        key = ""
    return key or os.environ.get("GROQ_API_KEY", "").strip()


class GroqLLM:
    """The Studio's coder on Groq (OpenAI-compatible chat API, plain HTTP)."""

    def __init__(self, model: str | None = None, key: str | None = None, http=None) -> None:
        self.key = key or groq_key()
        if not self.key:
            raise LLMError("Aucune clé Groq (groq_api_key) dans le fichier de configuration des clés.")
        self.requested = (model or "").strip() or None
        self._model: str | None = None
        self._http = http
        self.on_wait = None
        self.should_stop = None

    @property
    def http(self):
        if self._http is None:
            import requests
            self._http = requests
        return self._http

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    @property
    def model(self) -> str:
        """The configured model if the key can use it, else the best available one."""
        if self._model:
            return self._model
        available: list[str] = []
        try:
            r = self.http.get(f"{GROQ_URL}/models", headers=self._headers(), timeout=20)
            if r.status_code == 401:
                raise LLMError("Clé Groq refusée (401) : vérifiez groq_api_key.")
            if r.ok:
                available = [m.get("id", "") for m in (r.json().get("data") or [])
                             if m.get("active", True)]
        except LLMError:
            raise
        except Exception:
            available = []
        if not available:
            self._model = self.requested or GROQ_PREFERRED[0]
        elif self.requested in available:
            self._model = self.requested
        else:
            self._model = next((m for m in GROQ_PREFERRED if m in available), None) or next(
                (m for m in available if not any(s in m.lower() for s in _GROQ_SKIP)), available[0])
        return self._model

    def complete(self, prompt: str, json_mode: bool = False) -> str:
        body = {"model": self.model, "temperature": 0.2, "max_completion_tokens": 16384,
                "messages": [{"role": "system", "content": "You are an expert software engineer. "
                              "Follow the output format exactly."},
                             {"role": "user", "content": prompt}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        delay, attempts = 5.0, 6
        for attempt in range(attempts):
            try:
                r = self.http.post(f"{GROQ_URL}/chat/completions", headers=self._headers(),
                                   json=body, timeout=180)
            except Exception as e:
                if attempt < attempts - 1:
                    self._wait(delay, "Groq injoignable")
                    delay = min(delay * 2, 60.0)
                    continue
                raise LLMError(f"Groq injoignable : {e}") from e
            if r.status_code == 200:
                try:
                    text = r.json()["choices"][0]["message"].get("content") or ""
                except (ValueError, KeyError, IndexError):
                    text = ""
                if text.strip():
                    return text
                if attempt < attempts - 1:
                    # Often a reasoning model that spent its budget thinking, or a
                    # throttled account: an immediate retry fails the same way.
                    self._wait(delay, "réponse vide de Groq")
                    delay = min(delay * 2, 60.0)
                    continue
                raise LLMError("Réponse vide de Groq.")
            detail = r.text[:300]
            if r.status_code == 401:
                raise LLMError("Clé Groq refusée (401) : vérifiez groq_api_key.")
            if r.status_code == 400 and json_mode and "json" in detail.lower() and "response_format" in body:
                body.pop("response_format")              # model without JSON mode: ask in the prompt only
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                wait = delay
                try:
                    wait = min(90.0, float(r.headers.get("retry-after") or delay) + 0.5)
                except ValueError:
                    pass
                self._wait(wait, "limite Groq atteinte" if r.status_code == 429 else "Groq indisponible")
                delay = min(delay * 2, 60.0)
                continue
            raise LLMError(f"Groq a répondu {r.status_code} : {detail}")
        raise LLMError("Groq n'a pas répondu.")

    def _wait(self, secs: float, why: str) -> None:
        if callable(self.on_wait):
            self.on_wait(secs, why)
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if callable(self.should_stop) and self.should_stop():
                raise LLMError("Arrêté pendant l'attente du modèle.")
            time.sleep(0.25)


def make_llm(cfg: dict):
    """Groq when a Groq key exists (the Studio's coder), Gemini otherwise —
    unless the HUD/config pins `provider`."""
    provider = str(cfg.get("provider") or "auto").lower()
    if provider == "groq" or (provider == "auto" and groq_key()):
        return GroqLLM(cfg.get("groq_model") or None)
    return StudioLLM(cfg.get("model") or None)


def describe(llm) -> str:
    if isinstance(llm, GroqLLM):
        return f"Groq · {llm.model}"
    return f"Gemini · {getattr(llm, 'model', '?')}"


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
- ACCEPTANCE CRITERIA: list 3 to 8 observable behaviours that together cover EVERY feature
  the user asked for, ids AC1, AC2… Each says how it is verified:
    verify="test"   at least one test method whose NAME contains the id in lower case,
                    e.g. `def test_ac1_adds_an_expense(self):` — this is how the Studio
                    knows which criterion a test proves; a criterion without one is
                    reported "unverified".
    verify="web"    (kind=web only) a `web_checks` entry with that id, run in a real browser.
    verify="manual" ONLY for what no program can judge (look, feel, sound).
- WEB_CHECKS (kind=web only): scripted paths a headless browser plays on the page, each from
  a freshly loaded page with an empty localStorage. Step actions: click | fill (value) |
  press (key) | check | uncheck | select (value) | wait (ms). Expectations: a CSS selector plus
  ONE of text (contained, case-insensitive), count, value, visible. Use ids/classes that the
  HTML you plan really has. The page must also load with no JavaScript error, no console error
  and no missing local file: the browser blocks the Internet, so ship every asset locally.
"""

PLAN_PROMPT = """You are the senior engineer of "Matthew Code Studio". Plan a NEW project.

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
  "acceptance": [{{"id": "AC1", "criterion": "observable behaviour, in the user's language",
                  "verify": "test | web | manual"}}, ...],
  "web_checks": [{{"id": "AC2", "name": "short name",
                  "steps": [{{"action": "fill", "selector": "#amount", "value": "12"}},
                            {{"action": "click", "selector": "#add"}}],
                  "expect": [{{"selector": "#history li", "count": 1}},
                             {{"selector": "#total", "text": "12"}}]}}],
  "tasks": [{{"title": "task in the user's language", "files": ["path", ...]}}, ...],
  "files": [{{"path": "relative/path.ext", "purpose": "what it contains"}}, ...]
}}
Every file in "files" must appear in exactly one task. Order tasks so a file's
dependencies are written before it. "web_checks" is [] unless kind is "web"."""

CHANGE_PROMPT = """You are the senior engineer of "Matthew Code Studio". Plan a CHANGE to an
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
  "acceptance": "the FULL updated list (keep the map's existing criteria and their ids, add new ones)",
  "web_checks": "the FULL updated list for a web project (keep the existing ones that still apply)",
  "tasks": [{{"title": "task in the user's language", "files": ["path", ...]}}, ...],
  "files": [{{"path": "relative/path.ext", "action": "create | modify | delete", "purpose": "..."}}, ...]
}}"""

WRITE_PROMPT = """You are writing ONE file of the project "{title}".

Project plan:
{plan}

Files already written (excerpts):
{context}

{current}{tests_rule}
Write the COMPLETE content of: {path}
Purpose: {purpose}

Return ONLY the raw file content. No markdown fences, no explanation."""

FIX_PROMPT = """You are fixing the project "{title}" in Matthew Code Studio.

The check `{check}` failed. Error type: {kind}.
Output:
{output}

{located}
Project files (path: size): {files}

Relevant file contents:
{contents}
{spec}
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
                                      "run_command", "test_command", "features", "acceptance",
                                      "web_checks", "files")}
    text = llm.complete(WRITE_PROMPT.format(
        title=plan.get("title") or plan.get("project_name", ""),
        plan=json.dumps(brief, ensure_ascii=False, indent=1)[:9000],
        context=context[:14000] or NONE, current=cur, tests_rule=_tests_rule(plan, path),
        path=path, purpose=purpose))
    content = _strip_fences(text)
    return content + ("\n" if not content.endswith("\n") else "")


def is_test_file(path: str) -> bool:
    rel = path.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    if not name.endswith(".py") or name == "__init__.py":
        return False
    return rel.startswith("tests/") or name.startswith("test_")


def _tests_rule(plan: dict, path: str) -> str:
    """The naming rule, repeated where it matters: in the test file itself."""
    wanted = [c for c in plan.get("acceptance") or [] if c.get("verify") == "test"]
    if not wanted or not is_test_file(path):
        return ""
    lines = "\n".join(f"- {c['id']}: {c['criterion']}" for c in wanted)
    return ("\nTHIS IS A TEST FILE. The Studio knows which acceptance criterion a test proves ONLY by its "
            "name: every criterion below needs at least one test method (in this file or another test file "
            "of the plan) whose name contains its id in lower case, e.g. `def test_ac1_converts_zero(self):`. "
            "A criterion without such a test is reported UNVERIFIED.\n" + lines + "\n")


COVER_PROMPT = """The project "{title}" passes its checks, but these acceptance criteria have NO
test proving them (no test method whose name contains the id):
{missing}

Current test files:
{tests}

Code under test (excerpts):
{code}

Add the missing tests. Each new test method name MUST contain the criterion id in lower case
(`def test_ac2_...`). Test the real behaviour described, headless, fast, standard library only.
Keep every existing test. Only files under tests/ may be written.
Return ONLY a JSON object:
{{"files": [{{"path": "tests/test_xxx.py", "content": "the COMPLETE file"}}]}}"""


def cover(llm, title: str, missing: list[dict], tests: str, code: str) -> dict:
    return parse_json(llm.complete(COVER_PROMPT.format(
        title=title, missing="\n".join(f"- {c['id']}: {c['criterion']}" for c in missing),
        tests=tests[:12000] or NONE, code=code[:12000] or NONE), json_mode=True))


def fix(llm, title: str, check: str, kind: str, output: str, located: str,
        files: list[dict], contents: str, history: list[str], web_checks: list | None = None) -> dict:
    # The browser paths encode the user's criteria: the fixer changes the code
    # to satisfy them, it is never offered a way to weaken them.
    spec = ("\nBrowser checks the page must pass (fixed, change the CODE so they pass):\n"
            + json.dumps(web_checks, ensure_ascii=False)[:4000] + "\n") if web_checks else ""
    return parse_json(llm.complete(FIX_PROMPT.format(
        title=title, check=check, kind=kind, output=output, located=located,
        files=", ".join(f"{f['path']}: {f['size']}" for f in files[:120]),
        contents=contents[:24000], spec=spec, history="\n".join(history[-4:]) or "(aucune)"),
        json_mode=True))
