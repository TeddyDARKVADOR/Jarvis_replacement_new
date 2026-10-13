"""
offline/llm.py — un cerveau local : Ollama, avec appels d'outils.

Le modèle est le premier de cette liste qu'Ollama a ET qui déclare la capacité
`tools` (/api/show) : réglage `offline_model` d'api_keys.json, puis
qwen3:4b-instruct (celui de la cognition), gemma4:12b, llama3.2, mistral, puis
n'importe quel modèle capable d'outils. Hors réglage explicite, un modèle de
plus de MAX_AUTO_BYTES est écarté : qwen3.6 (23 Go) ne tient pas dans les 12 Go
de la RTX 3060 et a mis 41,6 s à répondre, mesuré le 2026-10-13. La réflexion (`think`)
est coupée : à la voix, une réponse en 2 s vaut mieux qu'une meilleure en 60.

API native /api/chat, pas l'API compatible OpenAI : `think` et `keep_alive`
n'existent que là. Même code sous Windows, Linux et macOS.
"""
from __future__ import annotations

import json
import re

import requests

OLLAMA = "http://127.0.0.1:11434"
PREFERRED = ("qwen3:4b-instruct", "gemma4:12b", "llama3.2:latest", "mistral:latest")
MAX_AUTO_BYTES = 10 * 1024 ** 3
MAX_TOOL_ROUNDS = 4
TIMEOUT_S = 90
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _show(model: str, base: str) -> dict:
    try:
        r = requests.post(f"{base}/api/show", json={"model": model}, timeout=5)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def pick_model(wanted: str | None = None, base: str = OLLAMA) -> tuple[str, list[str]] | None:
    """(modèle, capacités) du premier modèle capable d'outils ; None sans Ollama."""
    try:
        models = requests.get(f"{base}/api/tags", timeout=3).json().get("models", [])
    except Exception:
        return None
    sizes = {m["name"]: m.get("size", 0) for m in models}
    names = sorted(sizes, key=lambda n: sizes[n])
    order = ([wanted] if wanted else []) + list(PREFERRED) + names
    seen = set()
    for name in order:
        if not name or name in seen:
            continue
        seen.add(name)
        full = name if name in sizes else (f"{name}:latest" if f"{name}:latest" in sizes else None)
        if not full or (name != wanted and sizes[full] > MAX_AUTO_BYTES):
            continue
        caps = _show(full, base).get("capabilities") or []
        if "tools" in caps:
            return full, caps
    return None


def to_json_schema(gemini_schema) -> dict:
    """Déclaration Gemini (types en majuscules) → JSON Schema (minuscules)."""
    if isinstance(gemini_schema, dict):
        out = {}
        for k, v in gemini_schema.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.lower()
            else:
                out[k] = to_json_schema(v)
        return out
    if isinstance(gemini_schema, list):
        return [to_json_schema(v) for v in gemini_schema]
    return gemini_schema


class Brain:
    def __init__(self, model: str, caps: list[str], system: str, tools: dict | None = None,
                 base: str = OLLAMA, post=None):
        self.model, self.caps, self.base = model, caps, base
        self.tools = tools or {}             # nom -> (déclaration Gemini, fonction(args) -> str)
        self.history: list[dict] = [{"role": "system", "content": system}]
        self._post = post or (lambda url, payload: requests.post(url, json=payload, timeout=TIMEOUT_S).json())

    def warm(self) -> None:
        """Charge le modèle en mémoire maintenant, pas à la première question."""
        try:
            requests.post(f"{self.base}/api/generate", json={"model": self.model, "keep_alive": "15m"},
                          timeout=TIMEOUT_S)
        except Exception:
            pass

    def _declarations(self) -> list[dict]:
        return [{"type": "function", "function": {
                    "name": name, "description": decl.get("description", "")[:600],
                    "parameters": to_json_schema(decl.get("parameters") or {"type": "OBJECT", "properties": {}})}}
                for name, (decl, _fn) in self.tools.items()]

    def ask(self, text: str, on_tool=None) -> str:
        self.history.append({"role": "user", "content": text})
        self.history = self.history[:1] + self.history[-16:]     # contexte borné
        payload = {"model": self.model, "messages": self.history, "stream": False,
                   "keep_alive": "15m", "options": {"temperature": 0.4, "num_predict": 300}}
        if self.tools:
            payload["tools"] = self._declarations()
        if "thinking" in self.caps:
            payload["think"] = False
        for _ in range(MAX_TOOL_ROUNDS + 1):
            msg = (self._post(f"{self.base}/api/chat", payload) or {}).get("message") or {}
            calls = msg.get("tool_calls") or []
            self.history.append({"role": "assistant", "content": msg.get("content", ""),
                                 **({"tool_calls": calls} if calls else {})})
            if not calls:
                return _THINK.sub("", msg.get("content", "")).strip()
            for call in calls:
                fn = call.get("function") or {}
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if on_tool:
                    on_tool(name, args)
                entry = self.tools.get(name)
                try:
                    result = entry[1](args) if entry else f"Outil inconnu : {name}"
                except Exception as e:
                    result = f"L'outil {name} a échoué : {e}"
                self.history.append({"role": "tool", "content": str(result)[:2000], "tool_name": name})
            payload["messages"] = self.history
        return "Je n'ai pas réussi à terminer cette demande hors-ligne."
