"""
plugins/incidents.py — les erreurs réelles de JARVIS (paquet incidents/), et leur partage avec Night Shift.
"""

PLUGIN = {
    "name": "incidents",
    "description": (
        "JARVIS's own real errors over the last 7 days, grouped by kind and counted, most frequent first — what "
        "the tests don't catch. Actions: status (« quelles erreurs tu as eues ? ») · share with value on|off "
        "(send an anonymised digest — signatures and counts only — to the PUBLIC Night Shift data branch each "
        "night, so the nightly report ranks them; ask the user to confirm they accept it is public) · push (send "
        "it now, if sharing is on)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {"action": {"type": "STRING", "description": "status | share | push"},
                       "value": {"type": "STRING", "description": "For share: on | off"}},
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import incidents
    except ImportError:
        return "Le suivi des incidents n'est pas installé (dossier incidents/ absent)."
    action = str(parameters.get("action") or "status").strip().lower()
    try:
        from memory.config_manager import _patch_config, load_api_keys
        if action == "share":
            on = str(parameters.get("value") or "").strip().lower() in ("on", "oui", "true", "1")
            _patch_config(incidents_share=on)
            return ("Partage activé : chaque nuit, un résumé anonyme (signatures et compteurs, rien de personnel) part "
                    "sur la branche publique nightshift-data." if on else "Partage désactivé : les erreurs restent sur ce poste.")
        if action == "push":
            if not (load_api_keys() or {}).get("incidents_share"):
                return "Le partage est désactivé : active-le d'abord (« partage les incidents »)."
            err = incidents.push()
            return "Résumé envoyé à Night Shift." if err is None else f"Envoi impossible : {err}"
        text = incidents.summary()
        if player:
            try:
                player.show_content("INCIDENTS", text)
            except Exception:
                pass
        return text
    except Exception as e:
        return f"Incidents : {type(e).__name__}: {e}"
