"""
cognition_self — l'état de JARVIS tel qu'il est ENREGISTRÉ, pas tel qu'il l'imagine.

Plugin ordinaire (graphe contrat, cas 1). Lit cognition.get_active() ;
hors mode local, ou si cognition/ est absent, répond que c'est indisponible.
"""

PLUGIN = {
    "name": "self_state",
    "description": (
        "Read YOUR OWN recorded internal state: computed emotion variables (valence, "
        "arousal, assurance, frustration, curiosity, warmth), what caused their latest "
        "changes, active guidance, memory counts. Call it whenever the user asks how you "
        "feel, how you are doing, what is going on inside you, or why you act a certain "
        "way — and answer from its numbers, never from imagination. These are computed "
        "variables, not felt emotions: say so if relevant. Also gives your measured "
        "reliability per tool, calibration and hard limits: use it when asked how reliable "
        "you are or how sure you are. For which features you have, use 'capabilities'."
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
