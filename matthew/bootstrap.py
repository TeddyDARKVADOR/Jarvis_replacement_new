"""
matthew/bootstrap.py — l'amorçage : des données d'entraînement sans attendre des semaines.

Méthode APIGen / AgenticQwen (rejet par vérification indépendante), sans rien
exécuter et sans Gemini :
  1. inventer   pour chaque outil, le professeur ouvert écrit des demandes
                variées en français parlé (tutoiement, vouvoiement, valeurs
                concrètes, formulations indirectes) ;
  2. vérifier   une AUTRE passe du professeur, qui ne sait pas quel outil était
                visé, reçoit la demande avec le bon outil noyé parmi les leurres
                du routeur ; gardé seulement s'il choisit cet outil, avec des
                arguments conformes au schéma (obligatoires, types, enum).
  3. sans outil quelques demandes de conversation, où la bonne réponse est de
                répondre sans outil (sinon Matthew en appellerait partout).
Un exemple s'arrête à l'appel d'outil (aucun résultat inventé) : Matthew y
apprend à CHOISIR et à REMPLIR les arguments ; les réponses finales viennent
du journal réel.

Reprise possible : un outil déjà amorcé est sauté. Quota (429) : on attend ce
que le serveur demande (au plus 3 fois de suite), puis on s'arrête proprement.

    python -m matthew.bootstrap [--per-tool 6] [--chitchat 30] [--max-calls 400]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import config
from matthew.client import Client, ClientError, QuotaError, to_openai_tools
from matthew.journal import Journal
from matthew import data_dir
from matthew.router import Router
from matthew.router import default as default_router
from matthew.teacher import SYSTEM, check_teacher, tool_subset

GEN_PROMPT = """Voici un outil d'un assistant vocal personnel (sur PC Windows, en français) :

nom : {name}
description : {description}
paramètres (JSON Schema) : {params}

Écris {n} demandes DIFFÉRENTES qu'un utilisateur dirait à voix haute et qui exigent cet outil.
Varie : longueur, tutoiement / vouvoiement, formulation directe ou indirecte, valeurs concrètes
(noms, nombres, lieux, applications réalistes). Langue parlée naturelle, pas de liste technique.
Réponds uniquement en JSON : {{"requests": ["...", "..."]}}"""

CHAT_PROMPT = """Écris {n} phrases DIFFÉRENTES qu'un utilisateur dirait à son assistant vocal et
auxquelles il faut répondre directement, SANS action ni recherche : salutations, remerciements,
petites discussions, questions sur l'assistant lui-même, demandes de reformulation, avis simples.
Varie le ton. Réponds uniquement en JSON : {{"requests": ["...", "..."]}}"""

HARD_HINT = """

Cette fois, des demandes DIFFICILES : formulations indirectes ou implicites (l'utilisateur
décrit son besoin sans nommer l'action), phrases longues avec du contexte inutile, fautes
de transcription vocale plausibles, et demandes proches d'autres outils."""

NONE_PROMPT = """Voici les outils d'un assistant vocal sur PC (nom : rôle) :
{tools}

Écris {n} demandes DIFFÉRENTES et réalistes qu'un utilisateur pourrait lui faire, mais qu'AUCUN
de ces outils ne permet de réaliser (commander un repas, appeler un vrai taxi, conduire la voiture,
réserver une table, deviner le loto, contrôler un appareil absent…). Varie le ton et la longueur.
Réponds uniquement en JSON : {{"requests": ["...", "..."]}}"""

#: Quotas à atteindre pour la conversation et les refus, avant les outils (ils manquaient :
#: « salut ça va » appelait briefing ; refus justes 50 % au premier champion).
NO_TOOL_TARGETS = {"__chat__": 40, "__none__": 40}

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}


def valid_args(args: dict, schema: dict) -> tuple[bool, str]:
    """Obligatoires présents, types et enum respectés (schéma JSON en minuscules)."""
    if not isinstance(args, dict) or "_invalid_json" in args:
        return False, "arguments illisibles"
    props = schema.get("properties") or {}
    for r in schema.get("required") or []:
        if r not in args or args[r] in ("", None):
            return False, f"« {r} » manquant"
    for k, v in args.items():
        spec = props.get(k)
        if spec is None:
            return False, f"argument inconnu « {k} »"
        t = _TYPES.get(str(spec.get("type", "")).lower())
        if t and not isinstance(v, t) or (t is int and isinstance(v, bool)):
            return False, f"« {k} » n'est pas {spec.get('type')}"
        if spec.get("enum") and v not in spec["enum"]:
            return False, f"« {k} »={v!r} hors enum"
    return True, ""


def _requests_of(text: str) -> list[str]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        reqs = json.loads(m.group(0))["requests"] if m else []
    except (ValueError, KeyError, TypeError):
        return []
    return [r.strip() for r in reqs if isinstance(r, str) and 3 < len(r.strip()) < 300]


#: Double annotation (accord) : le générateur écrit aussi l'appel attendu ; le vérificateur, qui
#: ne le voit pas, doit retrouver les MÊMES valeurs. Les arguments étaient le point faible du
#: deuxième champion (0,795) : le vérificateur seul ne contrôlait que le nom et le schéma.
AGREE_HINT = """

Pour CHAQUE demande, donne aussi les arguments exacts de l'appel attendu (mêmes noms que le
schéma, valeurs telles qu'on les déduit de la phrase). Réponds uniquement en JSON :
{{"items": [{{"request": "...", "args": {{...}}}}]}}"""


def _items_of(text: str) -> list[tuple[str, dict]]:
    """L'objet {"items": …}, même précédé d'un autre objet JSON (le modèle écrivait parfois
    d'abord {"requests": …} puis {"items": …} : 37 générations illisibles sur 144 au banc)."""
    text, dec, items = text or "", json.JSONDecoder(), []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            items = obj["items"]
            break
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("request"), str) and isinstance(it.get("args"), dict):
            r = it["request"].strip()
            if 3 < len(r) < 300:
                out.append((r, it["args"]))
    return out


def args_agree(expected: dict, got: dict) -> tuple[bool, str]:
    """Mêmes clés renseignées, mêmes valeurs (forme canonique de norm.same)."""
    from matthew.norm import same
    want = {k: v for k, v in expected.items() if v not in ("", None, [], {})}
    have = {k: v for k, v in got.items() if v not in ("", None, [], {})}
    if set(want) != set(have):
        return False, f"clés {sorted(want)} ≠ {sorted(have)}"
    for k in want:
        if not (same(want[k], have[k]) or _same_text(want[k], have[k])):
            return False, f"« {k} » : {str(want[k])[:30]!r} ≠ {str(have[k])[:30]!r}"
    return True, ""


def _same_text(a, b) -> bool:
    """Texte libre (une tâche, un message de plus de 5 mots) : la même chose dite autrement
    passe — mots communs (racines) ≥ 60 %. Noms, nombres, dates restent exacts (norm.same)."""
    if not (isinstance(a, str) and isinstance(b, str)):
        return False
    from matthew.router import tokens
    x, y = set(tokens(a)), set(tokens(b))
    if len(a.split()) <= 5 or len(b.split()) <= 5 or not x or not y:
        return False
    return len(x & y) / len(x | y) >= 0.6


class Bootstrap:
    def __init__(self, journal: Journal | None = None, client: Client | None = None,
                 cfg: dict | None = None, sleep=time.sleep, max_calls: int = 400, verifier=None):
        self.cfg = cfg or config()
        self.journal = journal or Journal()
        t = self.cfg["teacher"]
        check_teacher(t)
        self.teacher = t
        self.client = client or Client(t["base_url"], t["model"], t.get("api_key"),
                                       purpose="teacher", usage_root=self.journal.root)
        # Vérificateur : un AUTRE modèle ouvert (indépendance du contrôle, et un quota
        # séparé chez Groq : le débit double). Sans réglage, le professeur lui-même.
        self.verifier_model = t.get("verifier_model") or t["model"]
        # Ses appels deviennent les cibles : sa licence compte autant que celle du professeur.
        check_teacher({"model": self.verifier_model, "license": t.get("verifier_license", t["license"])})
        self.verifier = verifier or (self.client if self.verifier_model == t["model"] or client else
                                     Client(t["base_url"], self.verifier_model, t.get("api_key"),
                                            purpose="teacher", usage_root=self.journal.root))
        self._sleep = sleep
        self.max_calls, self.calls = max_calls, 0
        # Professeur de secours, local (Ollama) : prend le relais quand le quota gratuit
        # est épuisé, au lieu d'arrêter la nuit. Licence ouverte exigée, comme le premier.
        self._fallback = self.cfg.get("teacher_fallback") if client is None else None
        self.fell_back = False

    @property
    def path(self) -> Path:
        d = self.journal.root / "synthetic"
        d.mkdir(parents=True, exist_ok=True)
        return d / "records.jsonl"

    def records(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                yield json.loads(line)
            except ValueError:
                pass

    def _verify(self, *a, **kw) -> dict:
        return self._chat(*a, _client=self.verifier, **kw)

    def _chat(self, *a, _client=None, **kw) -> dict:
        strikes = 0
        while True:
            if self.calls >= self.max_calls:
                raise StopIteration("budget d'appels atteint")
            self.calls += 1
            try:
                return (_client or self.client).chat(*a, **kw)
            except QuotaError as e:
                strikes += 1
                if strikes > 3:
                    if self._switch_to_fallback():
                        strikes = 0
                        _client = self.verifier if _client is not None else None
                        continue
                    raise StopIteration(f"quota du professeur épuisé : {e}")
                self._sleep(min(120.0, (e.retry_after or 20.0) + 1))

    def _switch_to_fallback(self) -> bool:
        fb = self._fallback
        if not fb or self.fell_back:
            return False
        check_teacher(fb)
        self.fell_back = True
        self.teacher = fb
        self.client = Client(fb["base_url"], fb["model"], fb.get("api_key"), purpose="teacher",
                             usage_root=self.journal.root, timeout=600, extra=fb.get("extra", {}))
        self.verifier_model = fb.get("verifier_model") or fb["model"]
        self.verifier = Client(fb["base_url"], self.verifier_model, fb.get("api_key"), purpose="teacher",
                               usage_root=self.journal.root, timeout=300, extra=fb.get("extra", {}))
        print(f"[Matthew] quota épuisé : le professeur local {fb['model']} prend le relais")
        return True

    def _write(self, rec: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def run(self, per_tool: int = 6, chitchat: int = 30) -> dict:
        decls = self.journal.tools()
        if not decls:
            return {"ok": 0, "rejected": 0, "reason": "aucune déclaration d'outils (tools.json) : lancer JARVIS une fois"}
        # Fait = au moins un exemple vérifié, ou 3 échecs (un aléa ne condamne pas un outil).
        ok_t, fails, oks = set(), {}, {}
        for r in self.records():
            t = r.get("target")
            if r.get("status") == "ok":
                ok_t.add(t)
                oks[t] = oks.get(t, 0) + 1
            else:
                fails[t] = fails.get(t, 0) + 1
        done = ok_t | {t for t, n in fails.items() if n >= 3}
        self.calls = 0
        # Leurres = ce que le routeur réel confond avec la cible (négatifs difficiles).
        router = default_router(decls) if self.journal.root == data_dir() else Router(decls)
        stats = {"ok": 0, "rejected": 0, "tools_done": 0, "reasons": {}}
        try:
            # 1. Conversation et refus D'ABORD : ce sont eux qui manquaient au premier champion.
            if chitchat:
                for kind, goal in NO_TOOL_TARGETS.items():
                    rounds = 0
                    while oks.get(kind, 0) < goal and rounds < 3:
                        rounds += 1
                        got = self._no_tool(kind, decls, router, min(20, chitchat), stats)
                        oks[kind] = oks.get(kind, 0) + got
            # 2. Les outils pas encore amorcés.
            for d in decls:
                if d["name"] in done:
                    continue
                self._tool(d, decls, router, per_tool, stats)
                stats["tools_done"] += 1
            # 3. La remédiation : outils ratés, et refus si le dernier candidat en manque.
            self._remediate(decls, router, stats)
        except StopIteration as e:
            stats["stopped"] = str(e)
        stats["calls"] = self.calls
        return stats

    def _reject(self, stats, target, req, why):
        stats["rejected"] += 1
        key = why.split(" «")[0][:40]
        stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
        self._write({"target": target, "status": "rejected", "user": req, "reason": why,
                     "teacher": self.teacher["model"], "ts": time.time()})

    def _tool(self, d, decls, router, n, stats, extra: str = "", round_: str | None = None,
              agree: bool = False):
        schema = to_openai_tools([d])[0]["function"]["parameters"]
        has_args = bool(schema.get("properties"))
        agree = agree and has_args
        prompt = GEN_PROMPT.format(name=d["name"], description=str(d.get("description", ""))[:900],
                                   params=json.dumps(schema, ensure_ascii=False)[:1500], n=n)
        if agree:                      # un seul format annoncé : sinon deux objets JSON à la suite
            prompt = prompt.rsplit("Réponds uniquement en JSON", 1)[0].rstrip()
        gen = self._chat([{"role": "user", "content": prompt + extra + (AGREE_HINT if agree else "")
            + f"\n\nNous sommes le {time.strftime('%Y-%m-%d')} : toute date future est postérieure."}],
            max_tokens=3000 if agree else 1500, temperature=0.9, json_mode=True)
        items = _items_of(gen["content"]) if agree else [(r, None) for r in _requests_of(gen["content"])]
        if not items:
            self._reject(stats, d["name"], "", "génération illisible")
            return
        for req, expect in items[:n]:
            sid = f"syn-{uuid.uuid4().hex[:10]}"
            subset = tool_subset(decls, [d["name"]], sid, router=router, query=req)
            tools = to_openai_tools(subset)
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": req}]
            try:
                got = self._verify(msgs, tools, temperature=0.2)
            except ClientError as e:
                self._reject(stats, d["name"], req, f"erreur {e}")
                continue
            calls = got.get("tool_calls") or []
            if len(calls) != 1 or (calls[0].get("function") or {}).get("name") != d["name"]:
                names = [(c.get("function") or {}).get("name") for c in calls]
                self._reject(stats, d["name"], req, f"vérification : {names or 'aucun outil'}")
                continue
            args = calls[0]["function"].get("arguments") or {}
            ok, why = valid_args(args, schema)
            if not ok:
                self._reject(stats, d["name"], req, f"arguments : {why}")
                continue
            if expect is not None:
                ok, why = args_agree(expect, args)
                if not ok:
                    self._reject(stats, d["name"], req, f"désaccord : {why}")
                    continue
            self._write({"episode": sid, "target": d["name"], "status": "ok", "synthetic": True,
                         "teacher": self.teacher["model"], "license": self.teacher["license"],
                         "verifier": self.verifier_model, **({"round": round_} if round_ else {}),
                         "verified": "agreement" if expect is not None else "independent",
                         "ts": time.time(), "tools": [x["name"] for x in subset],
                         "messages": msgs + [{"role": "assistant", "content": got.get("content", ""),
                                              "tool_calls": [{"id": "call_0", "type": "function",
                                                              "function": {"name": d["name"], "arguments": args}}]}]})
            stats["ok"] += 1

    def _remediate(self, decls, router, stats, per_tool: int = 4):
        """Remédiation (iTool, AgenticQwen) : pour chaque outil où le dernier candidat
        a échoué, des demandes PLUS DIFFICILES — formulations indirectes, proches des
        outils avec lesquels il l'a confondu. Une fois par évaluation."""
        try:
            ev = json.loads((self.journal.root / "last_eval.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        done = {r.get("round") for r in self.records() if r.get("round")}
        if ev.get("at") in done:
            return
        irr = ev.get("irrelevance")
        if irr is not None and irr < 0.75:
            stats.setdefault("remediated", []).append("__none__")
            self._no_tool("__none__", decls, router, 16, stats, round_=ev["at"])
        if not ev.get("weak") and "remediated" not in stats:
            return
        by = {d["name"]: d for d in decls}
        for name in ev.get("weak") or []:
            if name not in by:
                continue
            mistakes = [f["got"] for f in ev.get("failures", []) if f.get("target") == name]
            confused = sorted({g for m in mistakes if isinstance(m, list) for g in m if g != name})
            hint = (f"\nLe modèle élève l'a confondu avec : {', '.join(confused)}. Écris des demandes qui "
                    "distinguent clairement cet outil de ceux-là." if confused else "")
            stats.setdefault("remediated", []).append(name)
            self._tool(by[name], decls, router, per_tool, stats, extra=HARD_HINT + hint, round_=ev["at"])
        if "remediated" not in stats:
            return
        # Marque la ronde faite même si tous les exemples ont été rejetés.
        self._write({"target": "__round__", "status": "rejected", "round": ev["at"], "reason": "ronde",
                     "teacher": self.teacher["model"], "ts": time.time()})

    def _no_tool(self, kind, decls, router, n, stats, round_: str | None = None) -> int:
        """Demandes où la bonne réponse est de NE PAS appeler d'outil. __chat__ : conversation ;
        __none__ : action impossible avec les outils présents (le vérificateur doit refuser
        poliment). Le vérificateur reçoit les outils que le routeur juge les plus proches :
        s'il en appelle un, l'exemple est rejeté."""
        if kind == "__none__":
            catalog = "\n".join(f"- {d['name']} : {str(d.get('description', ''))[:110]}" for d in decls)
            prompt = NONE_PROMPT.format(tools=catalog, n=n)
        else:
            prompt = CHAT_PROMPT.format(n=n)
        gen = self._chat([{"role": "user", "content": prompt}], max_tokens=2000, temperature=0.9, json_mode=True)
        kept = 0
        for req in _requests_of(gen["content"])[:n]:
            sid = f"syn-{uuid.uuid4().hex[:10]}"
            subset = tool_subset(decls, [], sid, router=router, query=req)
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": req}]
            try:
                got = self._verify(msgs, to_openai_tools(subset), temperature=0.4, max_tokens=300)
            except ClientError as e:
                self._reject(stats, kind, req, f"erreur {e}")
                continue
            if got.get("tool_calls") or not got.get("content"):
                self._reject(stats, kind, req, "outil appelé alors qu'aucun ne convient")
                continue
            self._write({"episode": sid, "target": kind, "status": "ok", "synthetic": True,
                         "teacher": self.teacher["model"], "license": self.teacher["license"],
                         "verifier": self.verifier_model, **({"round": round_} if round_ else {}),
                         "verified": "independent", "ts": time.time(), "tools": [x["name"] for x in subset],
                         "messages": msgs + [{"role": "assistant", "content": got["content"]}]})
            stats["ok"] += 1
            kept += 1
        return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.bootstrap")
    ap.add_argument("--per-tool", type=int, default=6)
    ap.add_argument("--chitchat", type=int, default=30)
    ap.add_argument("--max-calls", type=int, default=400)
    a = ap.parse_args(argv)
    out = Bootstrap(max_calls=a.max_calls).run(a.per_tool, a.chitchat)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
