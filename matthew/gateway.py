"""
matthew/gateway.py — les tokens de Matthew : clés, budgets, limites, comptes.

Un token est un morceau de mot (≈ ¾ de mot en français) ; Matthew en lit (entrée) et en
écrit (sortie) à chaque réponse. Servi par la carte graphique de la maison, un token ne
coûte que de l'électricité — mais la carte ne sert qu'une demande à la fois, et Ollama
n'a aucune authentification : tout programme qui le joint peut s'en servir.

La passerelle se place devant Ollama, comme une API compatible OpenAI :

    python -m matthew.gateway serve [--host 127.0.0.1] [--port 11500]
    python -m matthew.gateway key create telephone [--budget 200000] [--rpm 20]
    python -m matthew.gateway key list | revoke telephone
    python -m matthew.gateway usage

    POST /v1/chat/completions   (Authorization: Bearer mk-…)   → Matthew via Ollama
    GET  /v1/models                                             → les modèles autorisés
    GET  /v1/usage                                              → les tokens de CETTE clé aujourd'hui

Chaque appareil (PC, téléphone, serveur) reçoit sa clé, montrée UNE fois puis gardée
seulement hachée (SHA-256) dans memory/matthew/keys.json. Par clé : un budget de tokens
par jour (entrée + sortie), un nombre de requêtes par minute, les modèles permis.
Dépassement → 429 avec Retry-After, comme les vraies API (modèle LiteLLM : clés
virtuelles, budgets, rpm). Chaque appel écrit ses tokens dans usage.jsonl (purpose
« gateway:<clé> ») : ils s'ajoutent aux tokens de Matthew sur la vitrine.

Pas de streaming pour l'instant (stream=true est servi en une fois). Écoute sur
127.0.0.1 par défaut ; l'ouvrir au téléphone passe par Tailscale, jamais par Internet.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import config, data_dir
from matthew.client import record_usage

DEFAULT_BUDGET = 200_000          # tokens par jour et par clé
DEFAULT_RPM = 20
MAX_BODY = 1_000_000


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class Keys:
    """Le registre des clés (hachées) et de leur consommation du jour."""

    def __init__(self, root: Path | None = None, clock=time.time):
        self.root = Path(root) if root else data_dir()
        self.path = self.root / "keys.json"
        self._clock = clock
        self._lock = threading.Lock()
        self._minute: dict[str, deque] = {}

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"keys": {}, "spent": {}}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")

    def create(self, name: str, budget: int = DEFAULT_BUDGET, rpm: int = DEFAULT_RPM,
               models: list[str] | None = None) -> str:
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("nom de clé : lettres, chiffres, - et _")
        key = "mk-" + secrets.token_urlsafe(24)
        with self._lock:
            data = self._load()
            data["keys"][name] = {"hash": _hash(key), "budget": int(budget), "rpm": int(rpm),
                                  "models": models or [config()["serve_model"]],
                                  "created": self._day(), "revoked": False}
            self._save(data)
        return key

    def revoke(self, name: str) -> bool:
        with self._lock:
            data = self._load()
            if name not in data["keys"]:
                return False
            data["keys"][name]["revoked"] = True
            self._save(data)
            return True

    def identify(self, key: str) -> tuple[str, dict] | None:
        h = _hash(key or "")
        for name, k in self._load()["keys"].items():
            if hmac.compare_digest(k["hash"], h) and not k.get("revoked"):
                return name, k
        return None

    def spent_today(self, name: str) -> int:
        return int(self._load().get("spent", {}).get(self._day(), {}).get(name, 0))

    def admit(self, name: str, k: dict) -> tuple[bool, str, int]:
        """(admis, raison, Retry-After en secondes)."""
        now = self._clock()
        q = self._minute.setdefault(name, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= k["rpm"]:
            return False, f"limite de {k['rpm']} requêtes par minute", int(61 - (now - q[0]))
        if self.spent_today(name) >= k["budget"]:
            midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0).timestamp() + 86400
            return False, f"budget du jour épuisé ({k['budget']:,} tokens)", int(midnight - now)
        q.append(now)
        return True, "", 0

    def charge(self, name: str, tokens: int) -> None:
        with self._lock:
            data = self._load()
            day = data.setdefault("spent", {}).setdefault(self._day(), {})
            day[name] = int(day.get(name, 0)) + int(tokens)
            # On ne garde que 31 jours de comptes.
            for d in sorted(data["spent"])[:-31]:
                del data["spent"][d]
            self._save(data)

    def table(self) -> list[dict]:
        data, day = self._load(), self._day()
        return [{"name": n, "budget": k["budget"], "rpm": k["rpm"], "models": k["models"],
                 "revoked": k.get("revoked", False), "created": k.get("created"),
                 "today": int(data.get("spent", {}).get(day, {}).get(n, 0))}
                for n, k in sorted(data["keys"].items())]


def _ollama_post(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    import requests
    r = requests.post(url, json=payload, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": {"message": r.text[:300]}}


def make_handler(keys: Keys, upstream: str, post=_ollama_post, timeout: float = 180.0):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Matthew/1"

        def log_message(self, *a):          # pas de journal d'accès (les contenus restent privés)
            pass

        def _send(self, code: int, body: dict, headers: dict | None = None):
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self):
            h = self.headers.get("Authorization", "")
            who = keys.identify(h[7:].strip()) if h.lower().startswith("bearer ") else None
            if who is None:
                self._send(401, {"error": {"message": "clé absente ou révoquée", "type": "auth"}})
            return who

        def do_GET(self):
            who = self._auth()
            if who is None:
                return
            name, k = who
            if self.path.rstrip("/") == "/v1/models":
                return self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in k["models"]]})
            if self.path.rstrip("/") == "/v1/usage":
                return self._send(200, {"key": name, "today": keys.spent_today(name), "budget": k["budget"], "rpm": k["rpm"]})
            self._send(404, {"error": {"message": "inconnu"}})

        def do_POST(self):
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._send(404, {"error": {"message": "inconnu"}})
            who = self._auth()
            if who is None:
                return
            name, k = who
            ok, why, wait = keys.admit(name, k)
            if not ok:
                return self._send(429, {"error": {"message": why, "type": "rate_limit"}}, {"Retry-After": max(1, wait)})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n > MAX_BODY:
                    return self._send(413, {"error": {"message": "requête trop grosse"}})
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": {"message": "JSON illisible"}})
            model = body.get("model") or k["models"][0]
            if model not in k["models"]:
                return self._send(403, {"error": {"message": f"modèle non permis pour cette clé : {model}"}})
            body.update(model=model, stream=False)
            body.setdefault("reasoning_effort", "none")       # Matthew est entraîné sans réflexion
            t0 = time.monotonic()
            try:
                code, data = post(f"{upstream.rstrip('/')}/chat/completions", body, timeout)
            except Exception as e:
                return self._send(502, {"error": {"message": f"Matthew injoignable : {type(e).__name__}"}})
            usage = data.get("usage") or {}
            used = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            if code < 400 and used:
                keys.charge(name, used)
                record_usage(model, f"gateway:{name}", usage, time.monotonic() - t0, keys.root)
            self._send(code, data, {"X-Matthew-Tokens-Today": keys.spent_today(name),
                                    "X-Matthew-Budget": k["budget"]})
    return Handler


def serve(host: str = "127.0.0.1", port: int = 11500, root: Path | None = None) -> None:
    cfg = config()
    keys = Keys(root)
    httpd = ThreadingHTTPServer((host, port), make_handler(keys, cfg["endpoint"]))
    print(f"Matthew écoute sur http://{host}:{port}/v1  (clés : {len(keys.table())})")
    httpd.serve_forever()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=11500)
    k = sub.add_parser("key")
    ks = k.add_subparsers(dest="action", required=True)
    c = ks.add_parser("create")
    c.add_argument("name")
    c.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    c.add_argument("--rpm", type=int, default=DEFAULT_RPM)
    ks.add_parser("list")
    r = ks.add_parser("revoke")
    r.add_argument("name")
    sub.add_parser("usage")
    a = ap.parse_args(argv)
    keys = Keys()
    if a.cmd == "serve":
        serve(a.host, a.port)
    elif a.cmd == "usage" or (a.cmd == "key" and a.action == "list"):
        print(json.dumps(keys.table(), ensure_ascii=False, indent=1))
    elif a.action == "create":
        key = keys.create(a.name, a.budget, a.rpm)
        print(f"Clé « {a.name} » (montrée une seule fois) :\n{key}")
    else:
        print("révoquée" if keys.revoke(a.name) else "clé inconnue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
