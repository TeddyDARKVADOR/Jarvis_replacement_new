"""
Plugin `skills` — des savoir-faire SKILL.md lus seulement quand il faut.

La logique est dans extensions/skills.py. La description envoyée à Gemini porte
l'index des skills (nom + description), lu au démarrage : un skill ajouté est
vu à la connexion suivante.
"""

try:
    from extensions import skills as _skills
    _INDEX = _skills.index()
except Exception:
    _skills, _INDEX = None, ""

PLUGIN = {
    "name": "skills",
    "description": (
        "Specialised step-by-step instructions for some tasks. When the request matches one "
        "of the skills below, call action=use with its name BEFORE starting, then follow it "
        "with your usual tools. action=read_file reads a reference file of a skill.\n"
        + (_INDEX or "(no skill installed)")
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "use | read_file | list"},
            "name": {"type": "STRING", "description": "Skill name"},
            "path": {"type": "STRING", "description": "read_file: file inside the skill"},
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None) -> str:
    if _skills is None:
        return "Skills indisponibles (extensions/ absent)."
    action = str(parameters.get("action", "list")).strip().lower()
    name = str(parameters.get("name", "")).strip()
    if action == "use":
        return _skills.use(name)
    if action == "read_file":
        return _skills.read_file(name, str(parameters.get("path", "")))
    return _skills.index() or "Aucun skill installé (dossier skills/ ou ~/.jarvis/skills)."
