"""
capabilities — le graphe de ce que JARVIS sait faire, consulté et montré par lui.

Plugin ordinaire (graphe `contrat`, cas 1). Les graphes vivent dans
capabilities/graphs/ ; ce fichier n'en contient aucune ligne.

    find   chercher dans son propre graphe : « sais-tu faire X ? », « comment
           marche Y ? ». La réponse vient des nœuds, avec leur état vérifié.
    list   les domaines, et les sous-graphes qui les détaillent.
    show   afficher le graphe dans le HUD. Seulement quand on le lui demande :
           le graphe n'entre jamais dans le prompt et n'a pas de bouton.

Importé au niveau du module EXPRÈS : si capabilities/ est supprimé, l'import
échoue, le loader rejette ce fichier et l'outil n'est pas proposé à Gemini.
"""
from __future__ import annotations

import capabilities as cap

PLUGIN = {
    "name": "capabilities",
    "description": (
        "Your verified map of what you can do, as graphs. show: when the user asks to SEE, "
        "show or display your features, abilities or graph ('montre-moi', 'affiche') — "
        "optional graph=<id> (server, hud, android, cognition, context, studio, deployment, "
        "eval_lab, preprod, contrat, client_desktop) and focus=<words>. find (query): "
        "whether you can do something or how a feature works; answer from its status. "
        "list: a spoken overview only. issues: what is broken, partial or untested."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["find", "list", "show", "issues"],
                       "description": "find (default) | list | show | issues"},
            "query": {"type": "STRING", "description": "find: what to look for"},
            "graph": {"type": "STRING", "description": "show: which graph (default general)"},
            "focus": {"type": "STRING", "description": "show: a feature to select"},
        },
        "required": [],
    },
}

_STATUS_EN = {"ok": "working, verified", "partiel": "partial", "a_tester": "untested",
              "bug": "known bug"}


def _line(n: dict) -> str:
    status = _STATUS_EN.get(n.get("status", ""), "not assessed")
    tool = f" [tool {n['tool']}]" if n.get("tool") else ""
    where = "" if n.get("in_graph") == cap.GENERAL else f" (graph {n.get('in_graph')})"
    return f"- {n.get('label')}{tool} — {status}{where}: {n.get('summary', '')}"


def _find(query: str) -> str:
    hits = cap.find(query, limit=5)
    if not hits:
        return (f"Nothing in my capability graph matches '{query}'. "
                "Say so plainly rather than guessing; it may not be something I can do.")
    rows = [_line(h) for h in hits]
    for h in hits[:1]:
        rows += [f"    · {d}" for d in h.get("details", [])[:3]]
    return f"From my capability graph ({len(hits)} matches):\n" + "\n".join(rows)


def _list() -> str:
    # Short on purpose: a tool result stays in the Live context and is billed
    # again on every later turn of the session.
    g = cap.load(cap.GENERAL)
    names = "; ".join(d["label"] for d in cap.domains())
    tools = sum(1 for n in g["nodes"] if n.get("kind") == "outil")
    return (f"My capability graph (updated {g.get('updated')}), {tools} tools in these domains: "
            f"{names}. Use find for details, show to display it.")


_ISSUE_ORDER = {"bug": 0, "partiel": 1, "a_tester": 2}


def _issues() -> str:
    """What the graph itself says is broken, partial or untested."""
    rows = []
    for gid in cap.graph_ids():
        for n in cap.load(gid)["nodes"]:
            if n.get("status") in _ISSUE_ORDER:
                rows.append((_ISSUE_ORDER[n["status"]], gid, n))
    if not rows:
        return "My graph reports no known bug, partial or untested feature."
    rows.sort(key=lambda r: (r[0], r[1] != cap.GENERAL))
    counts = {k: sum(1 for r in rows if r[2]["status"] == k) for k in _ISSUE_ORDER}
    head = (f"{counts['bug']} known bugs, {counts['partiel']} partial, "
            f"{counts['a_tester']} untested. Most important:")
    return head + "\n" + "\n".join(_line({**n, "in_graph": gid}) for _, gid, n in rows[:8])


def _show(graph: str, focus: str, player) -> str:
    graph = (graph or cap.GENERAL).strip().lower()
    if graph not in cap.graph_ids():
        return f"No graph named '{graph}'. Available: {', '.join(cap.graph_ids())}."
    target = ""
    if focus:
        hits = [h for h in cap.find(focus, limit=10) if h["in_graph"] == graph] or cap.find(focus, limit=1)
        if hits:
            graph, target = hits[0]["in_graph"], hits[0]["id"]
    show = getattr(player, "show_graph", None)
    if not callable(show):
        return "This interface cannot display my graph; I can describe it instead."
    show(graph, target)
    title = cap.load(graph).get("title", graph)
    return f"Displayed on the HUD: {title}." + (f" Selected: {target}." if target else "")


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "find").strip().lower()
    try:
        cap.reset()
        if action == "list":
            return _list()
        if action == "issues":
            return _issues()
        if action == "show":
            return _show(str(p.get("graph") or ""), str(p.get("focus") or ""), player)
        query = str(p.get("query") or p.get("focus") or "").strip()
        return _find(query) if query else _list()
    except Exception as exc:
        return f"My capability graph could not be read: {exc}"
