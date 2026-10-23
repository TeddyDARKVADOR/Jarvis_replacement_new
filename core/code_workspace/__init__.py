"""
core/code_workspace — Matthew Code Studio, his development workshop.

A dedicated folder on this PC (``C:\\Matthew_Workspace`` by default) where
Matthew creates projects, writes code, runs tests and shows his progress live in
the HUD — without touching the Matthew repository, Android, or anything else.

    sandbox.py      the root folder and the path checks every read/write goes through
    journal.py      the append-only event log: what was REALLY done, by the tools
    tasks.py        the living task list (à faire / en cours / terminé / bloqué)
    checkpoints.py  snapshots before each series of changes, diff, restore
    runner.py       the controlled terminal: allow-list, timeout, Job Object, stop
    diagnose.py     traceback → file/line, error signatures (loop detection)
    llm.py          the planner / writer / fixer prompts (Gemini, injectable)
    engine.py       the autonomous loop: plan → create → verify/fix → present
    service.py      the one Studio object the tool and the HUD both talk to

Nothing in here is imported by main.py. The tool (actions/code_workspace.py)
is discovered like any other action; the HUD reaches the service through
local_hud.LocalBackend. Removing this package removes the feature, nothing else.

THE SECURITY MODEL, HONESTLY
    Every file operation Matthew's tools perform is checked by sandbox.py — that
    part is enforced by code, not by asking the model nicely. Commands are run
    without a shell, from an allow-list, inside the project folder, with a
    sanitised environment, a timeout and a Windows Job Object (memory and
    process caps, killed as a whole on stop).

    But a program Matthew writes and then *runs* executes with your Windows
    account's rights: it could, in principle, open files elsewhere. Path checks
    cannot prevent that. Real isolation of executed code needs an OS sandbox
    (Windows Sandbox, a VM, a dedicated low-privilege account). The HUD says so.
"""

from .service import get_studio  # noqa: F401
