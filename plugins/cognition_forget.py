"""
cognition_forget — « oublie ce que je t'ai dit sur … » : tout, et seulement après CONFIRMER.

Plugin ordinaire (graphe contrat, cas 1). Effacer est irréversible : le
modèle peut DEMANDER l'oubli, il ne l'exécute pas. Le bandeau du HUD montre
combien de souvenirs seront effacés ; core/confirm.py n'exécute l'effacement
que si l'utilisateur appuie sur CONFIRMER.
"""

PLUGIN = {
    "name": "forget_about",
    "description": (
        "Forget everything about a subject the user names ('oublie ce que je t'ai dit "
        "sur ma sœur'): pass its keywords (FR and EN). The user must confirm on the "
        "HUD; never claim it is forgotten before."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "subject": {"type": "STRING",
                        "description": "Keywords of the subject, e.g. 'Ayse sœur sister'"},
        },
        "required": ["subject"],
    },
}


def _v1_matches(subject: str) -> list[dict]:
    """Les entrées de long_term.json (mémoire V1, injectée dans CHAQUE prompt)
    qui mentionnent le sujet : les oublier dans cognition/ seulement laisserait
    la V1 les répéter à chaque connexion."""
    from cognition.policy import content_words
    from memory.memory_manager import all_entries_for_ui
    words = set(content_words(subject))
    return [e for e in all_entries_for_ui()
            if words & set(content_words(f"{e['key'].replace('_', ' ')} {e['value']}"))]


def _erase(cog, subject: str) -> str:
    from memory.memory_manager import forget
    v1 = _v1_matches(subject)
    for e in v1:
        forget(e["key"], e["category"])
    return cog.forget_about(subject) + f" Mémoire V1 : {len(v1)} entrée(s) effacée(s)."


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from cognition import get_active
        from core import confirm
    except Exception:
        return "Forgetting is not available in this mode."
    cog = get_active()
    if cog is None:
        return "My long-term archive is only available on the local desktop brain for now."
    subject = str((parameters or {}).get("subject", "") or "").strip()
    if len(subject) < 2:
        return "Tell me what subject to forget."
    found = cog.forget_preview(subject)
    try:
        v1 = _v1_matches(subject)
    except Exception:
        v1 = []
    found = found + [{"content": f"{e['key']}: {e['value']}"} for e in v1]
    if not found:
        return f"Nothing in my archive mentions '{subject}'."
    if confirm.pending_title():
        return ("Another confirmation is already waiting on screen; ask the user to answer it "
                "first. Nothing was erased.")
    return confirm.request(
        "forget", f"Oublier {len(found)} souvenir(s) : « {subject[:50]} »",
        " · ".join(f["content"][:60] for f in found[:3]) + (" …" if len(found) > 3 else ""),
        lambda: _erase(cog, subject))
