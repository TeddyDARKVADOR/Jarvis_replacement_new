"""
meeting/brain.py — le modèle qui résume, en bibliothèque standard.

Pourquoi pas cognition/providers.py : cognition.selftest n°2 fige la liste de
ceux qui importent cognition, et ce paquet doit pouvoir être retiré seul
(contrat V2). Trois fournisseurs, dans cet ordre (réglable :
« meeting_brain_order » dans api_keys.json) :

  gemini   gemini_api_key — plusieurs Flash-Lite, chacun son compteur gratuit
           (500 requêtes par jour et par modèle, lu dans les 429 de Google le
           2026-10-16) ; ceux que le registre commun (quota/) dit épuisés sont
           sautés ;
  groq     groq_api_key — openai/gpt-oss-120b (8 000 tokens par minute en
           gratuit : summarize.py découpe en conséquence) ;
  cloud    Ollama Cloud (gpt-oss:120b-cloud), par l'Ollama local : plan
           gratuit à crédits mensuels, contexte de 128k, 0,9 s (mesuré) ;
  local    Ollama sur 127.0.0.1:11434 — le meilleur modèle installé (ou
           offline_model), par l'API native : contexte de 16k (l'endpoint
           compatible OpenAI s'arrête à la valeur par défaut, 4k, et tronquerait
           une réunion) et sans le « raisonnement » de Qwen3. Rien ne sort du
           poste ; lent sur un portable sans GPU, mais le compte rendu n'est
           pas pressé.

Les clés viennent de config/api_keys.json (memory.config_manager), jamais du
poste client : en mode client, c'est Oracle qui résume (server/assist.py).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
OLLAMA = "http://127.0.0.1:11434"
#: Préférence parmi les modèles installés : les plus capables d'abord, à taille raisonnable pour un CPU.
LOCAL_PREFERENCE = ("qwen3:14b", "qwen3:8b", "qwen2.5:14b", "gemma3:12b", "llama3.1:8b", "qwen2.5:7b",
                    "mistral:7b", "qwen3:4b", "qwen3:4b-instruct")
LOCAL_CTX = 16384
CLOUD_MODEL = "gpt-oss:120b-cloud"          # Ollama Cloud, plan gratuit (crédits mensuels) : 0,9 s, mesuré le 2026-10-16
DEFAULT_ORDER = ("gemini", "cloud", "groq", "local")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class BrainError(RuntimeError):
    pass


def _quota(fn: str, *args):
    try:
        import quota
        return getattr(quota, fn)(*args)
    except Exception:
        return None


def pick_local(installed: list[str], wanted: str = "") -> str | None:
    """Le modèle local à utiliser : celui demandé s'il est installé, sinon le préféré disponible. Pur."""
    if wanted and wanted in installed:
        return wanted
    for name in LOCAL_PREFERENCE:
        if name in installed:
            return name
    chat = [m for m in installed if not re.search(r"embed|coder|vision|llava|whisper", m)]
    return chat[0] if chat else None


@dataclass
class Brain:
    settings: dict
    timeout: float = 120.0
    local_timeout: float = 1800.0                 # un CPU de portable : un long compte rendu prend des minutes
    transport: object = None                      # (url, headers, body, timeout) -> (status, bytes) — pour les tests
    used: str = ""

    def providers(self) -> list[tuple[str, str]]:
        """(fournisseur, modèle) dans l'ordre d'essai."""
        out = []
        for name in self.settings.get("meeting_brain_order") or DEFAULT_ORDER:
            if name == "gemini" and (self.settings.get("gemini_api_key") or "").strip():
                first = (self.settings.get("meeting_gemini_model") or "").strip()
                models = ([first] if first else []) + [m for m in GEMINI_MODELS if m != first]
                out += [("gemini", m) for m in models]
            elif name == "groq" and (self.settings.get("groq_api_key") or "").strip():
                out.append(("groq", (self.settings.get("meeting_groq_model") or GROQ_MODEL).strip()))
            elif name == "cloud":
                out.append(("cloud", (self.settings.get("meeting_cloud_model") or CLOUD_MODEL).strip()))
            elif name == "local":
                out.append(("local", (self.settings.get("offline_model") or "").strip()))
        return out

    def _send(self, url: str, headers: dict, body: dict, timeout: float) -> tuple[int, bytes]:
        return (self.transport or _post)(url, headers, json.dumps(body).encode("utf-8"), timeout)

    def _local_models(self) -> list[str]:
        status, raw = (self.transport or _get)(f"{OLLAMA}/api/tags", {}, b"", 5)
        if status != 200:
            raise BrainError(f"Ollama : HTTP {status}")
        return [m.get("name", "") for m in json.loads(raw.decode("utf-8")).get("models", [])]

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        errors = []
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for name, model in self.providers():
            try:
                if name in ("local", "cloud"):
                    installed = self._local_models()
                    model = (model if model in installed else "") if name == "cloud" else pick_local(installed, model) or ""
                    if not model:
                        errors.append(f"{name} : modèle absent d'Ollama")
                        continue
                    status, raw = self._send(f"{OLLAMA}/api/chat", {"Content-Type": "application/json"},
                                             {"model": model, "messages": messages, "stream": False, "think": False,
                                              "options": {"num_ctx": LOCAL_CTX, "temperature": 0.2,
                                                          "num_predict": max_tokens}},
                                             self.local_timeout if name == "local" else self.timeout)
                else:
                    if name == "gemini" and _quota("allowed", model) is False:
                        errors.append(f"gemini/{model} : quota épuisé (registre)")
                        continue
                    key = self.settings["gemini_api_key" if name == "gemini" else "groq_api_key"].strip()
                    status, raw = self._send(GEMINI_URL if name == "gemini" else GROQ_URL,
                                             {"Content-Type": "application/json", "User-Agent": "matthew-assistant-meeting/1",
                                              "Authorization": f"Bearer {key}"},
                                             {"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                                              "messages": messages}, self.timeout)
            except Exception as e:
                errors.append(f"{name} : {type(e).__name__}")
                continue
            if status != 200:
                if name == "gemini" and status == 429:
                    _quota("note_error", model, "429 " + raw[:2000].decode("utf-8", "replace"))
                errors.append(f"{name}/{model} : HTTP {status}")
                continue
            try:
                data = json.loads(raw.decode("utf-8"))
                text = (data["message"]["content"] if name in ("local", "cloud")
                        else data["choices"][0]["message"]["content"]) or ""
            except (ValueError, KeyError, IndexError, TypeError):
                errors.append(f"{name} : réponse illisible")
                continue
            text = _THINK.sub("", text).strip()
            if text:
                if name == "gemini":
                    _quota("note_ok", model)
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


def _get(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
