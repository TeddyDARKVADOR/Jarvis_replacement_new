"""
cognition_goals — objectifs persistants : JARVIS propose, l'utilisateur dispose.

Plugin ordinaire (docs/V2_CONTRACT.md, cas 1), et le SEUL pont entre
cognition/ et core/confirm.py. Le modèle peut proposer, noter un progrès,
retirer sa proposition. Activer, clore, annuler ou relancer un objectif met un
bouton CONFIRMER sur le HUD : l'action n'a lieu que si l'utilisateur appuie.
Il n'existe aucune action de ce plugin qui active un objectif directement.
"""

PLUGIN = {
    "name": "goals",
    "description": (
        "Persistent goals that survive restarts. YOU can only propose them; the user "
        "approves on the HUD. Actions: 'list'; 'propose' (title, justification, "
        "success_criteria, optional priority 1-5, deadline YYYY-MM-DD, budget_actions) — "
        "use when the user states a lasting objective or when you have a well-grounded "
        "suggestion; 'progress' (goal, note) after doing something that serves an ACTIVE "
        "goal; 'request_completion' (goal, note = evidence) when the success criteria "
        "seem met; 'request_cancel' (goal, note); 'request_approval' (goal) to show the "
        "approval button again or resume a blocked goal; 'withdraw' (goal) to drop your "
        "own unapproved proposal. Never claim a goal is approved, done or cancelled "
        "until the user has confirmed on the HUD."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "list | propose | progress | request_completion | "
                                      "request_cancel | request_approval | withdraw"},
            "goal": {"type": "STRING", "description": "Goal id or a few words of its title"},
            "title": {"type": "STRING"},
            "justification": {"type": "STRING", "description": "Why this goal matters"},
            "success_criteria": {"type": "STRING", "description": "How we will know it is done"},
            "priority": {"type": "INTEGER", "description": "1 (low) to 5 (high)"},
            "deadline": {"type": "STRING", "description": "YYYY-MM-DD, optional"},
            "budget_actions": {"type": "INTEGER",
                               "description": "Max actions to spend before asking again"},
            "note": {"type": "STRING", "description": "Progress note or evidence"},
        },
        "required": ["action"],
    },
}

def _ask(cog, key: str, title: str, detail: str, run) -> str:
    """La seule porte vers approve/complete/cancel : core/confirm.py exécute
    `run` si, et seulement si, l'utilisateur appuie sur CONFIRMER."""
    from core import confirm
    if confirm.pending_title():
        return ("Another confirmation is already waiting on screen; ask the user to answer "
                "it first. Nothing was changed.")
    return confirm.request(key, title, detail, run)


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from cognition import get_active
        from cognition.goals import GoalError
    except Exception:
        return "Goals are not available in this mode."
    cog = get_active()
    if cog is None:
        return "Goals only run on the local desktop brain for now."

    p = parameters or {}
    action = str(p.get("action", "list")).strip().lower()
    ref = str(p.get("goal", "") or "")
    note = str(p.get("note", "") or "")
    try:
        if action == "list":
            goals = cog.goals.open_goals()
            if not goals:
                return "No open goals."
            return "Open goals:\n" + "\n".join("- " + cog.goals.describe(g) for g in goals)

        if action == "propose":
            g = cog.goal_propose(str(p.get("title", "")), str(p.get("justification", "")),
                                 str(p.get("success_criteria", "")), p.get("priority") or 3,
                                 p.get("deadline") or None, p.get("budget_actions") or None)
            gid = g["id"]
            ask = _ask(cog, "goal", f"Nouvel objectif : {g['title'][:80]}",
                       f"Pourquoi : {g['justification'][:120]} — Réussi quand : "
                       f"{g['success_criteria'][:120]}",
                       lambda: cog.goal_approve(gid))
            return f"Proposal saved as [{gid}] (status: proposed). {ask}"

        g = cog.goal_find(ref)
        gid = g["id"]
        if action == "progress":
            after = cog.goal_progress(gid, note)
            if after["status"] == "blocked":
                return (f"Progress noted, but the goal is now BLOCKED ({after['closed_reason']}). "
                        "Stop working on it and tell the user; only they can resume it.")
            return f"Progress noted: {cog.goals.describe(after)}"
        if action == "request_completion":
            return _ask(cog, "goal_done", f"Objectif atteint ? {g['title'][:80]}",
                        f"Preuve avancée par JARVIS : {note[:200] or '(aucune)'}",
                        lambda: cog.goal_complete(gid, note))
        if action == "request_cancel":
            return _ask(cog, "goal_cancel", f"Annuler l'objectif : {g['title'][:80]}",
                        note[:200] or "Demandé par JARVIS", lambda: cog.goal_cancel(gid, note))
        if action == "request_approval":
            if g["status"] not in ("proposed", "blocked"):
                return f"This goal is {g['status']}; nothing to approve."
            return _ask(cog, "goal", f"Approuver l'objectif : {g['title'][:80]}",
                        g["closed_reason"] or g["justification"][:200],
                        lambda: cog.goal_approve(gid))
        if action == "withdraw":
            cog.goal_withdraw(gid)
            return f"Proposal [{gid}] withdrawn."
        return f"Unknown action '{action}'."
    except GoalError as e:
        return f"Refused: {e}"
