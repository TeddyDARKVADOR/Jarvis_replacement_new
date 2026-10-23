"""
cognition_self — l'état de Matthew tel qu'il est ENREGISTRÉ, pas tel qu'il l'imagine.

Plugin ordinaire (graphe contrat, cas 1). Lit cognition.get_active() ;
hors mode local, ou si cognition/ est absent, répond que c'est indisponible.
"""

PLUGIN = {
    "name": "self_state",
    "description": (
        "Your RECORDED internal state: computed emotion variables and their causes, "
        "active guidance, measured reliability per tool, calibration, hard limits. Use "
        "when asked how you feel or how sure/reliable you are; answer from its numbers "
        "(computed, not felt). For which features you have, use capabilities."
    ),
    "parameters": {"type": "OBJECT", "properties": {
        "about": {"type": "STRING",
                  "description": "Optional: a tool name, to get its measured reliability"},
    }, "required": []},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from cognition import get_active
    except Exception:
        return "My internal-state module is not available in this mode."
    cog = get_active()
    if cog is None:
        return "My internal-state module only runs on the local desktop brain for now."
    return cog.self_report(str((parameters or {}).get("about", "") or ""))
