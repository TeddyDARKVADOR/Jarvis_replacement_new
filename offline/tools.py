"""
offline/tools.py — ce que Matthew peut encore faire sans réseau.

Les actions de actions/ sont réutilisées telles quelles (core.action_loader),
mais seulement celles qui marchent sans Gemini ni Internet : ouvrir une
application, régler l'ordinateur, gérer des fichiers, le bureau, un rappel.
Leurs garde-fous restent les leurs : arrêt, redémarrage et Wi-Fi passent par
core/confirm, qui refuse quand aucune interface ne peut demander.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED = ("open_app", "computer_settings", "file_controller", "desktop_control", "reminder")


class _Player:
    """Ce que les actions attendent de `player` : un journal."""

    def __init__(self, log):
        self.write_log = log
        self.request_say = None


def _now(_args: dict) -> str:
    return datetime.now().strftime("%A %d %B %Y, %H:%M")


def build(log, allowed=ALLOWED, actions_dir: Path | None = None) -> dict:
    """nom -> (déclaration Gemini, fonction(args) -> str)."""
    tools: dict = {"current_time": (
        {"description": "Current local date and time.", "parameters": {"type": "OBJECT", "properties": {}}},
        _now)}
    try:
        from core.action_loader import discover_actions
        reg = discover_actions(actions_dir or REPO / "actions", logger=lambda *_: None)
    except Exception as e:
        log(f"SYS: hors-ligne — actions indisponibles : {e}")
        return tools
    player = _Player(log)
    decls = {d["name"]: d for d in reg.get_tool_declarations()}
    for name in allowed:
        if name in decls:
            tools[name] = (decls[name],
                           lambda args, _n=name: reg.run(_n, args, {"player": player}))
    return tools
