"""
eval_lab/real.py — the REAL tier: what only hardware can prove.

A short list, written by a person, run by a person: real Gemini Live, real
microphone, real Android phone, real desktop, real GPU, real network. The lab
never executes these; it prints the procedure and records the verdict.

THE GUARD
    scenario.validate() refuses `tier: real` unless `source` is human or
    regression, and runner.run() skips the tier whatever it is asked. A
    generated scenario — however interesting — reaches a real device only
    when a person rewrites it here. There is no command that promotes one.

VERDICTS
    Those of jarvis-preprod, on purpose: PASS, FAIL, SKIP, PHYSICAL_ONLY is
    not needed here (everything here is physical). A run is a dated file under
    runs/real-<date>/, never an edit of the corpus.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from .scenario import dumps, make

CORPUS = Path(__file__).resolve().parent / "corpus" / "real" / "real.jsonl"
VERDICTS = ("PASS", "FAIL", "SKIP")


def seed_suite() -> list[dict]:
    """The initial suite. Each points at the FAST/FULL finding it confirms."""
    def R(surface, family, notes, **kw):
        return make(surface, source="human", tier="real", family=f"real.{family}",
                    oracle="explicit", notes=notes, **kw)
    return [
        R("sequence", "deferred_checkin",
          "Confirme REG-0001 sur le vrai serveur. 1) Redemarrer le serveur headless, telephone "
          "deconnecte. 2) Attendre un check-in proactif (log « Check-in differe »). 3) Connecter le "
          "telephone. 4) Au passage suivant du moniteur, verifier : le check-in est-il dit, notifie, "
          "ou perdu ? Attendu : pas perdu.",
          events=[{"op": "decide", "priority": "IMPORTANT", "payload": "proactive", "push_if_deferred": True},
                  {"op": "world", "phone": {"age_s": 0, "screen_on": True}},
                  {"op": "alerts", "alerts": ["[MONITOR_ALERT] a\nHeadline: b"]}],
          expected={"steps.2.lost": {"$len": 0}}),
        R("policy", "bluetooth_headset_name",
          "Candidat « nom de casque = voiture ». Telephone Android reel, Ne pas deranger actif, "
          "casque Bluetooth dont le nom contient « sync », « car » ou « auto ». Declencher une alerte "
          "IMPORTANT. Attendu : notification muette, JARVIS ne parle pas dans le casque.",
          world={"phone": {"age_s": 0, "screen_on": True, "dnd": True, "headset": True,
                           "bluetooth_devices": ["Bose SyncBuds"]}},
          stimulus={"priority": "IMPORTANT"}, expected={"speaks": False}),
        R("router", "phone_routing_live",
          "Gemini Live reel, a la voix, depuis le telephone : « ouvre YouTube sur mon PC » avec le PC "
          "eteint. Attendu : JARVIS dit que le PC n'est pas connecte, rien ne s'ouvre sur le telephone. "
          "Noter la phrase exacte (majuscule de « PC »).",
          world={"devices": [{"id": "desktop-01", "type": "pc", "caps": ["open_app"], "online": False},
                             {"id": "phone-01", "type": "android", "caps": ["open_app"]}],
                 "turn": {"origin": "phone-01", "text": "ouvre YouTube sur mon PC", "ago_s": 0}},
          stimulus={"tool": "open_app", "parameters": {"app": "youtube"}},
          expected={"executed_on": []}),
        R("face", "lipsync_gpu",
          "Desktop, vrai GPU, vrai audio : faire parler JARVIS 30 s en mode visage. Attendu : bouche "
          "synchrone a l'oreille (< 100 ms percu), aucun mouvement sous la nuque, aucune erreur JS.",
          stimulus={"intent": "explain", "state": "SPEAKING", "speech": True, "gaze": "user",
                    "urgent": False, "interrupted": False},
          expected={"violations": {"$len": 0}}),
    ]


def write_seed() -> int:
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    items = seed_suite()
    CORPUS.write_text("".join(dumps(s) + "\n" for s in items), encoding="utf-8", newline="\n")
    return len(items)


def load() -> list[dict]:
    if not CORPUS.exists():
        return []
    return [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]


def record(runs_dir: Path, scenario_id: str, verdict: str, note: str) -> Path:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict : {' | '.join(VERDICTS)}")
    if scenario_id not in {s["id"] for s in load()}:
        raise ValueError(f"{scenario_id} n'est pas dans la suite REAL")
    out = runs_dir / f"real-{_dt.date.today().isoformat()}" / "verdicts.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": scenario_id, "verdict": verdict, "note": note,
                             "at": _dt.datetime.now().isoformat(timespec="seconds")},
                            ensure_ascii=False) + "\n")
    return out
