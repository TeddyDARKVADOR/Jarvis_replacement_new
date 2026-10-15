"""
offline/selftest.py — le relais hors-ligne s'allume quand il faut, se tait au
retour, et sa boucle d'outils tient.

    python -m offline.selftest           # sans réseau, sans modèle
    python -m offline.selftest --real    # + voix système → Whisper → Ollama, en vrai

Le mode --real télécharge le modèle Whisper la première fois et demande Ollama.
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import offline as O
from offline import audio, llm, tools

REAL = "--real" in sys.argv
_SHARED: dict = {}
_results: list[tuple[str, bool, str]] = []


def check(name: str, real: bool = False):
    def deco(fn):
        if real and not REAL:
            return fn
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


VOICE, SILENCE = b"\x01" * audio.FRAME_BYTES, b"\x00" * audio.FRAME_BYTES
_is_voice = lambda f: f[0] == 1      # noqa: E731


@check("1 découpage : une phrase entière, précédée de son attaque")
def _segment():
    seg = audio.Segmenter(_is_voice)
    outs = [seg.feed(f) for f in [SILENCE] * 5 + [VOICE] * 30 + [SILENCE] * 30]
    pcm = [o for o in outs if o]
    assert len(pcm) == 1, len(pcm)
    frames = len(pcm[0]) // audio.FRAME_BYTES
    assert 30 <= frames <= 30 + 10 + 27, frames


@check("2 découpage : un claquement trop court est ignoré, 15 s maximum")
def _short_long():
    seg = audio.Segmenter(_is_voice)
    assert not any(seg.feed(f) for f in [VOICE] * 3 + [SILENCE] * 40)
    seg = audio.Segmenter(_is_voice)
    outs = [o for o in (seg.feed(VOICE) for _ in range(1000)) if o]
    assert outs and len(outs[0]) // audio.FRAME_BYTES == int(audio.MAX_UTTERANCE_S * 1000 / audio.FRAME_MS)


@check("3 nom : n'importe où dans la phrase, accents et casse ignorés")
def _addressed():
    names = ["jarvis", "Édith"]
    assert O.addressed("Dis-moi Jarvis, quelle heure ?", names)
    assert O.addressed("edith ouvre spotify", names)
    assert not O.addressed("le journal de vingt heures", names)
    assert not O.addressed("jarvisien", names)
    assert O.addressed("Jarvie, ouvre le bloc note.", names), "prononciation française"
    assert not O.addressed("jardin", names) and not O.addressed("service", names)
    assert O._STAMP.sub(" ", "Il est 22h59. [mardi 13/10/2026 22:59]").strip() == "Il est 22h59."


class _FakeVoice:
    def __init__(self):
        self.running = False
        self.starts = 0

    def start(self):
        self.running, self.starts = True, self.starts + 1

    def stop(self):
        self.running = False


@check("4 superviseur : une coupure brève ne le réveille pas")
def _brief():
    v = _FakeVoice()
    s = O.Supervisor(lambda: v, lambda *_: None, down_after=0.3)
    s.on_down()
    time.sleep(0.1)
    s.on_up()
    time.sleep(0.4)
    assert v.starts == 0 and not s.active


@check("5 superviseur : panne durable → relais ; retour → silence ; échecs répétés : un seul départ")
def _long_outage():
    v = _FakeVoice()
    s = O.Supervisor(lambda: v, lambda *_: None, down_after=0.2)
    for _ in range(5):
        s.on_down("timed out")
    time.sleep(0.4)
    assert v.starts == 1 and s.active
    s.on_down()
    assert v.starts == 1
    s.on_up()
    assert not v.running


@check("6 schéma Gemini → JSON Schema")
def _schema():
    g = {"type": "OBJECT", "properties": {"x": {"type": "STRING", "enum": ["A"]},
                                          "l": {"type": "ARRAY", "items": {"type": "INTEGER"}}}}
    j = llm.to_json_schema(g)
    assert j["type"] == "object" and j["properties"]["x"] == {"type": "string", "enum": ["A"]}
    assert j["properties"]["l"]["items"]["type"] == "integer"


@check("7 boucle d'outils : appel, résultat rendu au modèle, réponse finale sans <think>")
def _brain():
    replies = [
        {"message": {"content": "", "tool_calls": [{"function": {"name": "open_app", "arguments": {"app_name": "spotify"}}}]}},
        {"message": {"content": "<think>ok</think>Spotify est ouvert."}},
    ]
    sent, ran = [], []

    def post(url, payload):
        sent.append(copy.deepcopy(payload))
        return replies.pop(0)

    b = llm.Brain("m", ["tools", "thinking"], "sys",
                  {"open_app": ({"description": "d", "parameters": {"type": "OBJECT", "properties": {}}},
                                lambda a: ran.append(a) or "Opened spotify")}, post=post)
    assert b.ask("ouvre spotify") == "Spotify est ouvert."
    assert ran == [{"app_name": "spotify"}]
    assert sent[0]["think"] is False and sent[0]["tools"][0]["function"]["name"] == "open_app"
    assert sent[1]["messages"][-1] == {"role": "tool", "content": "Opened spotify", "tool_name": "open_app"}


@check("8 boucle d'outils : outil inconnu et outil qui plante ne cassent rien")
def _brain_errors():
    replies = [{"message": {"tool_calls": [{"function": {"name": "nope", "arguments": "{}"}},
                                           {"function": {"name": "boom", "arguments": {}}}]}},
               {"message": {"content": "Désolé."}}]
    b = llm.Brain("m", ["tools"], "s", {"boom": ({}, lambda a: 1 / 0)}, post=lambda u, p: replies.pop(0))
    assert b.ask("x") == "Désolé."
    contents = [m["content"] for m in b.history if m["role"] == "tool"]
    assert "Outil inconnu" in contents[0] and "a échoué" in contents[1]


@check("9 outils hors-ligne : seulement la liste autorisée, rien qui demande Internet")
def _tools():
    t = tools.build(lambda *_: None)
    assert "current_time" in t and set(t) - {"current_time"} <= set(tools.ALLOWED), set(t)
    assert "open_app" in t and "web_search" not in t
    assert t["current_time"][1]({})


@check("10 hooks : main.py et client_desktop branchés, sans effet hors mode local")
def _hooks():
    repo = Path(__file__).resolve().parent.parent
    main = (repo / "main.py").read_text(encoding="utf-8")
    assert "offline.attach(self)" in main and "_offline.on_live_down" in main and "_offline.on_live_up" in main
    app = (repo / "client_desktop" / "app.py").read_text(encoding="utf-8")
    assert "offline.Supervisor" in app

    class J:
        class ui:
            local_mode = False
    j = J()
    O.attach(j)
    assert not hasattr(j, "_offline")


@check("R1 voix système : une phrase synthétisée", real=True)
def _tts():
    from offline import tts
    pcm, rate = tts.shared("fr").synthesize("Jarvis, ouvre le bloc-notes.")
    assert len(pcm) > rate, len(pcm)
    _SHARED["tts"] = (pcm, rate)
    return f"{len(pcm) / 2 / rate:.1f} s à {rate} Hz"


@check("R2 Whisper : la phrase synthétisée revient en texte", real=True)
def _stt():
    from offline import stt
    pcm, rate = _SHARED["tts"]
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / rate))
    pcm16 = np.interp(idx, np.arange(len(a)), a).astype(np.int16).tobytes()
    w = stt.shared("fr", log=lambda *_: None, hotwords=["jarvis"])
    assert w.transcribe(b"\x00" * 16000) == "", "hallucination sur du silence"
    t = time.monotonic()
    text = w.transcribe(pcm16)
    assert O.addressed(text, ["jarvis"]) and "bloc" in text.lower(), text
    return f"« {text} » ({w.device}, {time.monotonic() - t:.1f} s)"


@check("R3 Ollama : un modèle capable d'outils répond et appelle current_time", real=True)
def _ollama():
    picked = llm.pick_model()
    assert picked, "Ollama absent ou sans modèle capable d'outils"
    called = []
    t = time.monotonic()
    b = llm.Brain(*picked, O.SYSTEM.format(name="JARVIS"),
                  {"current_time": tools.build(lambda *_: None)["current_time"]})
    reply = b.ask("Jarvis, quelle heure est-il ?", on_tool=lambda n, a: called.append(n))
    assert reply and "current_time" in called, (reply, called)
    first = time.monotonic() - t
    t = time.monotonic()
    again = b.ask("Et quel jour sommes-nous ?")
    assert again, "pas de réponse au second tour"
    return f"{picked[0]} : « {reply} » ({first:.1f} s à froid, {time.monotonic() - t:.1f} s ensuite)"


@check("R4 bout en bout : OfflineVoice entend « Jarvis, quelle heure est-il ? » et répond à voix haute", real=True)
def _end_to_end():
    import threading as th

    from offline import tts
    pcm, rate = tts.shared("fr").synthesize("Jarvis, quelle heure est-il ?")
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / rate))
    utterance = np.interp(idx, np.arange(len(a)), a).astype(np.int16).tobytes()
    log, played, done = [], [], th.Event()

    class FakeMic:
        def __init__(self, device=None):
            self.queue = [b"\x00" * 3200, utterance]       # du bruit sans nom, puis la question
            self.muted = th.Event()

        def start(self):
            pass

        def next_utterance(self, stop):
            if self.queue:
                return self.queue.pop(0)
            done.wait(60)
            stop.wait(0.1)
            return None

        def flush(self):
            pass

        def close(self):
            pass

    real_mic, real_play = audio.Microphone, audio.play
    audio.Microphone = FakeMic
    audio.play = lambda pcm, rate, device=None, mic=None: played.append(len(pcm))
    v = O.OfflineVoice(lambda line: (log.append(line), line.startswith("JARVIS") and len(played) >= 1
                                     and any(l.startswith("VOUS") for l in log) and done.set()),
                       language="fr", names=["jarvis"])
    try:
        v.start()
        assert done.wait(240), "pas de réponse : " + " | ".join(log[-6:])
    finally:
        v.stop()
        audio.Microphone, audio.play = real_mic, real_play
    heard = next(l for l in log if l.startswith("VOUS"))
    answer = [l for l in log if l.startswith("JARVIS")][-1]
    import re
    from datetime import datetime
    m = re.search(r"(\d{1,2})\s*(?:h|heures?|:)\s*(\d{2})?", answer)
    assert m, answer
    said = int(m.group(1)) * 60 + int(m.group(2) or 0)
    now = datetime.now().hour * 60 + datetime.now().minute
    assert min(abs(now - said), 1440 - abs(now - said)) <= 2, (answer, now)
    return f"{heard} → {answer}"


def main() -> int:
    ok = sum(1 for _, passed, _ in _results if passed)
    for name, passed, info in _results:
        print(f"  {'OK ' if passed else 'ÉCHEC'} {name}" + (f" — {info}" if info else ""))
    print(f"offline : {ok}/{len(_results)}")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
