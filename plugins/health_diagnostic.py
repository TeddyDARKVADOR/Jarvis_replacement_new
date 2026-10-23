"""Read-only diagnostics for Matthew running locally on the desktop."""
from __future__ import annotations


PLUGIN = {
    "name": "local_health_check",
    "description": (
        "Read-only health check of local Matthew: Gemini, chosen audio devices, actions, "
        "plugins, phone dashboard. Reports configuration, not whether a device "
        "physically works."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}


_LINK_LABELS = {
    "CONNECTED": "connecté",
    "RECONNECTING": "reconnexion en cours",
    "CONNECTING": "connexion en cours",
    "ERROR": "erreur d'authentification ou de connexion",
    "OFFLINE": "hors ligne",
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    if not getattr(player, "local_mode", False):
        return "Le diagnostic local est disponible lorsque Matthew tourne sur le PC."
    reader = getattr(player, "health_snapshot", None)
    if not callable(reader):
        return "Le diagnostic local n'est pas disponible dans cette interface."
    data = reader()
    if data.get("error"):
        return f"Diagnostic incomplet : {data['error']}."

    link = str(data.get("link", "UNKNOWN"))
    lines = [
        f"Gemini Live : {_LINK_LABELS.get(link, link.lower())}.",
        "Clé API : configurée." if data.get("gemini_key_configured")
        else "Clé API : absente.",
        f"Micro sélectionné : {data.get('microphone_setting') or 'inconnu'}.",
        f"Sortie audio sélectionnée : {data.get('speaker_setting') or 'inconnue'}.",
        f"Actions chargées : {int(data.get('actions_loaded', 0))}.",
        (f"Plugins chargés : {int(data.get('plugins_enabled', 0))} actifs sur "
         f"{int(data.get('plugins_loaded', 0))} valides."),
        ("Relais téléphone : initialisé." if data.get("dashboard_initialized")
         else "Relais téléphone : indisponible."),
        "HUD et réglages locaux accessibles même si Gemini est hors ligne.",
    ]
    return " ".join(lines)
