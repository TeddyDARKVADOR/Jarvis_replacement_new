"""
cognition/providers.py — une suite de générateurs de texte de secours.

Pour les tâches de fond de la cognition (extraction, réflexion, appraisal), pas
pour la conversation vocale, qui reste sur Gemini Live. L'idée : enchaîner les
offres GRATUITES et passer à la suivante dès qu'une est épuisée ou en panne.

    chain = ProviderChain.from_config()
    text, used = chain.complete("…", system="…", prefer="fast")

Fournisseurs reconnus (clé dans config/api_keys.json, sinon ignoré) :

    local        Ollama / llama.cpp / LM Studio — API compatible OpenAI sur
                 http://127.0.0.1:11434/v1 par défaut ; sans clé, sans quota,
                 utilisé seulement s'il répond.
    gemini       gemini_api_key        (SDK google-genai, déjà installé)
    groq         groq_api_key          (déjà utilisé par le Code Studio)
    cerebras     cerebras_api_key
    openrouter   openrouter_api_key    (modèles « :free »)
    mistral      mistral_api_key

Les quotas gratuits changent souvent et leurs chiffres publiés se contredisent
(recherche du 2026-10-13) : rien n'est codé en dur. Un 429 ou une erreur de
quota met le fournisseur en pause (durée annoncée par le serveur si elle est
donnée, sinon 60 s, doublée à chaque récidive, plafonnée à 1 h).

Les modèles par défaut sont des points de départ ; chacun se remplace dans
api_keys.json :  "cognition_providers": {"groq": {"model": "..."}, ...}
et l'ordre par  "cognition_provider_order": ["local", "groq", ...].
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# (nom, type, url de base, clé de config, modèle par défaut)
_CATALOG = {
    "local":      ("openai", "http://127.0.0.1:11434/v1", None, "qwen3:1.7b"),
    "groq":       ("openai", "https://api.groq.com/openai/v1", "groq_api_key", "openai/gpt-oss-20b"),
    "cerebras":   ("openai", "https://api.cerebras.ai/v1", "cerebras_api_key", "llama3.1-8b"),
    "gemini":     ("gemini", None, "gemini_api_key", "gemini-3.1-flash-lite"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "openrouter_api_key", "openrouter/free"),
    "mistral":    ("openai", "https://api.mistral.ai/v1", "mistral_api_key", "mistral-small-latest"),
}
# Paramètres ajoutés à la requête. gpt-oss « raisonne » avant de répondre : sans
# effort court, un petit budget de tokens part entièrement en brouillon et la
# réponse arrive vide (constaté à la sonde du 2026-10-13).
# Qwen3 local : même chose, mesuré sur ce PC (CPU, ~33 tok/s) : 6–7 s avec
# réflexion, < 1 s sans. Sans réflexion, la qualité baisse nettement : à
# réserver aux tâches simples (cf. prefer= dans complete()).
_EXTRA = {"groq": {"reasoning_effort": "low"}, "local": {"reasoning_effort": "none"}}
# Ordre par défaut : le plus rapide d'abord pour les tâches courtes.
DEFAULT_ORDER = ["local", "groq", "cerebras", "gemini", "openrouter", "mistral"]


class ProviderError(RuntimeError):
    def __init__(self, msg: str, *, retry_after: float | None = None, fatal: bool = False):
        super().__init__(msg)
        self.retry_after = retry_after
        self.fatal = fatal          # clé refusée, modèle inconnu : inutile de réessayer bientôt


class AllProvidersFailed(RuntimeError):
    pass


def _retry_after(text: str, headers=None) -> float | None:
    if headers is not None:
        v = headers.get("retry-after") if hasattr(headers, "get") else None
        try:
            return float(v) if v else None
        except ValueError:
            pass
    m = re.search(r"retry(?:Delay|_delay| in| after)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)\s*s",
                  text or "", re.I)
    return float(m.group(1)) if m else None


@dataclass
class Provider:
    name: str
    kind: str
    base_url: str | None
    model: str
    api_key: str | None = None
    timeout: float = 30.0
    extra: dict = field(default_factory=dict)
    # état
    paused_until: float = 0.0
    strikes: int = 0
    calls: int = 0
    failures: int = 0
    last_error: str = ""
    last_latency: float | None = None
    _client: object = field(default=None, repr=False)

    def available(self, now: float) -> bool:
        return now >= self.paused_until

    # Le transport est remplaçable : le selftest injecte un faux.
    def _call(self, prompt: str, system: str, max_tokens: int, temperature: float) -> str:
        if self.kind == "gemini":
            return self._call_gemini(prompt, system, max_tokens, temperature)
        return self._call_openai(prompt, system, max_tokens, temperature)

    def _call_openai(self, prompt, system, max_tokens, temperature) -> str:
        import requests
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=headers,
                              timeout=self.timeout,
                              json={"model": self.model, "messages": msgs,
                                    "max_tokens": max_tokens, "temperature": temperature,
                                    **self.extra})
        except requests.RequestException as e:
            raise ProviderError(f"réseau : {e}")
        if r.status_code == 429:
            raise ProviderError("quota (429)", retry_after=_retry_after(r.text, r.headers))
        if r.status_code in (401, 403):
            raise ProviderError(f"clé refusée ({r.status_code})", fatal=True)
        if r.status_code == 404:
            raise ProviderError(f"modèle introuvable : {self.model}", fatal=True)
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code} : {r.text[:200]}")
        try:
            choice = r.json()["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("réponse illisible")
        if not text.strip() and choice.get("finish_reason") == "length":
            raise ProviderError(f"budget de {max_tokens} tokens épuisé avant la réponse "
                                f"(modèle raisonnant ?)")
        # Les modèles « raisonnants » locaux exposent leur brouillon : on le retire.
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def _call_gemini(self, prompt, system, max_tokens, temperature) -> str:
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        try:
            resp = self._client.models.generate_content(
                model=self.model, contents=prompt,
                config={"system_instruction": system or None,
                        "max_output_tokens": max_tokens, "temperature": temperature})
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                raise ProviderError("quota Gemini", retry_after=_retry_after(msg))
            if "API key" in msg or "PERMISSION_DENIED" in msg:
                raise ProviderError("clé Gemini refusée", fatal=True)
            raise ProviderError(f"Gemini : {msg[:200]}")
        try:
            from core import token_usage
            token_usage.record_response(resp, self.model, "cognition")
        except Exception:
            pass
        return (resp.text or "").strip()


class ProviderChain:
    def __init__(self, providers: list[Provider], clock=time.monotonic):
        self.providers = providers
        self._clock = clock
        self._lock = threading.Lock()
        self._local_checked_at = -1e9
        self._local_ok = False

    @classmethod
    def from_config(cls, cfg: dict | None = None) -> "ProviderChain":
        if cfg is None:
            try:
                cfg = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cfg = {}
        overrides = cfg.get("cognition_providers") or {}
        order = cfg.get("cognition_provider_order") or DEFAULT_ORDER
        out = []
        for name in order:
            if name not in _CATALOG:
                continue
            kind, url, key_name, model = _CATALOG[name]
            o = overrides.get(name) or {}
            if o.get("enabled") is False:
                continue
            key = str(cfg.get(key_name) or "").strip() if key_name else None
            if key_name and not key:
                continue
            out.append(Provider(name=name, kind=kind, base_url=o.get("base_url", url),
                                model=o.get("model", model), api_key=key,
                                timeout=float(o.get("timeout", 30)),
                                extra=dict(o.get("extra", _EXTRA.get(name, {})))))
        return cls(out)

    def _local_alive(self, p: Provider) -> bool:
        """Sonde /models au plus une fois par minute : un serveur local éteint
        ne doit coûter qu'un échec de connexion, pas 30 s de délai par appel."""
        now = self._clock()
        if now - self._local_checked_at < 60:
            return self._local_ok
        self._local_checked_at = now
        try:
            import requests
            self._local_ok = requests.get(f"{p.base_url}/models", timeout=0.5).ok
        except Exception:
            self._local_ok = False
        return self._local_ok

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 400,
                 temperature: float = 0.3, prefer: str | None = None) -> tuple[str, str]:
        """(texte, nom du fournisseur). Lève AllProvidersFailed si tous échouent."""
        order = list(self.providers)
        if prefer:
            order.sort(key=lambda p: p.name != prefer)
        errors = []
        for p in order:
            now = self._clock()
            if not p.available(now):
                errors.append(f"{p.name}: en pause {p.paused_until - now:.0f}s")
                continue
            if p.name == "local" and not self._local_alive(p):
                errors.append("local: aucun serveur")
                continue
            t0 = self._clock()
            try:
                with self._lock:
                    p.calls += 1
                text = p._call(prompt, system, max_tokens, temperature)
                if not text:
                    raise ProviderError("réponse vide")
                p.strikes, p.last_latency = 0, self._clock() - t0
                return text, p.name
            except ProviderError as e:
                p.failures += 1
                p.last_error = str(e)
                p.strikes += 1
                if e.fatal:
                    pause = 3600.0
                elif e.retry_after:
                    pause = min(3600.0, e.retry_after + 1)
                else:
                    pause = min(3600.0, 60.0 * 2 ** (p.strikes - 1))
                p.paused_until = self._clock() + pause
                errors.append(f"{p.name}: {e}")
        raise AllProvidersFailed("; ".join(errors) or "aucun fournisseur configuré")

    def status(self) -> list[dict]:
        now = self._clock()
        return [{"name": p.name, "model": p.model, "available": p.available(now),
                 "paused_s": max(0, round(p.paused_until - now)), "calls": p.calls,
                 "failures": p.failures, "last_error": p.last_error,
                 "latency_s": None if p.last_latency is None else round(p.last_latency, 2)}
                for p in self.providers]


if __name__ == "__main__":     # python -m cognition.providers  — sonde réelle, une requête par fournisseur
    chain = ProviderChain.from_config()
    print(f"{len(chain.providers)} fournisseur(s) configuré(s) : "
          + ", ".join(p.name for p in chain.providers))
    for p in chain.providers:
        solo = ProviderChain([p])
        try:
            text, _ = solo.complete("Reply with exactly: OK", max_tokens=200, temperature=0)
            print(f"  {p.name:<11} {p.model:<28} OK  {p.last_latency:.2f}s  « {text[:30]} »")
        except AllProvidersFailed as e:
            print(f"  {p.name:<11} {p.model:<28} ÉCHEC  {e}")
