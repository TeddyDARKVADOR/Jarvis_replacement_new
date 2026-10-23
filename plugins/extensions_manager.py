"""
plugins/extensions_manager.py — l'outil vocal du gestionnaire d'extensions (extensions/manager.py).
"""

PLUGIN = {
    "name": "extensions",
    "description": (
        "Manage Matthew extensions (plugins) safely: inspect (what a plugin file declares AND what its code really "
        "does — network, shell, file writes… — without running it, plus its SHA-256), install (from a local path "
        "or an https:// URL, optional sha256 pin; asks CONFIRM on the HUD; keeps the previous version), list, "
        "disable / enable, restore (previous version), remove; mcp_allow / mcp_confirm with value « server/tool » "
        "(let an MCP tool run without confirmation, e.g. a read-only one — or require it again)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | inspect | install | disable | enable | restore | remove | mcp_allow | mcp_confirm"},
            "value": {"type": "STRING", "description": "Path or https URL (inspect/install), extension name, or server/tool"},
            "sha256": {"type": "STRING", "description": "For install: the expected SHA-256 (refused if different)"},
        },
        "required": ["action"],
    },
}

_MATTHEW_ASSISTANT = None          # posé par extensions.manager.attach via le hook de main.py


def _matthew_assistant():
    return _MATTHEW_ASSISTANT


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from extensions import manager as M
    except ImportError:
        return "Le gestionnaire d'extensions n'est pas installé."
    action = str(parameters.get("action") or "list").strip().lower()
    value = str(parameters.get("value") or "").strip()
    matthew_assistant = _matthew_assistant()
    try:
        if action == "inspect":
            return M.inspect(value, taken=M._taken(matthew_assistant) if matthew_assistant else None).render()
        if action == "install":
            if matthew_assistant is None:
                return "Installation possible seulement dans Matthew lancé (le registre des outils est nécessaire)."
            return M.install(matthew_assistant, value, str(parameters.get("sha256") or "").strip())
        if action in ("disable", "enable"):
            from memory.config_manager import save_plugin_enabled
            save_plugin_enabled(value, action == "enable")
            return f"{value} : {'activée' if action == 'enable' else 'désactivée'}."
        if action == "restore":
            return M.restore(matthew_assistant, value)
        if action == "remove":
            return M.remove(matthew_assistant, value)
        if action in ("mcp_allow", "mcp_confirm"):
            return _mcp(value, allow=action == "mcp_allow")
        rows = M.installed()
        if not rows:
            return "Aucune extension installée (user_plugins/ est vide). Les plugins du dépôt sont dans plugins/."
        return "\n".join(f"{r['name']} {r['version']} · {r['sha256']} · déclare {', '.join(r['permissions']) or 'rien'}"
                         + (f" · ⚠️ fait sans déclarer : {', '.join(r['undeclared'])}" if r["undeclared"] else "")
                         + (f" · {r['previous']} version(s) précédente(s)" if r["previous"] else "") for r in rows)
    except Exception as e:
        return f"Extensions : {type(e).__name__}: {e}"


def _mcp(value: str, allow: bool, path=None) -> str:
    """Ajoute ou retire un outil de l'auto_approve de son serveur dans config/mcp.json : la décision de
    l'utilisateur, jamais celle du serveur (ses annotations restent ignorées)."""
    import json
    from pathlib import Path
    if "/" not in value:
        return "Donne « serveur/outil », par exemple « agenda/list_events »."
    server, tool = value.split("/", 1)
    path = Path(path) if path else Path(__file__).resolve().parent.parent / "config" / "mcp.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "config/mcp.json absent ou illisible : aucun serveur MCP configuré."
    servers = cfg.get("mcpServers") or cfg.get("servers") or {}
    if server not in servers:
        return f"Serveur MCP « {server} » inconnu ({', '.join(servers) or 'aucun'})."
    auto = set(servers[server].get("auto_approve") or [])
    auto = auto | {tool} if allow else auto - {tool}
    servers[server]["auto_approve"] = sorted(auto)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return (f"{server}/{tool} passera sans confirmation." if allow
            else f"{server}/{tool} demandera de nouveau CONFIRMER.")
