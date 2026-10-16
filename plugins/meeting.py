"""
plugins/meeting.py — l'outil vocal du copilote de réunion (paquet meeting/).

Le copilote démarre seul quand une visio prend le micro ou la caméra ; cet outil
sert à le piloter à la voix. PLUGIN_DEVICE : il s'exécute sur le poste où sont
le micro et l'écran — en local, dans ce processus ; relié à Oracle, le serveur
le route vers le PC (client_desktop/device.py), qui a le copilote.
"""

PLUGIN = {
    "name": "meeting",
    "description": (
        "Meeting copilot: listens to any video call on this computer (Teams, Zoom, Meet, Discord…) through the "
        "computer's sound and the microphone, transcribes locally and writes minutes with actions turned into "
        "reminders and proposed goals. It starts BY ITSELF when another app takes the mic or camera. Actions: "
        "start (« je suis en réunion », « prends des notes ») · stop (« arrête les notes », « la réunion est "
        "finie ») · status (« où en est la réunion ? ») · recent (what was just said) · last (the last minutes) · "
        "mode with value auto|ask|off. Do NOT use reminder or goals for this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "start | stop | status | recent | last | mode"},
            "value": {"type": "STRING", "description": "For mode: auto | ask | off"},
        },
        "required": ["action"],
    },
}

PLUGIN_DEVICE = ("soundcard", "faster_whisper", "numpy")


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import meeting
    except ImportError:
        return "Le copilote de réunion n'est pas installé (dossier meeting/ absent)."
    cop = meeting.get()
    if cop is None:
        return ("Le copilote de réunion tourne sur le PC, avec son micro : il n'est pas actif ici. "
                "Ouvre JARVIS sur le PC (HUD) pour qu'il écoute les visios.")
    action = str(parameters.get("action") or "status").strip().lower()
    try:
        if action == "start":
            return cop.start(manual=True, reason="à la demande")
        if action == "stop":
            return cop.stop("à la demande")
        if action == "recent":
            lines = cop.recent()
            return "\n".join(x.render() for x in lines) if lines else cop.status()
        if action == "last":
            last = cop.last
            if not last:
                return "Aucun compte rendu depuis le démarrage de JARVIS."
            return (f"Dernière réunion ({last['started']}, {last['minutes']} min), fichier : {last['path']}\n\n"
                    + meeting.summary_text(last["result"]))
        if action == "mode":
            value = str(parameters.get("value") or "").strip().lower()
            if value not in ("auto", "ask", "off"):
                return "Mode : auto (je prends des notes seul), ask (je propose) ou off."
            return meeting.set_mode(value)
        return cop.status()
    except Exception as e:
        return f"Copilote de réunion : {type(e).__name__}: {e}"
