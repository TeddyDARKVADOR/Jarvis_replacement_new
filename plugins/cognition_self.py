"""
cognition_self — l'état de JARVIS tel qu'il est ENREGISTRÉ, pas tel qu'il l'imagine.

Plugin ordinaire (docs/V2_CONTRACT.md, cas 1). Lit cognition.get_active() ;
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
        "variables, not felt emotions: say so if relevant."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from cognition import get_active
    except Exception:
        return "My internal-state module is not available in this mode."
    cog = get_active()
    if cog is None:
        return "My internal-state module only runs on the local desktop brain for now."
    return cog.self_report()
