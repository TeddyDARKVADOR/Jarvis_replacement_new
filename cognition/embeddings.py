"""
cognition/embeddings.py — le sens d'un texte, en nombres, pour la preuve et le rappel.

La preuve et le rappel étaient lexicaux : « nurse » ne retrouvait jamais
« infirmière », et une traduction partait au second avis d'un autre modèle,
un appel réseau par fait. Un vecteur multilingue règle les deux, sur ce PC.

Fournisseurs, dans l'ordre (le premier qui répond) :
    local    Ollama, modèle `cognition_embedding_model` de api_keys.json
             (qwen3-embedding:0.6b par défaut : multilingue, ~50 ms par texte).
             Essayé seulement s'il est installé (sonde /api/tags, en cache).
    gemini   gemini-embedding-001, si une clé existe. Après un 429 ou une
             erreur, mis en pause 15 min.
    rien     None : l'appelant reste lexical. Jamais d'exception.

Les vecteurs portent le nom de leur modèle : on ne compare jamais deux modèles.
Mesuré le 2026-10-13 (qwen3-embedding:0.6b, FR → EN) : paraphrase ou traduction
0,55–0,66 ; sans rapport 0,19–0,31.
"""
from __future__ import annotations

import json
import math
import struct
import threading
import time
from pathlib import Path

#: Au-dessus : la valeur dit la même chose qu'une phrase de l'utilisateur.
SAME_MEANING = 0.50
#: En dessous : sans rapport. Entre les deux : on demande un second avis.
UNRELATED = 0.30

_OLLAMA = "http://127.0.0.1:11434"
_DEFAULT_LOCAL = "qwen3-embedding:0.6b"
_GEMINI_MODEL = "gemini-embedding-001"
_PAUSE_S = 900.0
_QUERY_INSTRUCTION = ("Instruct: Given a question from a user, retrieve the personal memory "
                      "that answers it\nQuery: ")


def _config() -> dict:
    try:
        from config import api_key_path
        path = api_key_path(Path(__file__).resolve().parent.parent)
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class Embedder:
    """Une chaîne de fournisseurs. `embed()` rend (modèle, vecteurs) ou None."""

    def __init__(self, cfg: dict | None = None, *, timeout: float = 20.0):
        cfg = _config() if cfg is None else cfg
        self.local_model = str(cfg.get("cognition_embedding_model") or _DEFAULT_LOCAL)
        self.gemini_key = str(cfg.get("gemini_api_key") or "")
        self.timeout = timeout
        self._local_ok: tuple[bool, float] | None = None     # (installé, vérifié à)
        self._paused_until = {"local": 0.0, "gemini": 0.0}
        self._lock = threading.Lock()

    # ── fournisseurs ──
    def _local_available(self) -> bool:
        now = time.monotonic()
        if self._local_ok and now - self._local_ok[1] < 60:
            return self._local_ok[0]
        ok = False
        try:
            import requests
            tags = requests.get(f"{_OLLAMA}/api/tags", timeout=0.5).json().get("models", [])
            names = {m.get("name", "") for m in tags} | {m.get("model", "") for m in tags}
            ok = self.local_model in names or f"{self.local_model}:latest" in names
        except Exception:
            ok = False
        self._local_ok = (ok, now)
        return ok

    def _local(self, texts: list[str]) -> list[list[float]]:
        import requests
        r = requests.post(f"{_OLLAMA}/api/embed", json={"model": self.local_model, "input": texts},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()["embeddings"]

    def _gemini(self, texts: list[str]) -> list[list[float]]:
        from google import genai
        client = genai.Client(api_key=self.gemini_key)        # gardé référencé jusqu'à la fin
        resp = client.models.embed_content(model=_GEMINI_MODEL, contents=texts)
        return [list(e.values) for e in resp.embeddings]

    def chain(self) -> list[str]:
        out = []
        if self._local_available():
            out.append("local")
        if self.gemini_key:
            out.append("gemini")
        return out

    def embed(self, texts: list[str]) -> tuple[str, list[list[float]]] | None:
        texts = [str(t or "")[:2000] for t in texts]
        if not texts:
            return None
        now = time.monotonic()
        for name in self.chain():
            if now < self._paused_until[name]:
                continue
            try:
                with self._lock:
                    vecs = self._local(texts) if name == "local" else self._gemini(texts)
                if len(vecs) == len(texts) and all(vecs):
                    model = self.local_model if name == "local" else _GEMINI_MODEL
                    return model, vecs
            except Exception:
                self._paused_until[name] = time.monotonic() + (_PAUSE_S if name == "gemini" else 60.0)
        return None

    def embed_query(self, query: str) -> tuple[str, list[float]] | None:
        """Le vecteur d'une REQUÊTE de rappel, à comparer à ceux des souvenirs.

        Qwen3-Embedding attend une instruction devant une requête : sans elle,
        « guitare » ressemblait à « city: Bordeaux » autant qu'au reste
        (0,4–0,6 partout). Avec, le pertinent monte (0,4–0,6) et le reste
        retombe (0,2–0,3). Gemini reçoit son type de tâche à la place."""
        now = time.monotonic()
        for name in self.chain():
            if now < self._paused_until[name]:
                continue
            try:
                with self._lock:
                    if name == "local":
                        vec = self._local([_QUERY_INSTRUCTION + str(query)[:500]])[0]
                        return self.local_model, vec
                    from google import genai
                    client = genai.Client(api_key=self.gemini_key)
                    resp = client.models.embed_content(
                        model=_GEMINI_MODEL, contents=[str(query)[:500]],
                        config={"task_type": "RETRIEVAL_QUERY"})
                    return _GEMINI_MODEL, list(resp.embeddings[0].values)
            except Exception:
                self._paused_until[name] = time.monotonic() + (_PAUSE_S if name == "gemini" else 60.0)
        return None

    def best_match(self, text: str, candidates: list[str]) -> float | None:
        """La plus forte similarité entre `text` et l'une des phrases, ou None."""
        candidates = [c for c in candidates if c and c.strip()]
        if not candidates:
            return None
        got = self.embed([text] + candidates)
        if not got:
            return None
        _, vecs = got
        return max(cosine(vecs[0], v) for v in vecs[1:])


_default: Embedder | None = None


def default() -> Embedder:
    global _default
    if _default is None:
        _default = Embedder()
    return _default
