"""
capabilities/ — le graphe de ce que Matthew sait faire, lu par Matthew lui-même.

Paquet racine au sens du contrat V2 (graphe `contrat`) : il n'importe rien de
main.py, du cœur, du serveur ni des clients. Le supprimer, avec
plugins/capabilities.py, rend Matthew identique à avant : l'outil disparaît, le
HUD n'a plus rien à afficher.

    python -m capabilities.selftest      le graphe correspond-il au code ?

LES GRAPHES REMPLACENT LES README
    Chaque fichier de `graphs/` décrit une partie de Matthew : `general.json`
    pour l'ensemble, puis un sous-graphe par dossier (serveur, HUD, Android…).
    Un nœud est une fonctionnalité, un outil, un module, une règle, une
    procédure ou une limite connue ; une arête dit comment deux nœuds se
    tiennent. Chaque graphe porte la date de sa dernière mise à jour.

    Le selftest fait du graphe une description vérifiée, pas un texte de plus :
    tout outil déclaré dans le code doit y être, tout outil du graphe doit
    exister, tout fichier cité doit exister, et un README remplacé ne peut pas
    revenir sans que le test le signale.

FORMAT (matthew_assistant.graph/1)
    {"schema", "id", "title", "updated": "AAAA-MM-JJ", "summary",
     "replaces": [chemins des .md que ce graphe remplace],
     "nodes": [{"id", "label", "kind", "summary", "details": [...],
                "files": [...], "tool", "status", "graph", "test"}],
     "edges": [[de, vers, relation], ...]}
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA = "matthew_assistant.graph/1"
ROOT = Path(__file__).resolve().parent
GRAPHS_DIR = ROOT / "graphs"
REPO = ROOT.parent
GENERAL = "general"

#: Ce qu'un nœud peut être. Le HUD en tire sa légende et ses couleurs.
KINDS = {
    "racine": "Matthew ou la racine d'un sous-graphe",
    "domaine": "un grand ensemble de fonctionnalités",
    "fonction": "une fonctionnalité",
    "outil": "un outil que Gemini peut appeler",
    "module": "un fichier ou un paquet de code",
    "regle": "une règle appliquée par le code",
    "donnee": "un fichier de données ou un état persistant",
    "test": "une suite de vérifications",
    "procedure": "une marche à suivre",
    "limite": "une limite ou un bug connu",
}
#: L'état d'un nœud, tel qu'il a été vérifié à la date du graphe.
STATUSES = {
    "ok": "fonctionne, vérifié",
    "partiel": "fonctionne en partie",
    "a_tester": "écrit, pas encore vérifié en conditions réelles",
    "bug": "défaut connu",
}
#: Comment deux nœuds se tiennent.
RELATIONS = {
    "contient": "fait partie de",
    "utilise": "s'appuie sur",
    "expose": "rend disponible",
    "protege": "garde ou vérifie",
    "alimente": "fournit des données à",
    "teste": "vérifie",
    "deploie": "met en service",
    "remplace": "succède à",
}


def _path(graph_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", graph_id or ""):
        raise KeyError(graph_id)
    return GRAPHS_DIR / f"{graph_id}.json"


def graph_ids() -> list[str]:
    """Le graphe général d'abord, puis les sous-graphes par ordre alphabétique."""
    ids = sorted(p.stem for p in GRAPHS_DIR.glob("*.json"))
    if GENERAL in ids:
        ids.remove(GENERAL)
        ids.insert(0, GENERAL)
    return ids


def load(graph_id: str = GENERAL) -> dict:
    """Un graphe, tel qu'il est sur le disque. KeyError s'il n'existe pas."""
    path = _path(graph_id)
    if not path.is_file():
        raise KeyError(graph_id)
    return json.loads(path.read_text(encoding="utf-8"))


def dumps(graph: dict) -> str:
    """The on-disk form: one key per line, one node per line, one edge per line.

    Hand edits stay easy and an automatic update (capabilities.verify) shows
    up in a diff as the few nodes it changed, not as a reformatted file."""
    head = [k for k in graph if k not in ("nodes", "edges")]
    out = ["{"]
    for k in head:
        out.append(f"  {json.dumps(k)}: {json.dumps(graph[k], ensure_ascii=False)},")
    nodes = graph.get("nodes", [])
    out.append('  "nodes": [')
    out += [f"    {json.dumps(n, ensure_ascii=False)}{',' if i < len(nodes) - 1 else ''}"
            for i, n in enumerate(nodes)]
    out.append("  ],")
    edges = graph.get("edges", [])
    out.append('  "edges": [')
    out += [f"    {json.dumps(e, ensure_ascii=False)}{',' if i < len(edges) - 1 else ''}"
            for i, e in enumerate(edges)]
    out.append("  ]")
    out.append("}")
    return "\n".join(out) + "\n"


def save(graph: dict) -> None:
    """Write a graph back to graphs/<id>.json, in the on-disk form."""
    _path(graph["id"]).write_text(dumps(graph), encoding="utf-8", newline="\n")
    reset()


def load_all() -> dict[str, dict]:
    return {gid: load(gid) for gid in graph_ids()}


@lru_cache(maxsize=1)
def _index() -> tuple[tuple[str, dict, str], ...]:
    """(id du graphe, nœud, texte cherchable) pour chaque nœud de chaque graphe."""
    rows = []
    for gid, g in load_all().items():
        for n in g.get("nodes", []):
            text = " ".join([n.get("label", ""), n.get("summary", ""), n.get("tool", ""),
                             " ".join(n.get("details", [])), " ".join(n.get("files", []))])
            rows.append((gid, n, _fold(text)))
    return tuple(rows)


def _fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in text if not unicodedata.combining(c))


#: Mots trop courants pour départager deux nœuds.
_STOP = {"les", "des", "est", "que", "qui", "pour", "dans", "avec", "sur", "une", "tu", "peux",
         "est-ce", "sais", "faire", "the", "and", "can", "you", "what", "how", "does"}


def find(query: str, limit: int = 6) -> list[dict]:
    """Les nœuds qui répondent le mieux à `query`, tous graphes confondus.

    Lexical, comme recall_memory. Un mot entier compte plus qu'une racine
    (« chercher » doit trouver « recherche », mais « rappel » doit préférer
    « Rappel » à « Rapport ») ; le libellé compte plus que le résumé, qui
    compte plus que le reste. Chaque résultat dit dans quel graphe il est
    (`in_graph`) ; `graph`, s'il existe, reste le sous-graphe qu'il ouvre.
    """
    words = [w for w in re.findall(r"[\w-]{3,}", _fold(query)) if w not in _STOP]
    if not words:
        return []
    scored = []
    for gid, n, text in _index():
        label = _fold(n.get("label", "") + " " + n.get("tool", ""))
        summary = _fold(n.get("summary", ""))
        score = 0.0
        for w in words:
            stem = w[:max(4, len(w) - 2)] if len(w) > 5 else w
            whole = re.compile(r"\b" + re.escape(w))
            score += 6 * bool(whole.search(label)) + 3 * bool(whole.search(summary)) + 1.5 * bool(whole.search(text))
            score += 2 * (stem in label) + 1 * (stem in summary) + 0.5 * (stem in text)
        if score:
            scored.append((score, gid, n))
    scored.sort(key=lambda t: (-t[0], t[1] != GENERAL))
    return [{**n, "in_graph": gid} for _, gid, n in scored[:limit]]


def domains() -> list[dict]:
    """Les domaines du graphe général, avec le sous-graphe qui les détaille."""
    return [n for n in load(GENERAL).get("nodes", []) if n.get("kind") == "domaine"]


def reset() -> None:
    """Relit les graphes au prochain appel (après une modification sur le disque)."""
    _index.cache_clear()
