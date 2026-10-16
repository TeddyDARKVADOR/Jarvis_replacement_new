"""
meeting/live.py — pendant la réunion, une réponse discrète quand on te pose une question.

    a = Assistant(answer=…, show=…, names=("moi", "Teddy"))
    a.on_line(line)        à chaque réplique transcrite (transcribe.Transcriber.on_line)
    a.help()               « aide-moi » (raccourci) : que dire, d'après la dernière minute

DEUX TEMPS
  1. Une règle immédiate, sans modèle : une réplique des participants qui
     ressemble à une question (« ? », mot interrogatif) ET qui te vise (ton
     prénom, « tu », « vous ») est candidate. « Est-ce qu'on fait la version
     mobile ? » ne l'est pas ; « Teddy, tu as le budget ? » l'est.
  2. Un modèle rapide (answer) juge, avec les 3 dernières minutes : est-ce bien
     pour toi, et que pourrais-tu répondre — en une ou deux lignes, avec ce qui
     s'est dit dans la réunion, sans rien inventer.
La réponse s'affiche en petit (overlay.py), puis s'efface.

Un seul travail à la fois, et la question la plus récente l'emporte : une
réponse qui arrive après la suivante ne sert plus à rien. La même question
n'est pas traitée deux fois en 30 s.

Mesuré sur un i7-1255U sans GPU : Whisper base transcrit une question de 5 s en
2,5 s ; avec la fin de phrase (1 s) et le modèle (~0,5 s), la suggestion
arrive ~4 s après la question.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Callable

from meeting.transcribe import Line

CONTEXT_S = 180.0
HELP_S = 60.0
REPEAT_S = 30.0

QUESTION = re.compile(
    r"\?|^\s*(?:\w+\s*,\s*)?(est[- ]ce|qu['’ ]est[- ]ce|comment|pourquoi|quand|combien|quel(?:le)?s?|où|qui|quoi|"
    r"peux[- ]tu|pouvez[- ]vous|as[- ]tu|avez[- ]vous|sais[- ]tu|savez[- ]vous|tu (?:as|peux|sais|penses|crois|vois)|"
    r"vous (?:avez|pouvez|savez|pensez)|t['’]as|what|how|why|when|where|who|which|can you|could you|do you|"
    r"did you|are you|would you|have you)\b", re.IGNORECASE)
SECOND_PERSON = re.compile(r"\b(tu|toi|te|ton|ta|tes|vous|votre|vos|you|your)\b|\bt['’]", re.IGNORECASE)

SYSTEM = (
    "You discreetly help the user live during a video meeting. The transcript lines are tagged « Moi » (the "
    "user) and « Participants » (everyone else). Answer ONLY with a JSON object: "
    '{"for_user": true|false, "answer": "…"}. for_user is true only if the last question or request is addressed '
    "to the user (by name, « tu », « vous » to them) and they are expected to answer. answer: what the user "
    "could say, at most 25 words, in the language of the meeting, built from facts said in the meeting (quote "
    "figures, dates, names) or common knowledge; if the meeting does not contain the answer, suggest a short "
    "honest reply (ask for a detail, or promise to check). Never invent facts.")
HELP = (
    "The user pressed « help » during the meeting. From the last minute, say in at most 30 words what they "
    "could say or ask now, in the language of the meeting. JSON only: {\"for_user\": true, \"answer\": \"…\"}")


def candidate(text: str, names: tuple[str, ...]) -> bool:
    """Une question qui te vise ? La règle immédiate, sans modèle. Pur."""
    t = " ".join(text.split())
    if len(t) < 8 or not QUESTION.search(t):
        return False
    low = t.lower()
    named = any(n and n.lower() != "moi" and re.search(rf"\b{re.escape(n.lower())}\b", low) for n in names)
    return bool(named or SECOND_PERSON.search(t))


def parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get("answer") or "").strip():
        return None
    return {"for_user": bool(data.get("for_user")), "answer": " ".join(str(data["answer"]).split())[:300]}


class Assistant:
    def __init__(self, answer: Callable[[str, str], str], show: Callable[[str, str], None],
                 names: tuple[str, ...] = ("moi",), log: Callable[[str], None] = print, clock=time.monotonic):
        self.answer, self.show, self.names, self.log, self.clock = answer, show, names, log, clock
        self.lines: deque[Line] = deque()
        self._pending: tuple[str, str, str] | None = None   # (genre, question, contexte)
        self._busy = False
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self.shown: list[tuple[str, str]] = []

    def context(self, seconds: float = CONTEXT_S) -> str:
        if not self.lines:
            return ""
        last = self.lines[-1].t
        return "\n".join(x.render() for x in self.lines if last - x.t <= seconds)

    def on_line(self, line: Line) -> None:
        with self._lock:
            self.lines.append(line)
            while self.lines and line.t - self.lines[0].t > CONTEXT_S:
                self.lines.popleft()
        if line.speaker != "participants" or not candidate(line.text, self.names):
            return
        key = re.sub(r"\W+", " ", line.text.lower()).strip()
        now = self.clock()
        if now - self._seen.get(key, -1e9) < REPEAT_S:
            return
        self._seen[key] = now
        self._submit("question", line.text, self.context())

    def help(self) -> str:
        ctx = self.context(HELP_S)
        if not ctx:
            return "Rien n'a encore été dit."
        self._submit("help", "", ctx)
        return "Je regarde la dernière minute."

    def _submit(self, kind: str, question: str, ctx: str) -> None:
        with self._lock:
            self._pending = (kind, question, ctx)        # la plus récente remplace celle qui attendait
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._work, name="meeting-assist", daemon=True).start()

    def _work(self) -> None:
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._busy = False
                    return
            kind, question, ctx = job
            t0 = self.clock()
            try:
                if kind == "help":
                    raw = self.answer(HELP, f"Last minute of the meeting:\n{ctx}")
                else:
                    who = next((n for n in self.names if n.lower() != "moi"), "")
                    raw = self.answer(SYSTEM, (f"The user is called {who}.\n" if who else "")
                                      + f"Recent transcript:\n{ctx}\n\nLast line, to judge: « {question} »")
                res = parse(raw)
            except Exception as e:
                self.log(f"SYS: réunion — aide en direct indisponible : {type(e).__name__}: {e}"[:200])
                continue
            if res and (res["for_user"] or kind == "help"):
                title = "Que dire" if kind == "help" else f"« {question[:90]} »"
                self.shown.append((title, res["answer"]))
                self.log(f"SYS: réunion — suggestion ({self.clock() - t0:.1f} s) : {res['answer'][:120]}")
                try:
                    self.show(title, res["answer"])
                except Exception:
                    pass
