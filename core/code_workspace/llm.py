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


#: Output budget (tokens) by role — what a call is for decides how much it may write.
OUT_BUDGET = {"plan": 3000, "write": 4000, "fix": 5000, "cover": 3000}


def estimate_tokens(text: str) -> int:
    """A conservative token count (code and French run ~3.5-4.2 characters a token)."""
    return int(len(text or "") / 3.2) + 64


def duration(text) -> float:
    """Groq's reset times: '7.66s', '2m59.56s', '14h12m', '320ms' -> seconds."""
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", str(text or "")):
        total += float(value) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


def configure(llm, role: str, schema: dict | None = None) -> None:
    """Tell a provider what the next call is for. Anything without these
    attributes (the selftest's scripted models) simply ignores it."""
    if llm is not None and hasattr(llm, "role"):
        llm.role = role
    if llm is not None and hasattr(llm, "schema"):
        llm.schema = schema


class GroqLLM:
    """The Studio's coder on Groq (OpenAI-compatible chat API, plain HTTP).

    Groq's free/developer limits are tight (gpt-oss-120b: 8 000 tokens per
    minute, 1 000 requests per day), so the client paces itself: every answer
    carries `x-ratelimit-*` headers, and a call that would not fit in what is
    left of the minute waits for the exact reset (or goes to the local relay)
    instead of earning a 429. A daily quota that is gone is said plainly.

    With a `fallback` (the local model), only calls whose quality matters less
    are relayed — writing a file from a plan, adding missing tests — while the
    plan and the fixes wait for Groq. Once the daily quota is gone, everything
    goes to the relay.
    """

    #: Roles a rate-limited call may hand to the local relay.
    RELAY_ROLES = ("write", "cover")
    #: gpt-oss reasoning effort per role: thinking is spent where it pays.
    EFFORT = {"plan": "medium", "fix": "medium", "write": "low", "cover": "low"}

    def __init__(self, model: str | None = None, key: str | None = None, http=None, fallback=None) -> None:
        self.key = key or groq_key()
        if not self.key:
            raise LLMError("Aucune clé Groq (groq_api_key) dans config/api_keys.json.")
        self.requested = (model or "").strip() or None
        self._model: str | None = None
        self._http = http
        self.on_wait = None
        self.should_stop = None
        #: Another coder (the local one) that answers instead of waiting out a
        #: rate limit or an outage. None: wait, as before.
        self.fallback = fallback
        self.relayed = 0
        self.role = "write"
        self.schema = None
        self.limits: dict = {}
        self.exhausted_until = 0.0
        self.exhausted_why = ""

    @property
    def http(self):
        if self._http is None:
            import requests
            self._http = requests
        return self._http

    def _relay(self, prompt: str, json_mode: bool, why: str) -> str:
        self.relayed += 1
        if callable(self.on_wait):
            self.on_wait(0, f"{why} — le modèle local prend le relais")
        configure(self.fallback, self.role, self.schema)
        self.fallback.should_stop = self.should_stop
        return self.fallback.complete(prompt, json_mode)

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

    # -- rate limits ---------------------------------------------------------
    def _read_limits(self, r) -> None:
        h = getattr(r, "headers", None) or {}
        now = time.monotonic()
        try:
            if h.get("x-ratelimit-remaining-tokens") is not None:
                self.limits["tokens"] = int(float(h["x-ratelimit-remaining-tokens"]))
                self.limits["tokens_reset"] = now + duration(h.get("x-ratelimit-reset-tokens"))
            if h.get("x-ratelimit-remaining-requests") is not None:
                self.limits["requests"] = int(float(h["x-ratelimit-remaining-requests"]))
                self.limits["requests_reset"] = now + duration(h.get("x-ratelimit-reset-requests"))
            if h.get("x-ratelimit-limit-tokens") is not None:
                self.limits["tpm"] = int(float(h["x-ratelimit-limit-tokens"]))
        except (TypeError, ValueError):
            pass

    def _pace(self, prompt: str, json_mode: bool) -> str | None:
        """Before a call: wait for the minute's budget, or relay. Returns the relayed answer, if any."""
        now = time.monotonic()
        if self.exhausted_until > now:
            if self.fallback is not None:
                return self._relay(prompt, json_mode, "quota quotidien Groq épuisé")
            raise LLMError(self._daily_message(self.exhausted_until - now))
        need = estimate_tokens(prompt) + OUT_BUDGET.get(self.role, 4000)
        left, reset = self.limits.get("tokens"), self.limits.get("tokens_reset", 0.0)
        if left is not None and left < need and reset > now:
            if self.fallback is not None and self.role in self.RELAY_ROLES:
                return self._relay(prompt, json_mode, "budget Groq de la minute épuisé")
            self._wait(reset - now + 0.5, "budget Groq de la minute épuisé (attente calculée)")
            self.limits.pop("tokens", None)
        return None

    def _daily_message(self, secs: float) -> str:
        return (f"Quota quotidien Groq épuisé{self.exhausted_why} (retour dans environ "
                f"{max(1, round(secs / 60))} min). Choisissez « Groq + relais local » ou « IA locale » "
                "dans les réglages du Studio.")

    def _daily_exhausted(self, r) -> float:
        """Seconds until the daily quota is back, when THAT is what the 429 means; else 0."""
        try:
            retry = float(r.headers.get("retry-after") or 0)
        except (TypeError, ValueError):
            retry = 0.0
        self._read_limits(r)
        if self.limits.get("requests") == 0 or retry > 120:
            text = (getattr(r, "text", "") or "").lower()
            self.exhausted_why = (" : jetons par jour (TPD)" if "tokens per day" in text or "tpd" in text else
                                  " : requêtes par jour (RPD)" if "requests per day" in text or "rpd" in text
                                  else "")
            back = max(retry, self.limits.get("requests_reset", 0.0) - time.monotonic())
            return back if back > 0 else 3600.0
        return 0.0

    # -- the call --------------------------------------------------------------
    def complete(self, prompt: str, json_mode: bool = False) -> str:
        relayed = self._pace(prompt, json_mode)
        if relayed is not None:
            return relayed
        role = self.role if self.role in OUT_BUDGET else "write"
        body = {"model": self.model, "temperature": 0.2, "max_completion_tokens": 16384,
                "messages": [{"role": "system", "content": "You are an expert software engineer. "
                              "Follow the output format exactly."},
                             {"role": "user", "content": prompt}]}
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = self.EFFORT.get(role, "medium")
            body["include_reasoning"] = False
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        delay, attempts, longer = 5.0, 6, False
        for attempt in range(attempts):
            try:
                r = self.http.post(f"{GROQ_URL}/chat/completions", headers=self._headers(),
                                   json=body, timeout=180)
            except Exception as e:
                if self.fallback is not None:
                    return self._relay(prompt, json_mode, "Groq injoignable")
                if attempt < attempts - 1:
                    self._wait(delay, "Groq injoignable")
                    delay = min(delay * 2, 60.0)
                    continue
                raise LLMError(f"Groq injoignable : {e}") from e
            if r.status_code == 200:
                self._read_limits(r)
                try:
                    choice = r.json()["choices"][0]
                    text = choice["message"].get("content") or ""
                    finish = choice.get("finish_reason")
                except (ValueError, KeyError, IndexError, TypeError):
                    text, finish = "", None
                if finish == "length" and not longer:
                    # Cut mid-file: a truncated file would only send the fix loop chasing it.
                    longer = True
                    body["max_completion_tokens"] = 32768
                    continue
                if finish == "length":
                    raise LLMError("Réponse de Groq tronquée (limite de sortie atteinte).")
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
            if r.status_code == 400 and ("reasoning" in detail.lower()) and "reasoning_effort" in body:
                body.pop("reasoning_effort", None)       # a model that does not take it
                body.pop("include_reasoning", None)
                continue
            if r.status_code == 413:
                # Bigger than the whole per-minute budget: waiting can never make it fit.
                if self.fallback is not None:
                    return self._relay(prompt, json_mode, "requête trop grosse pour la limite Groq")
                raise LLMError("Requête trop grosse pour la limite de Groq par minute. Choisissez « Groq + "
                               "relais local » ou « IA locale » dans les réglages du Studio.")
            if r.status_code == 429:
                back = self._daily_exhausted(r)
                if back:
                    self.exhausted_until = time.monotonic() + back
                    if self.fallback is not None:
                        return self._relay(prompt, json_mode, "quota quotidien Groq épuisé")
                    raise LLMError(self._daily_message(back))
            if r.status_code in (429, 500, 502, 503, 504) and self.fallback is not None \
                    and self.role in self.RELAY_ROLES:
                return self._relay(prompt, json_mode,
                                   "limite Groq atteinte" if r.status_code == 429 else "Groq indisponible")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                wait = delay
                try:
                    wait = min(90.0, float(r.headers.get("retry-after") or delay) + 0.5)
                except (TypeError, ValueError):
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


LOCAL_URL = "http://127.0.0.1:11434"
#: Coding models first; the first one installed in Ollama wins.
LOCAL_PREFERRED = ["qwen3-coder", "qwen2.5-coder", "devstral", "gemma4", "qwen3", "deepseek-coder",
                   "codellama", "llama3"]
LOCAL_MAX_CTX = 32768


class LocalLLM:
    """The Studio's coder on this PC, through Ollama (no quota, no key, slower).

    Ollama's defaults do not fit the Studio, and it fails SILENTLY: a prompt
    longer than the context window is cut without any error (only
    `prompt_eval_count`, stuck at the window size, tells). So:
      • the window is sized from the prompt (16k, or 32k for a big one), and a
        prompt that still filled it is retried larger, or refused — never used;
      • an answer cut by the output budget (`done_reason: length`) is retried
        with a bigger budget, never written as a half file;
      • JSON answers are constrained by a JSON schema (`format`), not merely
        asked for — a small model cannot produce an invalid plan;
      • the answer is streamed, so ARRÊTER stops it mid-sentence, and a model
        silent for 3 minutes is treated as stuck.
    A model that can think does it in Ollama's separate `thinking` field — some
    (Qwen3) leak their reasoning into the answer when told not to — so
    thinking is on by default when the model supports it.
    """

    def __init__(self, model: str | None = None, url: str | None = None, think: bool | None = None,
                 num_ctx: int = 16384, http=None) -> None:
        self.url = (url or LOCAL_URL).rstrip("/")
        self.requested = (model or "").strip() or None
        self.think = think
        self.num_ctx = num_ctx
        self._model: str | None = None
        self._http = http
        self.on_wait = None
        self.should_stop = None
        self.role = "write"
        self.schema = None
        self.stats = {"calls": 0, "seconds": 0.0, "tokens": 0}

    @property
    def http(self):
        if self._http is None:
            import requests
            self._http = requests
        return self._http

    def _get(self, path: str, body: dict | None = None):
        try:
            if body is None:
                return self.http.get(f"{self.url}{path}", timeout=10)
            return self.http.post(f"{self.url}{path}", json=body, timeout=30)
        except Exception as e:
            raise LLMError(f"Ollama ne répond pas sur {self.url} : lancez Ollama ({type(e).__name__}).") from e

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        r = self._get("/api/tags")
        names = [m.get("name", "") for m in (r.json().get("models") or [])] if r.ok else []
        if not names:
            raise LLMError("Aucun modèle dans Ollama : installez-en un (ollama pull qwen2.5-coder:14b).")
        if self.requested:
            match = [n for n in names if n == self.requested or n.split(":")[0] == self.requested]
            if not match:
                raise LLMError(f"Le modèle local « {self.requested} » n'est pas installé dans Ollama.")
            self._model = match[0]
        else:
            self._model = next((n for p in LOCAL_PREFERRED for n in names if n.startswith(p)), names[0])
        if self.think is None:
            show = self._get("/api/show", {"model": self._model})
            caps = (show.json().get("capabilities") or []) if show.ok else []
            self.think = "thinking" in caps
        return self._model

    def _post(self, body: dict) -> tuple[int, str, dict]:
        """(status, content, final stats) — streamed, stoppable, with an idle timeout."""
        try:
            r = self.http.post(f"{self.url}/api/chat", json=body, timeout=(10, 180), stream=True)
        except Exception as e:
            raise LLMError(f"Ollama ne répond pas : {type(e).__name__}") from e
        if r.status_code != 200 or not hasattr(r, "iter_lines"):
            if r.status_code != 200:
                return r.status_code, r.text, {}
            data = r.json()                                  # a non-streaming stand-in (tests)
            return 200, (data.get("message") or {}).get("content") or "", data
        parts: list[str] = []
        last: dict = {}
        try:
            for raw in r.iter_lines():
                if callable(self.should_stop) and self.should_stop():
                    raise LLMError("Arrêté pendant la réponse du modèle local.")
                if not raw:
                    continue
                d = json.loads(raw)
                if d.get("error"):
                    raise LLMError(f"Ollama : {str(d['error'])[:200]}")
                parts.append((d.get("message") or {}).get("content") or "")
                if d.get("done"):
                    last = d
                    break
        except LLMError:
            raise
        except Exception as e:                               # read timeout: 3 minutes without a token
            raise LLMError(f"Le modèle local ne répond plus ({type(e).__name__}).") from e
        finally:
            try:
                r.close()
            except Exception:
                pass
        return 200, "".join(parts), last

    def complete(self, prompt: str, json_mode: bool = False) -> str:
        model = self.model
        role = self.role if self.role in OUT_BUDGET else "write"
        predict = OUT_BUDGET[role] * 2 + (4096 if self.think else 0)
        need = estimate_tokens(prompt) + OUT_BUDGET[role] + (2048 if self.think else 0)
        ctx = self.num_ctx if need <= self.num_ctx else LOCAL_MAX_CTX
        body = {"model": model, "stream": True, "keep_alive": "30m", "think": bool(self.think),
                "messages": [{"role": "system", "content": "You are an expert software engineer. "
                              "Follow the output format exactly."},
                             {"role": "user", "content": prompt}],
                "options": {"temperature": 0.2, "num_ctx": ctx, "num_predict": predict}}
        if json_mode:
            body["format"] = self.schema or "json"
        for _attempt in range(4):
            if callable(self.should_stop) and self.should_stop():
                raise LLMError("Arrêté pendant l'attente du modèle local.")
            t0 = time.monotonic()
            status, text, data = self._post(body)
            if status == 400 and "think" in text.lower() and body.get("think"):
                body["think"] = self.think = False          # a model without thinking
                continue
            if status != 200:
                raise LLMError(f"Ollama a répondu {status} : {text[:200]}")
            self.stats["calls"] += 1
            self.stats["seconds"] += time.monotonic() - t0
            self.stats["tokens"] += int(data.get("eval_count") or 0)
            used = int(data.get("prompt_eval_count") or 0)
            if used >= body["options"]["num_ctx"] - 8:
                # The prompt filled the window: Ollama cut it without saying so.
                if body["options"]["num_ctx"] < LOCAL_MAX_CTX:
                    body["options"]["num_ctx"] = LOCAL_MAX_CTX
                    continue
                raise LLMError(f"Demande trop longue pour le modèle local ({used} jetons au moins, "
                               f"contexte {LOCAL_MAX_CTX}) : elle serait tronquée.")
            if data.get("done_reason") == "length":
                if body["options"]["num_predict"] < 32768:
                    body["options"]["num_predict"] = min(32768, body["options"]["num_predict"] * 2)
                    continue
                raise LLMError("Réponse du modèle local tronquée (budget de sortie atteint).")
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
            if text.strip():
                return text
        raise LLMError("Réponse vide du modèle local.")


def local_available(url: str | None = None) -> bool:
    try:
        import requests
        return requests.get(f"{(url or LOCAL_URL).rstrip('/')}/api/tags", timeout=2).ok
    except Exception:
        return False


def make_llm(cfg: dict):
    """Which model writes the code — `provider` in the Studio's config:

        auto        Groq when a Groq key exists, Gemini otherwise (the default)
        groq        Groq only
        groq+local  Groq, and the local model takes over whenever Groq says
                    "rate limit" instead of waiting for it
        local       the local model only (Ollama): no quota, slower
        gemini      Gemini only
    """
    provider = str(cfg.get("provider") or "auto").lower()
    local = lambda: LocalLLM(cfg.get("local_model") or None, cfg.get("local_url") or None,  # noqa: E731
                             cfg.get("local_think"))
    if provider in ("local", "ollama"):
        return local()
    if provider == "groq+local":
        return GroqLLM(cfg.get("groq_model") or None, fallback=local())
    if provider == "groq" or (provider == "auto" and groq_key()):
        return GroqLLM(cfg.get("groq_model") or None)
    return StudioLLM(cfg.get("model") or None)


def describe(llm) -> str:
    if isinstance(llm, LocalLLM):
        return f"Local · {llm.model}" + (" (réflexion)" if llm.think else "")
    if isinstance(llm, GroqLLM):
        relay = f" + relais local {describe(llm.fallback)}" if llm.fallback is not None else ""
        return f"Groq · {llm.model}{relay}"
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
- The program must never block waiting for keyboard input when run by the tests. A CLI takes its
  inputs as command-line arguments (argparse); run without arguments it prints a useful default
  (help, a demo, a table…) and exits. Use input() menus ONLY if the user asked for an interactive
  program — and then keep the logic in importable functions the tests call directly.
- Never call str.format() or an f-string on a template that contains literal braces (CSS, JS,
  JSON): `{ color: red }` becomes a KeyError. Use string.Template, concatenation, or double the
  braces.
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
  Play each check in your head before writing it: its expectations describe the page AFTER its
  own steps (e.g. add one item then delete it -> expect count 0, not 1).
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

FIX_PROMPT = """You are fixing the project "{title}" in Jarvis Code Studio.

The check `{check}` failed. Error type: {kind}.
Output:
{output}

{located}
Project files (path: size): {files}

Relevant file contents:
{contents}
{spec}
Previous attempts that did NOT fix it (what was changed, and the error it was meant to fix):
{history}

Work like a careful developer:
1. "explanation": walk through what the failing code ACTUALLY does on the failing case, step by
   step, and say where it diverges from what the check expects.
2. "diagnosis": the root cause in one sentence, in French.
3. "files": the fix. If previous attempts are listed above, they did NOT work: do something
   different, do not repeat them.
Fix the CODE. Change a test only if it contradicts the acceptance criterion it is named after.
{check_rule}
Return ONLY a JSON object:
{{
  "explanation": "step-by-step trace of the failing case",
  "diagnosis": "one sentence, in French",
  "files": [{{"path": "relative/path", "content": "the COMPLETE new content of the file"}}],
  "needs_dependency": "pypi package name if a third-party package is genuinely missing, else empty",
  "give_up": "non-empty explanation ONLY if this cannot be fixed without the user"
}}
Change at most 3 files."""


# ── JSON answers: a schema (constrained decoding where the provider has it) and
#    one repairing retry everywhere — an unusable answer is sent back with the
#    exact problem instead of failing the whole job ──

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
_CHECK = {"type": "object", "properties": {
    "id": _STR, "name": _STR,
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["click", "fill", "press", "check", "uncheck", "select", "wait"]},
        "selector": _STR, "value": _STR, "key": _STR, "ms": {"type": "integer"}}, "required": ["action"]}},
    "expect": {"type": "array", "items": {"type": "object", "properties": {
        "selector": _STR, "text": _STR, "count": {"type": "integer"}, "value": _STR,
        "visible": {"type": "boolean"}}, "required": ["selector"]}}},
    "required": ["id", "steps", "expect"]}
_ACCEPTANCE = {"type": "array", "items": {"type": "object", "properties": {
    "id": _STR, "criterion": _STR, "verify": {"type": "string", "enum": ["test", "web", "manual"]}},
    "required": ["id", "criterion", "verify"]}}
_TASKS = {"type": "array", "items": {"type": "object", "properties": {"title": _STR, "files": _STRS},
                                     "required": ["title", "files"]}}
PLAN_SCHEMA = {"type": "object", "properties": {
    "project_name": _STR, "title": _STR, "summary": _STR,
    "kind": {"type": "string", "enum": ["web", "script", "cli", "gui", "library"]},
    "stack": _STRS, "entry_point": _STR, "run_command": _STR, "test_command": _STR,
    "dependencies": _STRS, "features": _STRS, "acceptance": _ACCEPTANCE,
    "web_checks": {"type": "array", "items": _CHECK}, "tasks": _TASKS,
    "files": {"type": "array", "items": {"type": "object", "properties": {"path": _STR, "purpose": _STR},
                                         "required": ["path", "purpose"]}}},
    "required": ["project_name", "title", "summary", "kind", "entry_point", "run_command", "test_command",
                 "dependencies", "features", "acceptance", "web_checks", "tasks", "files"]}
CHANGE_SCHEMA = {"type": "object", "properties": {
    "summary": _STR, "test_command": _STR, "run_command": _STR, "dependencies": _STRS,
    "acceptance": _ACCEPTANCE, "web_checks": {"type": "array", "items": _CHECK}, "tasks": _TASKS,
    "files": {"type": "array", "items": {"type": "object", "properties": {
        "path": _STR, "action": {"type": "string", "enum": ["create", "modify", "delete"]}, "purpose": _STR},
        "required": ["path", "action", "purpose"]}}},
    "required": ["summary", "test_command", "run_command", "dependencies", "acceptance", "web_checks",
                 "tasks", "files"]}
_FILES = {"type": "array", "items": {"type": "object", "properties": {"path": _STR, "content": _STR},
                                     "required": ["path", "content"]}}
FIX_SCHEMA = {"type": "object", "properties": {
    "explanation": _STR, "diagnosis": _STR, "files": _FILES,
    "check_fix": {"type": "object", "properties": {
        "id": _STR, "reason": _STR, "steps": _CHECK["properties"]["steps"],
        "expect": _CHECK["properties"]["expect"]}, "required": ["id", "reason", "steps", "expect"]},
    "needs_dependency": _STR, "give_up": _STR},
    "required": ["explanation", "diagnosis", "files", "needs_dependency", "give_up"]}
COVER_SCHEMA = {"type": "object", "properties": {"files": _FILES}, "required": ["files"]}

REPAIR = ("\n\nYOUR PREVIOUS ANSWER WAS NOT USABLE: {problem}\n"
          "Answer again with ONLY the JSON object described above, complete and valid.")


def _has_files(data: dict) -> str:
    files = data.get("files")
    if not isinstance(files, list) or not any(isinstance(f, dict) and str(f.get("path", "")).strip()
                                              for f in files):
        return "\"files\" must be a non-empty list of objects with a \"path\"."
    return ""


def _files_list(data: dict) -> str:
    files = data.get("files", [])
    if not isinstance(files, list) or any(not isinstance(f, dict) for f in files):
        return "\"files\" must be a list of {\"path\", \"content\"} objects."
    return ""


def ask_json(llm, prompt: str, role: str, schema: dict | None = None, check=None) -> dict:
    """One JSON answer for `role`; an unusable one is sent back once with the reason."""
    problem = ""
    for _attempt in range(2):
        configure(llm, role, schema)
        text = llm.complete(prompt + (REPAIR.format(problem=problem[:300]) if problem else ""), json_mode=True)
        try:
            data = parse_json(text)
            problem = check(data) if check else ""
        except LLMError as e:
            problem = str(e)
        if not problem:
            return data
    raise LLMError(f"Réponse du modèle inutilisable, même après une relance : {problem}")


def plan_new(llm, request: str) -> dict:
    return ask_json(llm, PLAN_PROMPT.format(request=request, rules=_RULES), "plan", PLAN_SCHEMA, _has_files)


def plan_change(llm, request: str, project_map: dict, files: list[dict]) -> dict:
    listing = "\n".join(f"{f['path']}: {f['size']}" for f in files[:200]) or "(vide)"
    return ask_json(llm, CHANGE_PROMPT.format(
        project_map=json.dumps(project_map, ensure_ascii=False, indent=1)[:6000],
        files=listing, request=request, rules=_RULES), "plan", CHANGE_SCHEMA, _has_files)


def write_file(llm, plan: dict, path: str, purpose: str, context: str, current: str | None) -> str:
    cur = (f"Current content of {path} (modify it, keep what still applies):\n{current[:12000]}\n"
           if current else "")
    brief = {k: plan.get(k) for k in ("title", "summary", "kind", "stack", "entry_point",
                                      "run_command", "test_command", "features", "acceptance",
                                      "web_checks", "files")}
    configure(llm, "write")
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
    return ask_json(llm, COVER_PROMPT.format(
        title=title, missing="\n".join(f"- {c['id']}: {c['criterion']}" for c in missing),
        tests=tests[:12000] or NONE, code=code[:12000] or NONE), "cover", COVER_SCHEMA, _files_list)


CHECK_REVIEW_PROMPT = """A browser check of the project "{title}" keeps failing, and the fixer suspects the
CHECK rather than the code. Decide which one is wrong.

The acceptance criterion the check verifies: {criterion}
The check (each run starts from a freshly loaded page with an empty localStorage):
{check}
Its failure: {detail}

Relevant code:
{contents}

Play the steps in your head on the page as the code builds it. The CHECK is wrong only if its
expectations cannot be true after its own steps, whatever the code does (e.g. add one item then
delete it, and expect one item left). Otherwise the CODE is wrong.
Return ONLY a JSON object:
{{"verdict": "check" or "code", "reason": "one sentence, in French",
  "steps": [...corrected steps, same criterion — [] if verdict is "code"],
  "expect": [...corrected expectations — [] if verdict is "code"]}}"""

CHECK_REVIEW_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["check", "code"]}, "reason": _STR,
    "steps": _CHECK["properties"]["steps"], "expect": _CHECK["properties"]["expect"]},
    "required": ["verdict", "reason", "steps", "expect"]}


def review_check(llm, title: str, criterion: str, check: dict, detail: str, contents: str) -> dict:
    """One required question when a small model hints the check is wrong but did not
    say so in the optional field: is it the check or the code? (engine._fix)"""
    return ask_json(llm, CHECK_REVIEW_PROMPT.format(
        title=title, criterion=criterion or "?", check=json.dumps(check, ensure_ascii=False)[:2500],
        detail=detail[:600], contents=contents[:12000]), "fix", CHECK_REVIEW_SCHEMA,
        lambda d: "" if d.get("verdict") in ("check", "code") else "\"verdict\" must be \"check\" or \"code\".")


CHECK_RULE = """If, and ONLY if, a browser check is ITSELF impossible (its expectations cannot describe the
page after its own steps, whatever the code), add "check_fix": {"id": "<the check's id>", "reason":
"why it is impossible", "steps": [...], "expect": [...]} — the corrected check for the SAME criterion.
It is shown to the user as a change you made to a verification. Never use it to make a check easier."""


def fix(llm, title: str, check: str, kind: str, output: str, located: str,
        files: list[dict], contents: str, history: list[str], web_checks: list | None = None) -> dict:
    # The browser paths encode the user's criteria. The fixer changes the code to
    # satisfy them; it may correct a check only when the check itself is
    # impossible — and the engine shows that correction in the summary.
    spec = ("\nBrowser checks the page must pass:\n"
            + json.dumps(web_checks, ensure_ascii=False)[:4000] + "\n") if web_checks else ""
    return ask_json(llm, FIX_PROMPT.format(
        title=title, check=check, kind=kind, output=output, located=located,
        files=", ".join(f"{f['path']}: {f['size']}" for f in files[:120]),
        contents=contents[:24000], spec=spec, history="\n\n".join(history[-3:]) or "(aucune)",
        check_rule=CHECK_RULE if web_checks else ""), "fix", FIX_SCHEMA, _files_list)
