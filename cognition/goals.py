"""
cognition/goals.py — objectifs persistants (phase 5).

LA RÈGLE, APPLIQUÉE ICI ET TESTÉE
    JARVIS PROPOSE, l'utilisateur DISPOSE.

    - Tout objectif créé par le modèle naît `proposed`. Il ne devient `active`
      que par approve(), et le seul chemin vers approve() est le bouton
      CONFIRMER du HUD (core/confirm.py, via plugins/cognition_goals.py) : un
      jeton émis par l'interface, que le modèle ne peut pas fabriquer.
    - Il en va de même pour clore (`done`) ou annuler un objectif actif : le
      modèle peut le DEMANDER, pas le faire. « J'ai fini » est une affirmation,
      pas une preuve (docs/cognition/RECHERCHE.md, §1.1).
    - Le modèle peut, seul : noter un progrès, retirer SA proposition.

LES LIMITES (contre les boucles et la consommation de ressources)
    - budget d'actions par objectif : dépassé -> `blocked`, plus rien n'est
      compté, il faut que l'utilisateur relance ;
    - la même note de progrès répétée REPEAT_LIMIT fois -> `blocked` (boucle) ;
    - échéance passée -> `expired` ;
    - au plus MAX_OPEN objectifs ouverts, au plus MAX_PROPOSALS_PER_DAY
      propositions de JARVIS par jour.

    Un objectif n'est jamais effacé : son historique (goal_events) reste.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

STATUSES = ("proposed", "active", "blocked", "done", "cancelled", "expired", "withdrawn")
OPEN = ("proposed", "active", "blocked")
KINDS = ("user", "reliability", "learning", "mastery", "continuity")

MAX_OPEN = 10
MAX_PROPOSALS_PER_DAY = 3
DEFAULT_BUDGET = 20
MAX_BUDGET = 200
REPEAT_LIMIT = 3


class GoalError(ValueError):
    """Une demande refusée par les règles. Le message est dit à l'utilisateur."""


class Goals:
    def __init__(self, store, now=None):
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _ts(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _event(self, c, gid: str, kind: str, note: str = "", by: str = "model") -> None:
        c.execute("INSERT INTO goal_events(goal_id, ts, kind, note, actor) VALUES (?,?,?,?,?)",
                  (gid, self._ts(), kind, note[:300], by))

    # ── Lecture ──────────────────────────────────────────────────────────────

    def get(self, gid: str) -> dict | None:
        rows = self.store.query("SELECT * FROM goals WHERE id=?", (gid,))
        return dict(rows[0]) if rows else None

    def find(self, ref: str) -> dict | None:
        """Par id, préfixe d'id ou titre approchant : le modèle cite rarement un
        id exact à l'oral."""
        ref = (ref or "").strip()
        if not ref:
            return None
        g = self.get(ref)
        if g:
            return g
        rows = self.store.query(
            "SELECT * FROM goals WHERE id LIKE ? OR lower(title) LIKE ? "
            "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'proposed' THEN 1 ELSE 2 END, "
            "updated_at DESC LIMIT 1", (ref + "%", f"%{ref.lower()}%"))
        return dict(rows[0]) if rows else None

    def open_goals(self) -> list[dict]:
        self.expire()
        return [dict(r) for r in self.store.query(
            "SELECT * FROM goals WHERE status IN ('proposed','active','blocked') "
            "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END, "
            "priority DESC, coalesce(deadline, '9999') ASC, created_at ASC")]

    def history(self, gid: str) -> list[dict]:
        return [dict(r) for r in self.store.query(
            "SELECT * FROM goal_events WHERE goal_id=? ORDER BY id", (gid,))]

    # ── Ce que le modèle peut faire seul ─────────────────────────────────────

    def propose(self, title: str, *, justification: str, success_criteria: str,
                kind: str = "user", priority: int = 3, deadline: str | None = None,
                budget_actions: int | None = None, origin: str = "jarvis") -> dict:
        title = (title or "").strip()[:160]
        if not title:
            raise GoalError("Un objectif a besoin d'un titre.")
        if not (justification or "").strip() or not (success_criteria or "").strip():
            raise GoalError("Un objectif a besoin d'une justification et d'un critère de réussite.")
        if kind not in KINDS:
            kind = "user"
        priority = max(1, min(5, int(priority or 3)))
        if deadline:
            try:
                d = date.fromisoformat(str(deadline)[:10])
            except ValueError:
                raise GoalError("Échéance illisible : format AAAA-MM-JJ attendu.")
            if d < self._now().date():
                raise GoalError("L'échéance est déjà passée.")
            deadline = d.isoformat()
        budget = max(1, min(MAX_BUDGET, int(budget_actions or DEFAULT_BUDGET)))

        with self.store.transaction() as c:
            n_open = c.execute("SELECT count(*) FROM goals WHERE status IN "
                               "('proposed','active','blocked')").fetchone()[0]
            if n_open >= MAX_OPEN:
                raise GoalError(f"Déjà {n_open} objectifs ouverts : il faut en clore un d'abord.")
            today = self._now().date().isoformat()
            n_today = c.execute("SELECT count(*) FROM goals WHERE origin='jarvis' "
                                "AND substr(created_at, 1, 10)=?", (today,)).fetchone()[0]
            if origin == "jarvis" and n_today >= MAX_PROPOSALS_PER_DAY:
                raise GoalError("J'ai déjà proposé assez d'objectifs aujourd'hui.")
            dup = c.execute("SELECT id FROM goals WHERE lower(title)=? AND status IN "
                            "('proposed','active','blocked')", (title.lower(),)).fetchone()
            if dup:
                raise GoalError("Cet objectif existe déjà.")
            gid = uuid.uuid4().hex[:8]
            ts = self._ts()
            c.execute(
                "INSERT INTO goals(id, title, kind, origin, justification, priority, status, "
                "success_criteria, deadline, budget_actions, spent_actions, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (gid, title, kind, origin, justification.strip()[:400], priority, "proposed",
                 success_criteria.strip()[:400], deadline, budget, ts, ts))
            self._event(c, gid, "proposed", justification)
        return self.get(gid)

    def progress(self, gid: str, note: str) -> dict:
        """Une action au service d'un objectif actif. Compte dans le budget."""
        g = self.get(gid)
        if not g:
            raise GoalError("Objectif introuvable.")
        if g["status"] != "active":
            raise GoalError(f"Cet objectif est « {g['status']} », pas actif : rien n'est compté.")
        note = (note or "").strip()[:300]
        with self.store.transaction() as c:
            spent = g["spent_actions"] + 1
            last = [r[0] for r in c.execute(
                "SELECT note FROM goal_events WHERE goal_id=? AND kind='progress' "
                "ORDER BY id DESC LIMIT ?", (gid, REPEAT_LIMIT - 1)).fetchall()]
            looping = (note and len(last) == REPEAT_LIMIT - 1
                       and all(n.lower() == note.lower() for n in last))
            c.execute("UPDATE goals SET spent_actions=?, updated_at=? WHERE id=?",
                      (spent, self._ts(), gid))
            self._event(c, gid, "progress", note)
            if looping:
                self._block(c, gid, f"boucle : « {note[:60]} » répété {REPEAT_LIMIT} fois")
            elif spent >= g["budget_actions"]:
                self._block(c, gid, f"budget de {g['budget_actions']} actions épuisé")
        return self.get(gid)

    def withdraw(self, gid: str) -> dict:
        """Retirer SA proposition, jamais un objectif approuvé."""
        g = self.get(gid)
        if not g or g["status"] != "proposed":
            raise GoalError("Seule une proposition pas encore approuvée peut être retirée.")
        return self._set(gid, "withdrawn", "retirée par JARVIS", by="model")

    # ── Ce qui exige le bouton CONFIRMER ─────────────────────────────────────
    # Appelé UNIQUEMENT depuis la fonction que core/confirm.py exécute quand
    # l'utilisateur appuie (plugins/cognition_goals.py). by="user".

    def approve(self, gid: str) -> str:
        g = self.get(gid)
        if not g or g["status"] not in ("proposed", "blocked"):
            return "Rien à approuver."
        if g["status"] == "blocked":
            # Relancer un objectif bloqué remet son compteur à zéro : c'est
            # l'utilisateur qui accorde un nouveau budget.
            self.store.execute("UPDATE goals SET spent_actions=0 WHERE id=?", (gid,))
        self._set(gid, "active", "approuvé sur le HUD", by="user",
                  extra=("approved_at", self._ts()))
        return f"Objectif actif : {g['title']}"

    def complete(self, gid: str, evidence: str = "") -> str:
        g = self.get(gid)
        if not g or g["status"] not in OPEN:
            return "Rien à clore."
        self._set(gid, "done", evidence or "confirmé sur le HUD", by="user")
        return f"Objectif atteint : {g['title']}"

    def cancel(self, gid: str, reason: str = "") -> str:
        g = self.get(gid)
        if not g or g["status"] not in OPEN:
            return "Rien à annuler."
        self._set(gid, "cancelled", reason or "annulé sur le HUD", by="user")
        return f"Objectif annulé : {g['title']}"

    # ── Interne ──────────────────────────────────────────────────────────────

    def _block(self, c, gid: str, reason: str) -> None:
        c.execute("UPDATE goals SET status='blocked', closed_reason=?, updated_at=? WHERE id=?",
                  (reason, self._ts(), gid))
        self._event(c, gid, "blocked", reason, by="system")

    def _set(self, gid: str, status: str, note: str, by: str, extra=None) -> dict:
        with self.store.transaction() as c:
            sql = "UPDATE goals SET status=?, closed_reason=?, updated_at=?"
            params = [status, note if status not in ("active",) else None, self._ts()]
            if extra:
                sql += f", {extra[0]}=?"
                params.append(extra[1])
            c.execute(sql + " WHERE id=?", params + [gid])
            self._event(c, gid, status, note, by=by)
        return self.get(gid)

    def expire(self) -> int:
        today = self._now().date().isoformat()
        rows = self.store.query("SELECT id FROM goals WHERE status IN ('proposed','active','blocked') "
                                "AND deadline IS NOT NULL AND deadline < ?", (today,))
        for r in rows:
            self._set(r["id"], "expired", "échéance dépassée", by="system")
        return len(rows)

    # ── Présentation ─────────────────────────────────────────────────────────

    def describe(self, g: dict) -> str:
        due = f", due {g['deadline']}" if g["deadline"] else ""
        budget = f", {g['spent_actions']}/{g['budget_actions']} actions"
        return f"[{g['id']}] {g['title']} (P{g['priority']}, {g['status']}{due}{budget})"

    def prompt_lines(self, limit: int = 3) -> list[str]:
        goals = self.open_goals()
        active = [g for g in goals if g["status"] == "active"][:limit]
        blocked = [g for g in goals if g["status"] == "blocked"]
        proposed = [g for g in goals if g["status"] == "proposed"]
        if not (active or blocked or proposed):
            return []
        lines = ["[GOALS — only ACTIVE ones were approved by the user. Higher P first; "
                 "if two conflict, ask. Report progress with the goals tool.]"]
        for g in active:
            lines.append("- " + self.describe(g) + f" — done when: {g['success_criteria'][:120]}")
        if blocked:
            lines.append(f"Blocked (need the user): " + "; ".join(g["title"][:50] for g in blocked))
        if proposed:
            lines.append(f"Awaiting approval: {len(proposed)}")
        return lines

    def as_json(self) -> str:
        return json.dumps(self.open_goals(), ensure_ascii=False)
