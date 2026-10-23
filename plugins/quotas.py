"""
plugins/quotas.py — « où en sont mes quotas ? » : le registre commun (paquet quota/).
"""

PLUGIN = {
    "name": "quotas",
    "description": (
        "The free AI quotas Matthew uses today: requests per Gemini model against its daily limit, which models "
        "are exhausted and until when, and when everything resets. Use when the user asks about quotas, limits, "
        "why something is slower or failing, or « combien il me reste »."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import quota
    except ImportError:
        return "Le registre des quotas n'est pas installé (dossier quota/ absent)."
    try:
        text = quota.summary()
    except Exception as e:
        return f"Registre des quotas illisible : {e}"
    if player:
        try:
            player.show_content("QUOTAS", text)
        except Exception:
            pass
    return text
