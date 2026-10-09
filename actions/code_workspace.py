"""
code_studio — Jarvis Code Studio, his development workshop on this PC.

A thin door onto core/code_workspace: every operation is executed, checked and
journaled there, so what the HUD's STUDIO panel shows is what the tools really
did. This file only translates the model's call.

Long work ("create", "modify") runs in the background and returns at once; the
outcome is announced when it is finished (main.plugin_say through
`player.request_say`). The workspace root can only be chosen in the HUD.
"""

from __future__ import annotations


def _available(player) -> str | None:
    # On the Oracle server (HeadlessUI without local_mode) the "workspace" would
    # be on the server, not on this PC — which is not what the Studio promises.
    if type(player).__name__ == "HeadlessUI" and not getattr(player, "local_mode", False):
        return ("The Code Studio only works when JARVIS runs on this PC "
                "(python main.py --local). Tell the user so in one sentence.")
    return None


def code_studio(parameters: dict, player=None) -> str:
    refused = _available(player)
    if refused:
        return refused
    from core.code_workspace import get_studio
    return get_studio().tool(dict(parameters or {}), player=player)


TOOL = {
    "name": "code_studio",
    "description": (
        "Jarvis Code Studio: your own isolated development workspace on this PC (a dedicated folder). "
        "Use it when the user asks you to create, build, code or develop an application, a program, a "
        "script or a project 'in your workspace' / 'dans ton espace de travail' / 'dans ton atelier', "
        "or wants to SEE your progress, or to continue/modify/test/preview/restore one of your Studio "
        "projects. action=create starts an autonomous job (plan, write files, run real tests, fix, "
        "report) in the background with live progress in the HUD's STUDIO panel — it returns "
        "immediately and you are told the real result later. action=modify does the same on an existing "
        "project. Other actions: status, list_projects, info, files, read_file, write_file, delete_file, "
        "run (one allowed command: python/pytest/node/npm, no shell), test, checkpoint, checkpoints, "
        "restore, diff, preview, stop, workspace. Never claim a result the tool did not return."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "create | modify | status | list_projects | info | files | read_file | "
                               "write_file | delete_file | run | test | checkpoint | checkpoints | restore | "
                               "diff | preview | stop | workspace (default: status)",
            },
            "request": {
                "type": "STRING",
                "description": "create/modify: the user's full request, in their words, with every detail",
            },
            "project": {
                "type": "STRING",
                "description": "Project folder name (optional for create; defaults to the current project)",
            },
            "path": {"type": "STRING", "description": "Relative file path inside the project"},
            "content": {"type": "STRING", "description": "write_file: complete file content"},
            "command": {"type": "STRING", "description": "run: e.g. 'python main.py' or 'python -m unittest -v'"},
            "checkpoint": {"type": "STRING", "description": "restore/diff: checkpoint id, or 'last'"},
            "label": {"type": "STRING", "description": "checkpoint: a short label"},
        },
    },
    "handler": code_studio,
}
