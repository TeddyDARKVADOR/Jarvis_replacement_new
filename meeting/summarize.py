"""
meeting/summarize.py — la transcription devient un compte rendu.

    result = summarize(transcript, brain, started=datetime, duration_s=…)
    md     = to_markdown(result, transcript)

Le modèle rend un JSON strict : titre, résumé, décisions, actions (qui, quoi,
échéance absolue), questions ouvertes. La date du jour et son nom sont donnés,
pour que « vendredi » devienne une date. Une longue réunion est d'abord
résumée par morceaux (CHUNK_CHARS, sous la limite de 8 000 tokens par minute
de Groq gratuit), puis les notes sont fusionnées. Une réponse qui n'est pas du
JSON n'est pas perdue : elle devient le résumé, sans actions.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

CHUNK_CHARS = 14_000
DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

SYSTEM = (
    "You write meeting minutes from a raw automatic transcript. Lines are tagged « Moi » (the user, who owns "
    "this assistant) and « Participants » (everyone else, heard through the computer). The transcript has "
    "recognition errors: fix obvious ones from context, never invent facts. Write in the language of the "
    "transcript.")

FINAL = (
    "Now: {weekday} {date}, {now}. The meeting started at {time} and lasted {minutes} min. "
    "Calendar of the next days: {calendar}.\n"
    "{who}\n"
    "Return ONLY a JSON object, no prose, with exactly these keys:\n"
    '{{"title": "short title", "summary": "5 to 10 sentences", "decisions": ["…"], '
    '"actions": [{{"who": "moi | a name | ?", "task": "…", "due": "YYYY-MM-DD HH:MM | YYYY-MM-DD | null"}}], '
    '"questions": ["open questions left unanswered"]}}\n'
    "« who » is « moi » when the user committed to it or it was asked of them. « due » is an absolute date "
    "computed with the calendar when a deadline was said (« vendredi », « demain 15 h »), else null. A weekday "
    "name means its next occurrence that is still in the future (said on a Friday at 11:00, « avant vendredi "
    "10 h » is next week's Friday). Never give a due date already in the past.\n\n{body}")

PARTIAL = ("This is part {i} of {n} of a long meeting transcript. Write dense notes (bullet points) keeping every "
           "decision, commitment with its owner and deadline, figure and open question. No JSON.\n\n{body}")


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Coupe aux fins de ligne, jamais au milieu d'une réplique. Pur."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        cur += line
    return out + ([cur] if cur.strip() else [])


def parse(raw: str) -> dict:
    """Le JSON du modèle, nettoyé ; à défaut, sa réponse devient le résumé. Pur."""
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    data = None
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return {"title": "Réunion", "summary": (raw or "").strip()[:4000], "decisions": [], "actions": [],
                "questions": [], "parsed": False}
    actions = []
    for a in data.get("actions") or []:
        if isinstance(a, dict) and str(a.get("task") or "").strip():
            due = str(a.get("due") or "").strip()
            actions.append({"who": str(a.get("who") or "?").strip() or "?", "task": str(a["task"]).strip(),
                            "due": due if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", due) else None})
    strs = lambda k: [str(x).strip() for x in data.get(k) or [] if str(x).strip()]   # noqa: E731
    return {"title": str(data.get("title") or "Réunion").strip()[:120], "summary": str(data.get("summary") or "").strip(),
            "decisions": strs("decisions"), "actions": actions, "questions": strs("questions"), "parsed": True}


def calendar(start: datetime, days: int = 8) -> str:
    """« vendredi 2026-10-16 (today), samedi 2026-10-17, … » : les modèles comptent mal les jours. Pur."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        out.append(f"{DAYS[d.weekday()]} {d:%Y-%m-%d}" + (" (today)" if i == 0 else ""))
    return ", ".join(out)


def summarize(transcript: str, brain, started: datetime, duration_s: float,
              names: tuple[str, ...] = (), now: datetime | None = None) -> dict:
    if not transcript.strip():
        return {"title": "Réunion", "summary": "Rien n'a été transcrit.", "decisions": [], "actions": [],
                "questions": [], "parsed": True, "model": ""}
    parts = chunks(transcript)
    body = transcript
    if len(parts) > 1:
        notes = [brain.complete(SYSTEM, PARTIAL.format(i=i, n=len(parts), body=p), max_tokens=1500)
                 for i, p in enumerate(parts, 1)]
        body = "\n\n".join(f"Notes, part {i}:\n{n}" for i, n in enumerate(notes, 1))
    now = now or datetime.now()
    real = [n for n in names if n and n.lower() != "moi"]
    who = (f"The user (« Moi ») is called {real[0]}: anything asked of « {real[0]} » is the user's (« moi »)."
           if real else "")
    prompt = FINAL.format(weekday=DAYS[now.weekday()], date=now.strftime("%Y-%m-%d"), now=now.strftime("%H:%M"),
                          time=started.strftime("%H:%M"), minutes=max(1, round(duration_s / 60)),
                          calendar=calendar(now), who=who, body=body)
    result = parse(brain.complete(SYSTEM, prompt, max_tokens=2500))
    result["model"] = getattr(brain, "used", "")
    return result


def mine(result: dict, names: tuple[str, ...] = ("moi",)) -> list[dict]:
    """Les actions de l'utilisateur : « moi », ou son nom."""
    wanted = {n.strip().lower() for n in names if n}
    return [a for a in result.get("actions") or [] if a["who"].strip().lower() in wanted]


def to_markdown(result: dict, transcript: str, started: datetime, duration_s: float, sources: str = "") -> str:
    lines = [f"# {result.get('title') or 'Réunion'}", "",
             f"{started:%Y-%m-%d %H:%M} · {max(1, round(duration_s / 60))} min"
             + (f" · {sources}" if sources else "") + (f" · résumé : {result['model']}" if result.get("model") else ""),
             "", "## Résumé", "", result.get("summary") or "—", ""]
    if result.get("decisions"):
        lines += ["## Décisions", ""] + [f"- {d}" for d in result["decisions"]] + [""]
    if result.get("actions"):
        lines += ["## Actions", ""] + [f"- [ ] **{a['who']}** — {a['task']}" + (f" (pour le {a['due']})" if a["due"] else "")
                                      for a in result["actions"]] + [""]
    if result.get("questions"):
        lines += ["## Questions ouvertes", ""] + [f"- {q}" for q in result["questions"]] + [""]
    lines += ["## Transcription", "", "```text", transcript.rstrip(), "```", ""]
    return "\n".join(lines)
