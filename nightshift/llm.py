"""
nightshift/llm.py — un client minimal pour les API gratuites compatibles OpenAI.

    chain = Chain.from_env(budget=Budget(max_calls=12, max_tokens=60000, deadline=…))
    reply = chain.complete(messages, max_tokens=1800, reasoning_effort="low")

Pourquoi pas cognition/providers.py : nightshift n'importe rien de Matthew
(contrat V2), et cognition.selftest n°2 fige la liste de ceux qui importent
cognition. Ce client fait moins, en bibliothèque standard :

  CHAÎNE   groq : GROQ_MODEL s'il est défini, puis openai/gpt-oss-120b et
           qwen/qwen3.8-27b (en « preview » chez Groq : peut disparaître) ;
           puis gemini : GEMINI_MODEL, sinon gemini-3.5-flash-lite puis
           gemini-3.1-flash-lite ; en dernier recours openai/gpt-oss-20b.
           Ordre tiré du banc du 2026-10-16 (3 tâches réelles, patch appliqué
           et suites relancées) : gpt-oss-120b 3/3, gemini-3.5-flash-lite 3/3,
           qwen 2/3, gemini-3.1-flash-lite 1/3 (et des 503), gpt-oss-20b 0/3.
           Un fournisseur sans clé est sauté.
  RYTHME   8 000 tokens par minute et par modèle (Groq gratuit, confirmé le
           2026-10-14) : avant chaque appel, le client attend que la fenêtre
           glissante d'une minute ait la place, ou passe au modèle suivant.
  429      retry-after lu ; le modèle se repose ce temps-là. Court (≤ 30 s) et
           dans le budget : le client attend et réessaie une fois.
  ERREURS  401/403 : clé refusée, fournisseur écarté pour la nuit ; 404 ou
           modèle inconnu : modèle écarté ; délai, 5xx, réseau, réponse vide :
           modèle suivant.
  BUDGETS  appels, tokens, heure limite (NIGHTLY_MAX_LLM_CALLS,
           NIGHTLY_MAX_TOKENS, NIGHTLY_MAX_RUNTIME_MINUTES) : au plafond, arrêt
           propre (BudgetExhausted), et le rapport le dit.

La clé ne sort jamais d'ici : ni affichée, ni journalisée, ni recopiée dans une
erreur (tout message d'erreur passe par redact()).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from nightshift.run import redact

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")
GROQ_LAST = ("openai/gpt-oss-20b",)          # après Gemini, sauf choisi par GROQ_MODEL
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
TPM = 8000
SHORT_RETRY_S = 30
CHARS_PER_TOKEN = 3.2


class LLMError(RuntimeError):
    pass


class BudgetExhausted(LLMError):
    pass


@dataclass
class Model:
    provider: str
    name: str
    url: str
    key_env: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.name}"


@dataclass
class Reply:
    text: str
    model: str
    tokens_in: int
    tokens_out: int


@dataclass
class Budget:
    max_calls: int = 12
    max_tokens: int = 60000
    deadline: float | None = None           # time.time() à ne pas dépasser
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    providers: dict = field(default_factory=dict)

    def check(self, estimate: int, now: float) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExhausted(f"plafond d'appels atteint ({self.max_calls})")
        if self.tokens_in + self.tokens_out + estimate > self.max_tokens:
            raise BudgetExhausted(f"plafond de tokens atteint ({self.max_tokens})")
        if self.deadline is not None and now >= self.deadline:
            raise BudgetExhausted("heure limite de la nuit atteinte")

    def record(self, model: str, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.providers[model] = self.providers.get(model, 0) + 1

    def usage(self) -> dict:
        return {"llm_calls": self.calls, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "providers": dict(self.providers)}


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""


def estimate_messages(messages: list[dict]) -> int:
    return int(sum(len(str(m.get("content", ""))) for m in messages) / CHARS_PER_TOKEN) + 8


class Chain:
    """Les modèles gratuits, dans l'ordre, sous budget. Voir le docstring du module."""

    def __init__(self, models: list[Model], env: dict, budget: Budget, transport=None, clock=time.time,
                 sleep=time.sleep, tpm: int = TPM, timeout: float = 90.0, log=print):
        self.models = models
        self.env = env
        self.budget = budget
        self.transport = transport or _urllib_transport
        self.clock, self.sleep = clock, sleep
        self.tpm, self.timeout, self.log = tpm, timeout, log
        self._window: dict[str, list[tuple[float, int]]] = {}
        self._paused: dict[str, float] = {}
        self._dropped: set[str] = set()          # modèles et fournisseurs écartés pour la nuit

    @classmethod
    def from_env(cls, env=None, budget: Budget | None = None, **kw) -> "Chain":
        env = dict(os.environ if env is None else env)
        first = (env.get("GROQ_MODEL") or "").strip()
        groq = [first] + [m for m in GROQ_MODELS if m != first] if first else list(GROQ_MODELS)
        gfirst = (env.get("GEMINI_MODEL") or "").strip()
        gemini = [gfirst] + [m for m in GEMINI_MODELS if m != gfirst] if gfirst else list(GEMINI_MODELS)
        last = [m for m in groq if m in GROQ_LAST and m != first]
        models = [Model("groq", m, GROQ_URL, "GROQ_API_KEY") for m in groq if m not in last]
        models += [Model("gemini", m, GEMINI_URL, "GEMINI_API_KEY") for m in gemini]
        models += [Model("groq", m, GROQ_URL, "GROQ_API_KEY") for m in last]
        return cls(models, env, budget or Budget(), **kw)

    def available(self) -> list[Model]:
        return [m for m in self.models if (self.env.get(m.key_env) or "").strip()
                and m.label not in self._dropped and m.provider not in self._dropped]

    # ── rythme ──
    def _wait_for_room(self, model: Model, estimate: int) -> float | None:
        """Secondes à attendre pour que l'appel tienne dans la minute glissante ; None si jamais."""
        if estimate > self.tpm:
            return None
        now = self.clock()
        window = [(t, n) for t, n in self._window.get(model.label, []) if now - t < 60]
        self._window[model.label] = window
        used = sum(n for _, n in window)
        if used + estimate <= self.tpm:
            return 0.0
        for t, n in window:                     # du plus ancien au plus récent
            used -= n
            if used + estimate <= self.tpm:
                return max(0.0, t + 60 - now)
        return 60.0

    def _time_left(self) -> float:
        return float("inf") if self.budget.deadline is None else self.budget.deadline - self.clock()

    # ── un appel ──
    def _body(self, model: Model, messages: list[dict], max_tokens: int, effort: str | None) -> dict:
        body: dict = {"model": model.name, "messages": messages, "temperature": 0.2}
        if model.provider == "groq":
            body["max_completion_tokens"] = max_tokens
            if effort and model.name.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = effort      # sans lui, gpt-oss peut tout dépenser en brouillon
        else:
            body["max_tokens"] = max_tokens
            if effort:
                body["reasoning_effort"] = effort
        return body

    def _call(self, model: Model, messages: list[dict], max_tokens: int, effort: str | None) -> Reply:
        estimate = estimate_messages(messages) + max_tokens
        self.budget.check(estimate, self.clock())
        key = (self.env.get(model.key_env) or "").strip()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                   "User-Agent": "matthew-assistant-nightshift/2"}
        body = json.dumps(self._body(model, messages, max_tokens, effort)).encode("utf-8")
        self.budget.calls += 1
        t0 = self.clock()
        status, rh, raw = self.transport(model.url, headers, body, self.timeout)
        took = self.clock() - t0
        if status == 429:
            try:
                retry = float(rh.get("retry-after") or 60)
            except ValueError:
                retry = 60.0
            self._paused[model.label] = self.clock() + retry
            raise _Retry(retry, f"{model.label} : quota (429), retry-after {retry:.0f} s")
        if status in (401, 403):
            self._dropped.add(model.provider)
            raise LLMError(f"{model.provider} : clé refusée (HTTP {status})")
        if status == 404 or b"model_not_found" in raw or b"does not exist" in raw:
            self._dropped.add(model.label)
            raise LLMError(f"{model.label} : modèle introuvable")
        if status != 200:
            raise LLMError(f"{model.label} : HTTP {status} {redact(raw[:160].decode('utf-8', 'replace'))}")
        data = json.loads(raw.decode("utf-8"))
        usage = data.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or estimate_messages(messages))
        tout = int(usage.get("completion_tokens") or 0)
        self._window.setdefault(model.label, []).append((self.clock(), tin + tout))
        self.budget.record(model.label, tin, tout)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        self.log(f"  {model.label} : {tin} + {tout} tokens, {took:.1f} s")
        if not text.strip():
            raise LLMError(f"{model.label} : réponse vide")
        return Reply(text, model.label, tin, tout)

    def complete(self, messages: list[dict], max_tokens: int = 1800, reasoning_effort: str | None = "low") -> Reply:
        """La première réponse non vide de la chaîne. LLMError si aucune, BudgetExhausted au plafond."""
        errors: list[str] = []
        for model in self.available():
            if model.label in self._dropped or model.provider in self._dropped:
                continue                               # écarté pendant cette boucle (clé refusée, modèle disparu)
            reply = self._try(model, messages, max_tokens, reasoning_effort, errors)
            if reply is not None:
                return reply
        if not errors:
            raise LLMError("aucun fournisseur disponible (pas de clé GROQ_API_KEY ni GEMINI_API_KEY)")
        raise LLMError(" ; ".join(errors)[:600])

    def _try(self, model: Model, messages: list[dict], max_tokens: int, effort: str | None,
             errors: list[str]) -> Reply | None:
        """Un modèle : un appel, et un seul nouvel essai après un 429 court. None : au suivant."""
        for _ in range(2):
            why = self._not_now(model, messages, max_tokens)
            if why:
                errors.append(why)
                return None
            try:
                return self._call(model, messages, max_tokens, effort)
            except BudgetExhausted:
                raise
            except _Retry as r:
                errors.append(str(r))
                if r.seconds > SHORT_RETRY_S or r.seconds >= self._time_left():
                    return None
                self.log(f"  {model.label} : 429, nouvel essai dans {r.seconds:.0f} s")
                self.sleep(r.seconds)
                self._paused.pop(model.label, None)
            except (LLMError, ValueError, KeyError) as e:
                errors.append(redact(str(e))[:200])
                return None
            except Exception as e:                     # délai, réseau : modèle suivant
                errors.append(f"{model.label} : {type(e).__name__}")
                return None
        return None

    def _not_now(self, model: Model, messages: list[dict], max_tokens: int) -> str | None:
        """Pourquoi ne pas appeler ce modèle maintenant — après avoir attendu la place, si l'attente tient."""
        if self.clock() < self._paused.get(model.label, 0):
            return f"{model.label} : au repos"
        wait = self._wait_for_room(model, estimate_messages(messages) + max_tokens)
        if wait is None:
            return f"{model.label} : prompt trop grand pour {self.tpm} tokens/min"
        if wait > 0:
            if wait > self._time_left():
                return f"{model.label} : attente de {wait:.0f} s au-delà de l'heure limite"
            self.log(f"  {model.label} : attente {wait:.0f} s (limite de tokens par minute)")
            self.sleep(wait)
        return None


class _Retry(LLMError):
    def __init__(self, seconds: float, message: str):
        super().__init__(message)
        self.seconds = seconds
