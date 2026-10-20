"""
matthew/teacher.py — l'école : un modèle OUVERT réécrit ce que Gemini a fait.

Pourquoi : les réponses de Gemini ne peuvent pas être apprises (conditions du
2026-03-23, voir matthew/__init__.py). On garde la DEMANDE de l'utilisateur et
les RÉSULTATS RÉELS des outils, et on demande à un professeur sous licence
ouverte (gpt-oss, Qwen : Apache-2.0) de produire lui-même la réponse.

Échantillonnage par rejet, vérifié par l'exécution réelle (AgenticQwen 2026,
APIGen) : la proposition du professeur n'est gardée que si elle concorde avec
ce qui a réellement marché.
    match_mode "trace"   mêmes outils, et au moins la moitié des valeurs
                         d'arguments identiques à l'appel qui a réussi ;
    match_mode "schema"  mêmes outils, arguments obligatoires présents.
Concordance → on lui rend les vrais résultats, il écrit la réponse finale.
Sinon → rejeté, avec la raison (le taux de désaccord se suit dans le statut).

Chaque exemple montre les outils utilisés plus quelques leurres : apprendre à
CHOISIR, dans un prompt assez court pour s'entraîner sur 12 Go.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from matthew import OPEN_LICENSES, config, data_dir
from matthew.client import Client, ClientError, to_openai_tools
from matthew.journal import Journal
from matthew.norm import same

SYSTEM = ("Tu es Matthew, l'assistant personnel de l'utilisateur. Tu réponds en "
          "français, en une ou deux phrases faites pour être dites à voix haute. "
          "Quand la demande exige une action ou une information que tu n'as pas, "
          "tu appelles l'outil qui convient ; sinon tu réponds directement.")
DECOYS = 5


class LicenseError(ValueError):
    pass


def check_teacher(t: dict) -> None:
    lic = str(t.get("license", "")).lower()
    model = str(t.get("model", "")).lower()
    if "gemini" in model:
        raise LicenseError(f"professeur refusé : {t.get('model')} (conditions Gemini)")
    if lic not in OPEN_LICENSES:
        raise LicenseError(f"professeur sans licence ouverte déclarée ({lic or 'aucune'}) : "
                           f"ses sorties ne peuvent pas devenir des cibles")


def tool_subset(declarations: list[dict], used: list[str], seed: str, decoys: int = DECOYS,
                router=None, query: str = "") -> list[dict]:
    """Les outils utilisés + des leurres : ceux que le routeur classe le plus haut
    pour cette demande (négatifs difficiles), sinon un tirage stable."""
    by_name = {d["name"]: d for d in declarations if d.get("name")}
    chosen = [n for n in dict.fromkeys(used) if n in by_name]
    if router is not None and query:
        others = [n for n in router.pick(query, len(by_name)) if n not in chosen]
    else:
        others = sorted((n for n in by_name if n not in chosen),
                        key=lambda n: hashlib.sha1(f"{seed}:{n}".encode()).hexdigest())
    picked = chosen + others[:decoys]
    # Ordre stable mais sans indice : l'outil juste n'est pas toujours en tête.
    picked.sort(key=lambda n: hashlib.sha1(f"{seed}:{n}".encode()).hexdigest())
    return [by_name[n] for n in picked]


def _norm(v) -> str:
    return " ".join(str(v).casefold().split())


def calls_match(proposed: list[dict], trace: list[dict], declarations: dict, mode: str) -> tuple[bool, str]:
    names_p = [(c.get("function") or {}).get("name") for c in proposed]
    names_t = [t["name"] for t in trace]
    if sorted(names_p) != sorted(names_t):
        return False, f"outils {names_p} au lieu de {names_t}"
    for call in proposed:
        fn = call.get("function") or {}
        args = fn.get("arguments") or {}
        if "_invalid_json" in args:
            return False, "arguments JSON invalides"
        required = ((declarations.get(fn["name"]) or {}).get("parameters") or {}).get("required") or []
        missing = [r for r in required if r not in args]
        if missing:
            return False, f"{fn['name']} : arguments obligatoires absents {missing}"
        if mode == "trace":
            ref = next((t["args"] for t in trace if t["name"] == fn["name"]), {})
            if ref:
                hits = sum(1 for k, v in ref.items() if k in args and same(args[k], v))
                if hits / len(ref) < 0.5:
                    return False, f"{fn['name']} : arguments trop différents de l'appel réussi"
    return True, ""


class School:
    def __init__(self, journal: Journal | None = None, client: Client | None = None,
                 cfg: dict | None = None, clock=time.time):
        self.cfg = cfg or config()
        self.journal = journal or Journal()
        t = self.cfg["teacher"]
        check_teacher(t)
        self.teacher = t
        self.client = client or Client(t["base_url"], t["model"], t.get("api_key"),
                                       purpose="teacher", usage_root=self.journal.root)
        self._clock = clock

    @property
    def path(self) -> Path:
        d = self.journal.root / "teacher"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{datetime.fromtimestamp(self._clock()).strftime('%Y-%m')}.jsonl"

    def done(self) -> set[str]:
        d = self.journal.root / "teacher"
        seen = set()
        for f in d.glob("*.jsonl") if d.exists() else []:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(json.loads(line)["episode"])
                except (ValueError, KeyError):
                    pass
        return seen

    def records(self):
        d = self.journal.root / "teacher"
        for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    yield json.loads(line)
                except ValueError:
                    pass

    def pending(self) -> list[dict]:
        done = self.done()
        excluded = {r["of"] for r in self.journal.records() if r.get("kind") in ("correction", "forget")}
        return [r for r in self.journal.records()
                if r.get("kind") == "episode" and r.get("user") and r["id"] not in done
                and r["id"] not in excluded and all(t.get("ok", True) for t in r.get("tools", []))]

    def relabel(self, budget: int | None = None) -> dict:
        budget = self.cfg["teacher_budget"] if budget is None else budget
        decls = self.journal.tools()
        by_name = {d["name"]: d for d in decls}
        from matthew.router import Router, default
        eps = [r for r in self.journal.records() if r.get("kind") == "episode" and r.get("tools")]
        self._router = default(decls, eps) if self.journal.root == data_dir() else Router(decls, eps)
        stats = {"ok": 0, "rejected": 0, "errors": 0}
        for ep in self.pending()[:budget]:
            try:
                rec = self.one(ep, decls, by_name)
            except ClientError as e:
                stats["errors"] += 1
                if stats["errors"] >= 3:
                    stats["stopped"] = str(e)
                    break
                continue
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["ok" if rec["status"] == "ok" else "rejected"] += 1
        return stats

    def one(self, ep: dict, decls: list[dict], by_name: dict) -> dict:
        trace = ep.get("tools") or []
        subset = tool_subset(decls, [t["name"] for t in trace], ep["id"],
                             router=getattr(self, "_router", None), query=ep["user"])
        tools = to_openai_tools(subset)
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": ep["user"]}]
        base = {"episode": ep["id"], "ts": ep["ts"], "teacher": self.teacher["model"],
                "license": self.teacher["license"], "tools": [d["name"] for d in subset]}
        first = self.client.chat(msgs, tools or None)
        calls = first.get("tool_calls") or []
        if not trace:
            if calls:
                return {**base, "status": "rejected", "reason": "outil appelé là où aucun n'a servi"}
            if not first["content"]:
                return {**base, "status": "rejected", "reason": "réponse vide"}
            return {**base, "status": "ok", "verified": "answer",
                    "messages": msgs + [{"role": "assistant", "content": first["content"]}]}
        if not calls:
            return {**base, "status": "rejected", "reason": "aucun outil appelé"}
        ok, why = calls_match(calls, trace, by_name, self.cfg.get("match_mode", "trace"))
        if not ok:
            return {**base, "status": "rejected", "reason": why}
        clean_calls, tool_msgs, used = [], [], set()
        for i, c in enumerate(calls):
            fn = c["function"]
            cid = f"call_{i}"
            clean_calls.append({"id": cid, "type": "function",
                                "function": {"name": fn["name"], "arguments": fn["arguments"]}})
            # Le vrai résultat, celui de l'exécution réussie (même outil, pris dans l'ordre).
            j, res = next((j, t) for j, t in enumerate(trace)
                          if t["name"] == fn["name"] and j not in used)
            used.add(j)
            tool_msgs.append({"role": "tool", "tool_call_id": cid, "name": fn["name"],
                              "content": res["result"]})
        msgs += [{"role": "assistant", "content": first.get("content", ""), "tool_calls": clean_calls}]
        msgs += tool_msgs
        final = self.client.chat(msgs, tools)
        if final.get("tool_calls") or not final["content"]:
            return {**base, "status": "rejected", "reason": "pas de réponse finale après les outils"}
        return {**base, "status": "ok", "verified": self.cfg.get("match_mode", "trace"),
                "messages": msgs + [{"role": "assistant", "content": final["content"]}]}

    def stats(self) -> dict:
        out = {"ok": 0, "rejected": 0, "reasons": {}}
        for r in self.records():
            out["ok" if r.get("status") == "ok" else "rejected"] += 1
            if r.get("status") != "ok":
                key = str(r.get("reason", "?")).split(" :")[0][:40]
                out["reasons"][key] = out["reasons"].get(key, 0) + 1
        return out
