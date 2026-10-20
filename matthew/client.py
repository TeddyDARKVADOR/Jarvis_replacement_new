"""
matthew/client.py — un appel /chat/completions compatible OpenAI, et ses tokens.

Ollama, vLLM, llama.cpp, Groq parlent tous ce dialecte : Matthew servi chez
nous, ou un professeur ouvert ailleurs, passent par ici. Chaque appel ajoute
une ligne à usage.jsonl : ce sont les tokens de Matthew, à lui, comptés à
part de ceux de Gemini (core/token_usage.py).

Le transport est injectable (selftest : aucun réseau).
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from matthew import data_dir

_THINK = re.compile(r"<think>.*?</think>", re.S)
_lock = threading.Lock()


class ClientError(RuntimeError):
    def __init__(self, msg: str, *, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class QuotaError(ClientError):
    pass


def _requests_post(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    import requests
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise ClientError(f"réseau : {e}")
    if r.status_code == 429:
        try:
            wait = float(r.headers.get("retry-after") or 0) or None
        except ValueError:
            wait = None
        raise QuotaError(f"quota (429) : {r.text[:160]}", retry_after=wait)
    if r.status_code >= 400:
        raise ClientError(f"HTTP {r.status_code} : {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        raise ClientError("réponse illisible")


class Client:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, *,
                 timeout: float = 120.0, purpose: str = "matthew", post=None,
                 usage_root: Path | None = None, extra: dict | None = None):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.timeout, self.purpose = timeout, purpose
        self._post = post or _requests_post
        self._usage_root = usage_root
        self.extra = extra or {}

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *,
             max_tokens: int = 700, temperature: float = 0.3, json_mode: bool = False) -> dict:
        """Le message de l'assistant ({content, tool_calls?}), réflexion retirée."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, **self.extra}
        if tools:
            payload["tools"] = tools
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        data = self._post(f"{self.base_url}/chat/completions", headers, payload, self.timeout)
        latency = time.monotonic() - t0
        try:
            msg = dict(data["choices"][0]["message"])
        except (KeyError, IndexError, TypeError):
            raise ClientError("réponse sans message")
        msg["content"] = _THINK.sub("", msg.get("content") or "").strip()
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                try:
                    fn["arguments"] = json.loads(fn["arguments"] or "{}")
                except ValueError:
                    fn["arguments"] = {"_invalid_json": fn["arguments"]}
        record_usage(self.model, self.purpose, data.get("usage") or {}, latency, self._usage_root)
        msg["_latency"] = latency
        return msg


def record_usage(model: str, purpose: str, usage: dict, latency: float,
                 root: Path | None = None) -> None:
    line = {"ts": datetime.now().isoformat(timespec="seconds"), "model": model,
            "purpose": purpose, "in": int(usage.get("prompt_tokens") or 0),
            "out": int(usage.get("completion_tokens") or 0), "latency_s": round(latency, 3)}
    path = (root or data_dir()) / "usage.jsonl"
    try:
        with _lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass


def usage_summary(root: Path | None = None, day: str | None = None) -> dict:
    """Tokens par usage (matthew = les siens ; teacher = l'école) ; `day` AAAA-MM-JJ filtre."""
    out: dict = {}
    path = (root or data_dir()) / "usage.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if day and not r.get("ts", "").startswith(day):
            continue
        s = out.setdefault(r.get("purpose", "?"), {"calls": 0, "in": 0, "out": 0})
        s["calls"] += 1
        s["in"] += r.get("in", 0)
        s["out"] += r.get("out", 0)
    return out


def to_openai_tools(declarations: list[dict]) -> list[dict]:
    """Déclarations Gemini (types en MAJUSCULES) → outils OpenAI / JSON Schema."""
    def lower(s):
        if isinstance(s, dict):
            return {k: (v.lower() if k == "type" and isinstance(v, str) else lower(v))
                    for k, v in s.items()}
        if isinstance(s, list):
            return [lower(v) for v in s]
        return s
    return [{"type": "function", "function": {
                "name": d["name"], "description": str(d.get("description", ""))[:400],
                "parameters": lower(d.get("parameters") or {"type": "OBJECT", "properties": {}})}}
            for d in declarations if d.get("name")]
