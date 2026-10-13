"""
cognition_memory — l'archive de JARVIS : conversations passées et faits avec provenance.

Plugin ordinaire (graphe contrat, cas 1). Il ne fait que lire
cognition.get_active() : hors mode local, ou si cognition/ est absent, il répond
que l'archive est indisponible et JARVIS continue comme avant.
"""

PLUGIN = {
    "name": "recall_episodes",
    "description": (
        "Search your archive of past conversations and facts, with dates and provenance "
        "('what did we talk about last week'). For a quick personal fact use "
        "recall_memory first. Say so if a result is UNVERIFIED or CONTESTED."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING",
                      "description": "Keywords to look for. Empty = most recent memories."},
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from cognition import get_active
    except Exception:
        return "My long-term archive is not available in this mode."
    cog = get_active()
    if cog is None:
        return "My long-term archive is only available on the local desktop brain for now."
    return cog.recall(str((parameters or {}).get("query", "") or ""), limit=5)
