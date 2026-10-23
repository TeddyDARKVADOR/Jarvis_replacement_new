"""
plugins/pilot.py — l'outil vocal du pilote (paquet pilot/).

PLUGIN_DEVICE : il s'exécute sur le PC, là où sont l'écran, le clavier et la
souris — en local dans ce processus ; relié à Oracle, le serveur le route vers
le PC (client_desktop/device.py).
"""

PLUGIN = {
    "name": "pilot",
    "description": (
        "Operate ANY application or website on this computer for the user, step by step, like a human: it "
        "looks at the screen, clicks, types and checks, and adapts to whatever app is there (Teams, Outlook, a "
        "UML site, VS Code, settings, any software). Use it for multi-step work inside an app: « envoie à Paul "
        "sur Teams : je serai en retard », « fais le diagramme de classes sur mon site UML », « dans VS Code, "
        "ouvre le fichier de l'erreur », « remplis ce formulaire ». Give the whole task in `task`, with every "
        "detail the user said (recipient, exact text, file names). It runs in the background and reports when "
        "done; it asks the user before sending, posting, deleting or paying. Actions: do (with task) · stop · "
        "status. Prefer open_app for simply opening an app, and code_studio to build a project."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "do | stop | status"},
            "task": {"type": "STRING", "description": "For do: the complete task, in the user's words"},
        },
        "required": ["action"],
    },
}

PLUGIN_DEVICE = ("PIL",)


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import pilot
    except ImportError:
        return "Le pilote n'est pas installé (dossier pilot/ absent)."
    p = pilot.get()
    if p is None:
        return ("Le pilote agit sur le PC, avec son écran : il n'est pas actif ici. "
                "Ouvre Matthew sur le PC (HUD) pour qu'il puisse prendre la main.")
    action = str(parameters.get("action") or "status").strip().lower()
    try:
        if action == "do":
            return p.start(str(parameters.get("task") or ""))
        if action == "stop":
            return p.stop()
        return p.status()
    except Exception as e:
        return f"Pilote : {type(e).__name__}: {e}"
