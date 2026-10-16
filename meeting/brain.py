"""
meeting/brain.py — le modèle qui résume, en bibliothèque standard.

Pourquoi pas cognition/providers.py : cognition.selftest n°2 fige la liste de
ceux qui importent cognition, et ce paquet doit pouvoir être retiré seul
(contrat V2). Trois fournisseurs compatibles OpenAI, dans cet ordre :

  gemini   gemini_api_key — gemini-3.5-flash-lite : grand contexte, gratuit,
           3/3 au banc de Night Shift du 2026-10-16 ;
  groq     groq_api_key — openai/gpt-oss-120b (8 000 tokens par minute en
           gratuit : summarize.py découpe en conséquence) ;
  local    Ollama sur 127.0.0.1:11434, le modèle d'offline_model s'il est
           réglé — rien ne sort du poste.

Les clés viennent de config/api_keys.json (memory.config_manager), jamais du
poste client : en mode client, c'est Oracle qui résume (server/meeting_api.py).
Ordre réglable : « meeting_brain_order » dans api_keys.json.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini_api_key",
               "gemini-3.5-flash-lite"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "groq_api_key", "openai/gpt-oss-120b"),
    "local": ("http://127.0.0.1:11434/v1/chat/completions", "", "qwen3:4b-instruct"),
}
DEFAULT_ORDER = ("gemini", "groq", "local")


class BrainError(RuntimeError):
    pass


@dataclass
class Brain:
    settings: dict
    timeout: float = 120.0
    transport: object = None          # (url, headers, body, timeout) -> (status, bytes) — pour les tests
    used: str = ""

    def providers(self) -> list[tuple[str, str, str, str]]:
        order = self.settings.get("meeting_brain_order") or DEFAULT_ORDER
        out = []
        for name in order:
            if name not in PROVIDERS:
                continue
            url, key_name, model = PROVIDERS[name]
            key = (self.settings.get(key_name) or "").strip() if key_name else ""
            if key_name and not key:
                continue
            if name == "local":
                model = self.settings.get("offline_model") or model
            model = (self.settings.get(f"meeting_{name}_model") or model).strip()
            out.append((name, url, key, model))
        return out

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        errors = []
        for name, url, key, model in self.providers():
            body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}]}).encode("utf-8")
            headers = {"Content-Type": "application/json", "User-Agent": "jarvis-meeting/1"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            try:
                status, raw = (self.transport or _post)(url, headers, body, self.timeout)
            except Exception as e:
                errors.append(f"{name} : {type(e).__name__}")
                continue
            if status != 200:
                errors.append(f"{name} : HTTP {status}")
                continue
            try:
                text = json.loads(raw.decode("utf-8"))["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError):
                errors.append(f"{name} : réponse illisible")
                continue
            if text.strip():
                self.used = f"{name}/{model}"
                return text
            errors.append(f"{name} : réponse vide")
        raise BrainError(" ; ".join(errors) or "aucun modèle configuré (ni clé Gemini, ni Groq, ni Ollama)")


def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
