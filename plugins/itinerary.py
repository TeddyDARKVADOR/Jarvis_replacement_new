"""Open a prefilled route in the local desktop browser.

This deliberately creates a navigation link only. It does not read device
location, calculate travel times, or call a directions API.
"""
from __future__ import annotations

import webbrowser
from urllib.parse import urlencode


_TRAVEL_MODES = {
    "driving": "driving",
    "car": "driving",
    "voiture": "driving",
    "walking": "walking",
    "marche": "walking",
    "bicycling": "bicycling",
    "bike": "bicycling",
    "vélo": "bicycling",
    "transit": "transit",
    "transport en commun": "transit",
    "train": "transit",
}


PLUGIN = {
    "name": "open_itinerary",
    "description": (
        "Open a Google Maps directions route on the local JARVIS desktop. "
        "Use when the user asks how to get from a stated origin to a stated "
        "destination. Ask for either place if missing. This opens a route link; "
        "it does not know the user's current location or calculate live travel times."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "origin": {
                "type": "STRING",
                "description": "Starting point explicitly provided by the user",
            },
            "destination": {
                "type": "STRING",
                "description": "Destination explicitly provided by the user",
            },
            "travel_mode": {
                "type": "STRING",
                "description": "driving (default), walking, bicycling, or transit",
            },
        },
        "required": ["origin", "destination"],
    },
}


def _route_url(origin: str, destination: str, travel_mode: str) -> str:
    mode = _TRAVEL_MODES.get((travel_mode or "driving").strip().lower(), "driving")
    return "https://www.google.com/maps/dir/?" + urlencode({
        "api": "1",
        "origin": origin,
        "destination": destination,
        "travelmode": mode,
    })


def run(parameters: dict, player=None, session_memory=None) -> str:
    origin = str(parameters.get("origin") or "").strip()
    destination = str(parameters.get("destination") or "").strip()
    if not origin or not destination:
        return "Indiquez le point de départ et la destination pour ouvrir l'itinéraire."

    if not getattr(player, "local_mode", False):
        return (
            "L'ouverture d'itinéraire est disponible lorsque JARVIS tourne en mode "
            "local sur le PC."
        )

    mode = str(parameters.get("travel_mode") or "driving").strip().lower()
    url = _route_url(origin, destination, mode)
    try:
        if not webbrowser.open(url):
            return "Je n'ai pas pu ouvrir le navigateur pour l'itinéraire."
    except Exception as exc:
        return f"Je n'ai pas pu ouvrir le navigateur pour l'itinéraire : {exc}"

    labels = {
        "driving": "en voiture",
        "car": "en voiture",
        "voiture": "en voiture",
        "walking": "à pied",
        "marche": "à pied",
        "bicycling": "à vélo",
        "bike": "à vélo",
        "vélo": "à vélo",
        "transit": "en transport en commun",
        "transport en commun": "en transport en commun",
        "train": "en transport en commun",
    }
    label = labels.get(mode, "en voiture")
    return f"J'ai ouvert l'itinéraire {label} de {origin} à {destination}."
