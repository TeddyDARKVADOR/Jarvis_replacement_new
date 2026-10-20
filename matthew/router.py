"""
matthew/router.py — choisir les quelques outils qu'on montre à Matthew.

Gemini reçoit les 43 déclarations à chaque tour ; un modèle de 4B avec un
contexte d'entraînement de 2 048 tokens, non. Le routeur classe les outils pour
une demande et n'en garde que k :
    lexical   BM25 sur le nom et la description de chaque outil (accents et
              casse repliés, mots vides retirés, racines à 5 lettres) ;
    appris    ce que le journal a vu : quels mots de l'utilisateur ont précédé
              quel outil qui a réussi. Plus Matthew sert, mieux il trie.
    sens      (si Ollama répond) plongements multilingues qwen3-embedding : la
              demande contre la description de chaque outil, et contre les
              demandes passées qui ont mené à cet outil (exemples amorcés et
              journal). Indispensable : les descriptions sont en anglais, les
              demandes en français — mesuré le 2026-10-20, le lexical seul ne
              trouvait ni « rappelle-moi… » (reminder) ni « quel temps… »
              (weather_report).
Le même routeur sert deux fois : à l'entraînement (les leurres sont les outils
que le routeur confond avec le bon, des « négatifs difficiles »), et à l'usage
(offline/llm.py ne passe à Matthew que la présélection). Ce qu'il voit en
s'entraînant est donc ce qu'il verra en servant.

    recall(journal, k)   part des épisodes dont TOUS les outils sont dans le top k,
                         chaque épisode étant retiré de l'apprentissage pendant
                         qu'on le note (pas de triche).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

K = 8
_STOP = set("""le la les un une des de du d l a au aux et ou en dans sur pour par avec sans ce cet cette
ces mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs je tu il elle on nous vous ils elles
me te se moi toi lui y est sont suis es etre avoir ai as qui que quoi quel quelle quels quelles ne pas plus
peux peut veux veut fais fait faire stp svp merci jarvis matthew the a an of to in on for and or is are be
this that it with use when user tool if not do does from as by at""".split())


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _fold(text).replace("_", " "))
    return [w[:5] for w in words if len(w) > 1 and w not in _STOP]


class Router:
    def __init__(self, declarations: list[dict], episodes: list[dict] | None = None,
                 embed=None, exemplars: list[tuple[str, str]] | None = None):
        """`embed(textes, requête: bool) -> vecteurs` (None = lexical seul) ;
        `exemplars` = [(demande, outil)] déjà vus (amorçage, journal)."""
        self.decls = {d["name"]: d for d in declarations if d.get("name")}
        self.embed = embed
        self.tool_vecs: dict[str, list[float]] = {}
        self.ex: list[tuple[str, str, list[float]]] = []
        if embed is not None:
            try:
                names = list(self.decls)
                vecs = embed([f"{n}: {self.decls[n].get('description', '')}"[:1200] for n in names], False)
                self.tool_vecs = dict(zip(names, vecs))
                exs = [(t, n) for t, n in (exemplars or []) if n in self.decls]
                if exs:
                    self.ex = [(t, n, v) for (t, n), v in zip(exs, embed([t for t, _ in exs], True))]
            except Exception:
                self.embed, self.tool_vecs, self.ex = None, {}, []
        self.docs = {n: tokens(f"{n} {n} {d.get('description', '')}") for n, d in self.decls.items()}
        self.avgdl = (sum(len(t) for t in self.docs.values()) / len(self.docs)) if self.docs else 1.0
        self.df = Counter(w for t in self.docs.values() for w in set(t))
        self.tf = {n: Counter(t) for n, t in self.docs.items()}
        self.learned: dict[str, Counter] = {}
        self.word_count: Counter = Counter()
        for ep in episodes or []:
            self.learn(ep, +1)

    def learn(self, ep: dict, sign: int = 1) -> None:
        words = set(tokens(ep.get("user", "")))
        for t in ep.get("tools") or []:
            if not t.get("ok", True) or t["name"] not in self.decls:
                continue
            c = self.learned.setdefault(t["name"], Counter())
            for w in words:
                c[w] += sign
            self.word_count.update({w: sign for w in words})

    def _bm25(self, q: list[str], name: str, k1=1.2, b=0.75) -> float:
        tf, dl, n = self.tf[name], len(self.docs[name]), len(self.docs)
        s = 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * dl / self.avgdl))
        return s

    def _learned(self, q: list[str], name: str) -> float:
        c = self.learned.get(name)
        if not c:
            return 0.0
        return sum(math.log1p(c[w]) / math.log(2 + self.word_count[w]) for w in q if c[w] > 0)

    def rank(self, text: str, exclude_exemplar: str | None = None) -> list[tuple[str, float]]:
        q = tokens(text)
        lex = {n: self._bm25(q, n) + 2.0 * self._learned(q, n) for n in self.decls}
        if self.embed is None or not self.tool_vecs:
            return sorted(lex.items(), key=lambda x: (-x[1], x[0]))
        try:
            qv = self.embed([text], True)[0]
        except Exception:
            return sorted(lex.items(), key=lambda x: (-x[1], x[0]))
        best_ex: dict[str, float] = {}
        for t, n, v in self.ex:
            if t != exclude_exemplar:
                c = _cos(qv, v)
                if c > best_ex.get(n, -1.0):
                    best_ex[n] = c
        top = max(lex.values()) or 1.0
        scored = [(n, W_DESC * _cos(qv, self.tool_vecs.get(n, [])) + W_EX * best_ex.get(n, 0.0)
                   + W_LEX * lex[n] / top) for n in self.decls]
        return sorted(scored, key=lambda x: (-x[1], x[0]))

    def pick(self, text: str, k: int = K, exclude_exemplar: str | None = None) -> list[str]:
        return [n for n, _ in self.rank(text, exclude_exemplar)[:k]]


#: Poids du score combiné. Mesuré le 2026-10-20 sur 116 exemples amorcés (23 outils),
#: chaque exemple retiré de ses propres exemplaires — rappel@1 / @3 / @8 :
#:   lexical seul 0,24 / 0,35 / 0,45 ; sens (descriptions) 0,58 / 0,84 / 0,96 ;
#:   sens + exemples + lexical (1 / 0,5 / 0,15) 0,71 / 0,91 / 1,00  ← retenu.
W_DESC, W_EX, W_LEX = 1.0, 0.5, 0.15
QUERY_INSTRUCT = ("Instruct: Given a spoken request to a personal voice assistant, "
                  "retrieve the tool that fulfills it\nQuery: ")


def _cos(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embedder(cache: dict | None = None):
    """Plongements Ollama avec cache mémoire ; None si Ollama ou le modèle manque.
    Les requêtes reçoivent l'instruction de recherche (usage prévu de qwen3-embedding)."""
    from matthew.augment import ollama_embedder
    raw = ollama_embedder()
    if raw is None:
        return None
    cache = {} if cache is None else cache

    def embed(texts: list[str], query: bool = False) -> list[list[float]]:
        keyed = [(QUERY_INSTRUCT + t) if query else t for t in texts]
        missing = [t for t in dict.fromkeys(keyed) if t not in cache]
        if missing:
            for t, v in zip(missing, raw(missing)):
                cache[t] = v
        return [cache[t] for t in keyed]
    return embed


def exemplars(root=None) -> list[tuple[str, str]]:
    """(demande, outil) déjà vérifiés : amorçage (ok) et journal (outil réussi)."""
    from matthew.journal import Journal
    j = Journal(root)
    out = []
    try:
        from matthew.dataset import synthetic_records
        out += [(r["messages"][1]["content"], r["target"]) for r in synthetic_records(j.root)
                if r.get("target") and not r["target"].startswith("__")]
    except Exception:
        pass
    for r in j.records():
        if r.get("kind") == "episode" and r.get("user") and r.get("tools")                 and all(t.get("ok", True) for t in r["tools"]):
            out += [(r["user"], t["name"]) for t in r["tools"]]
    return out


def default(decls: list[dict], episodes: list[dict] | None = None) -> "Router":
    """Le routeur complet : sens (si Ollama répond), exemples vérifiés, journal."""
    try:
        exs = exemplars()
    except Exception:
        exs = []
    return Router(decls, episodes, embed=embedder(), exemplars=exs)


_CACHED: dict = {}


def pick(text: str, declarations: dict | list, k: int = K) -> list[str]:
    """Pour offline/llm.py : les k outils à montrer. Le routeur (plongements des outils
    et des exemples) est construit une fois par jeu d'outils, puis réutilisé."""
    decls = list(declarations.values()) if isinstance(declarations, dict) else declarations
    key = tuple(sorted(d.get("name", "") for d in decls))
    if _CACHED.get("key") != key:
        try:
            from matthew.journal import Journal
            eps = [r for r in Journal().records() if r.get("kind") == "episode" and r.get("tools")]
        except Exception:
            eps = []
        _CACHED.update(key=key, router=default(decls, eps))
    return _CACHED["router"].pick(text, k)


def recall(declarations: list[dict], episodes: list[dict], k: int = K) -> dict:
    eps = [e for e in episodes if e.get("user") and e.get("tools")
           and all(t.get("ok", True) for t in e["tools"])]
    r = Router(declarations, eps)
    hit = lex_hit = 0
    lexical = Router(declarations)
    for e in eps:
        r.learn(e, -1)                       # l'épisode ne s'aide pas lui-même
        used = {t["name"] for t in e["tools"]}
        hit += used <= set(r.pick(e["user"], k))
        lex_hit += used <= set(lexical.pick(e["user"], k))
        r.learn(e, +1)
    n = len(eps)
    return {"episodes": n, "k": k, "recall": round(hit / n, 3) if n else None,
            "recall_lexical_only": round(lex_hit / n, 3) if n else None}
