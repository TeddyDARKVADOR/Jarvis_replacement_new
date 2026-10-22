"""
matthew/session.py — une grosse session de données, sur n'importe quel PC (sans GPU).

L'entraînement de Matthew demande la carte NVIDIA du PC d'entraînement ; ce qui le limite
pourtant, c'est la donnée : le deuxième champion avait appris sur 198 exemples vérifiés,
certains outils sur 1 ou 2, et ses arguments plafonnaient à 0,795. Cette session fabrique
de la donnée vérifiée en quantité, avec des professeurs ouverts servis par Ollama (cloud
compris), puis l'emballe (matthew/pack.py) pour le PC d'entraînement.

    1. conversation et refus     jusqu'à --chat / --none exemples vérifiés chacun
    2. chaque outil              jusqu'à --per-tool exemples, en DOUBLE ANNOTATION : le
                                 générateur écrit l'appel attendu, le vérificateur (autre
                                 famille de modèle) le refait sans le voir ; gardé seulement
                                 si les valeurs concordent (bootstrap.args_agree)
    3. ronde difficile           --hard demandes indirectes, bruitées, proches d'autres outils
Ordre : les outils les plus faibles d'abord (précision et nombre d'exemples publiés par la
vitrine). Quota (429) : pause de 15 min puis reprise ; arrêt à --hours. État :
memory/matthew/session.json (lisible pendant la session).

    python -m matthew.session [--per-tool 24] [--hard 8] [--chat 80] [--none 80] [--hours 10]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matthew import config, data_dir
from matthew.bootstrap import HARD_HINT, Bootstrap
from matthew.journal import Journal
from matthew.router import default as default_router

#: Professeurs par défaut : deux familles différentes, licences ouvertes, servis par Ollama
#: (offre cloud gratuite ; ni Gemini ni Claude). gpt-oss : Apache-2.0 ; Gemma 4 : Apache-2.0.
TEACHERS = {"base_url": "http://127.0.0.1:11434/v1", "model": "gpt-oss:120b-cloud", "license": "apache-2.0",
            "verifier_model": "gemma4:31b-cloud", "verifier_license": "apache-2.0"}
VITRINE_DATA = "https://teddydarkvador.github.io/matthew-vitrine/data/vitrine.json"


def keep_awake(on: bool) -> None:
    """Windows : pas de mise en veille pendant la session (l'écran peut s'éteindre, le verrou
    Windows+L ne gêne pas). Rendu au système à la fin. Ailleurs : rien."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def priority(names: list[str], fetch=None) -> list[str]:
    """Les outils les plus faibles d'abord : précision publiée, puis nombre d'exemples."""
    try:
        if fetch is None:
            import requests
            fetch = lambda: requests.get(VITRINE_DATA, timeout=10).json()
        from matthew.vitrine import pub
        pub_of = {pub(n): n for n in names}
        rank = {pub_of[t["id"]]: ((t["accuracy"] if t["accuracy"] is not None else 0.0), t["examples"])
                for t in fetch()["tools"] if t["id"] in pub_of}
    except Exception:
        rank = {}
    return sorted(names, key=lambda n: rank.get(n, (0.0, 0)))


class Session:
    def __init__(self, cfg: dict | None = None, journal: Journal | None = None, clock=time.time,
                 sleep=time.sleep, bootstrap: Bootstrap | None = None, fetch=None):
        self.cfg = dict(cfg or config())
        self.cfg["teacher"] = {**TEACHERS, **(self.cfg.get("session_teachers") or {})}
        self.cfg["teacher_fallback"] = None        # pas de professeur local ici : on attend le quota
        self.journal = journal or Journal()
        self.boot = bootstrap or Bootstrap(self.journal, cfg=self.cfg, max_calls=10 ** 6)
        self._clock, self._sleep, self._fetch = clock, sleep, fetch
        self.state = {"started": datetime.fromtimestamp(clock()).isoformat(timespec="seconds"),
                      "teacher": self.cfg["teacher"]["model"], "verifier": self.cfg["teacher"]["verifier_model"],
                      "phase": "", "ok": 0, "rejected": 0, "reasons": {}, "per_target": {}}

    @property
    def path(self) -> Path:
        return self.journal.root / "session.json"

    def _save(self, **kw) -> None:
        self.state.update(kw, at=datetime.fromtimestamp(self._clock()).isoformat(timespec="seconds"))
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def counts(self, round_: str | None = None) -> dict:
        out: dict[str, int] = {}
        for r in self.boot.records():
            if r.get("status") == "ok" and (round_ is None or r.get("round") == round_):
                out[r["target"]] = out.get(r["target"], 0) + 1
        return out

    def _step(self, fn, deadline: float) -> bool:
        """Un appel au professeur ; False quand l'heure est passée. Quota : on attend."""
        while self._clock() < deadline:
            stats = {"ok": 0, "rejected": 0, "reasons": {}}
            try:
                fn(stats)
                return True
            except StopIteration as e:
                self._save(phase=f"pause quota : {str(e)[:120]}")
                self._sleep(900)
                self.boot.calls = 0
            finally:
                self.state["ok"] += stats["ok"]
                self.state["rejected"] += stats["rejected"]
                for k, v in stats["reasons"].items():
                    self.state["reasons"][k] = self.state["reasons"].get(k, 0) + v
        return False

    def run(self, per_tool: int = 24, hard: int = 8, chat: int = 80, none: int = 80, hours: float = 10.0) -> dict:
        deadline = self._clock() + hours * 3600
        decls = self.journal.tools()
        if not decls:
            return {"error": "aucune déclaration d'outils (tools.json) : lancer Matthew une fois"}
        router = default_router(decls)
        by = {d["name"]: d for d in decls}
        order = priority(list(by), self._fetch)
        self._save(phase="conversation et refus", order=order)
        for kind, goal in (("__chat__", chat), ("__none__", none)):
            for _ in range(8):
                have = self.counts().get(kind, 0)
                if have >= goal or not self._step(
                        lambda st, k=kind, n=min(20, goal - have + 4): self.boot._no_tool(k, decls, router, n, st), deadline):
                    break
        day = datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")
        for phase, goal, extra, round_ in (("outils", per_tool, "", None),
                                           ("ronde difficile", hard, HARD_HINT, f"dur-{day}")):
            for i, name in enumerate(order):
                for _ in range(4):                       # au plus 4 générations par outil et par phase
                    have = self.counts(round_).get(name, 0)
                    if have >= goal:
                        break
                    if not self._step(lambda st, d=by[name], n=min(12, goal - have + 3):
                                      self.boot._tool(d, decls, router, n, st, extra=extra, round_=round_, agree=True),
                                      deadline):
                        return self._finish("heure limite")
                self._save(phase=f"{phase} {i + 1}/{len(order)} : {name}", per_target=self.counts())
        return self._finish("terminée")

    def _finish(self, why: str) -> dict:
        self._save(phase=why, per_target=self.counts(), done=True)
        return self.state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="matthew.session")
    ap.add_argument("--per-tool", type=int, default=24)
    ap.add_argument("--hard", type=int, default=8)
    ap.add_argument("--chat", type=int, default=80)
    ap.add_argument("--none", type=int, default=80)
    ap.add_argument("--hours", type=float, default=10.0)
    a = ap.parse_args(argv)
    keep_awake(True)
    try:
        out = Session().run(a.per_tool, a.hard, a.chat, a.none, a.hours)
    finally:
        keep_awake(False)
    print(json.dumps({k: out.get(k) for k in ("phase", "ok", "rejected", "reasons")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
