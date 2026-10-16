"""
meeting/watcher.py — quand la réunion commence, quand elle finit. Pur, testable.

    w = Watcher(mode="auto")
    action = w.step(now, in_use=True, recording=False, silent_for=0.0)   # "start" | "ask" | "stop" | None

DÉBUT  micro ou caméra pris par une autre application pendant START_AFTER_S
       (15 s : une dictée vocale ou un message vocal ne déclenche rien).
       mode « auto » : on enregistre et on le dit ; « ask » : on le propose une
       fois par usage ; « off » : rien.
FIN    plus ni micro ni caméra pendant STOP_AFTER_S (45 s : une coupure de
       réseau ou un changement de périphérique ne coupe pas la réunion) ; ou
       aucune parole transcrite depuis SILENCE_END_S (10 min) ; ou MAX_S (4 h).
MANUEL « je suis en réunion » (une réunion en présentiel n'a ni visio ni
       caméra) : seuls la demande d'arrêt, le silence et MAX_S l'arrêtent.
macOS  ne dit pas QUI utilise le micro, et notre propre capture l'occupe :
       pendant l'enregistrement, seul compte la caméra (own_mic_masks=True).
"""
from __future__ import annotations

START_AFTER_S = 15.0
STOP_AFTER_S = 45.0
SILENCE_END_S = 600.0
MAX_S = 4 * 3600.0


class Watcher:
    def __init__(self, mode: str = "auto", start_after: float = START_AFTER_S, stop_after: float = STOP_AFTER_S,
                 silence_end: float = SILENCE_END_S, max_s: float = MAX_S):
        self.mode = mode if mode in ("auto", "ask", "off") else "auto"
        self.start_after, self.stop_after = start_after, stop_after
        self.silence_end, self.max_s = silence_end, max_s
        self.manual = False
        self._seen_since: float | None = None
        self._gone_since: float | None = None
        self._asked = False
        self._started_at: float | None = None

    def begin(self, now: float, manual: bool) -> None:
        self.manual, self._started_at, self._gone_since = manual, now, None

    def end(self) -> None:
        self.manual, self._started_at, self._seen_since, self._gone_since = False, None, None, None

    def step(self, now: float, in_use: bool, recording: bool, silent_for: float = 0.0) -> str | None:
        if not recording:
            if not in_use:
                self._seen_since, self._asked = None, False
                return None
            self._seen_since = self._seen_since if self._seen_since is not None else now
            if now - self._seen_since < self.start_after or self.mode == "off":
                return None
            if self.mode == "auto":
                self.begin(now, manual=False)
                return "start"
            if not self._asked:
                self._asked = True
                return "ask"
            return None
        started = self._started_at if self._started_at is not None else now
        if now - started >= self.max_s or silent_for >= self.silence_end:
            return "stop"
        if self.manual:
            return None
        if in_use:
            self._gone_since = None
            return None
        self._gone_since = self._gone_since if self._gone_since is not None else now
        return "stop" if now - self._gone_since >= self.stop_after else None
