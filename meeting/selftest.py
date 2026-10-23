"""
meeting/selftest.py — le copilote de réunion, sans réunion.

    python -m meeting.selftest

Faux micro, faux Whisper, faux modèle, fausse horloge : tout se passe en
mémoire et dans un dossier temporaire. Les deux derniers contrôles touchent le
vrai poste (détection, périphériques audio) et se déclarent « sans objet » là
où il n'y a rien à mesurer (CI, conteneur).
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import meeting as M
from meeting import brain, capture, detect, summarize, transcribe, watcher

ROOT = Path(__file__).resolve().parent

# Le registre commun des quotas (quota/) dans un dossier jetable : les faux 429
# du test ne doivent jamais toucher memory/quota.json.
try:
    import quota as _Q
    _Q.reset_for_tests(Path(tempfile.mkdtemp(prefix="matthew-assistant-quota-")) / "quota.json")
except Exception:
    _Q = None
_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        try:
            _results.append((name, True, str(fn() or "")))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


@check("1 contrat V2 : meeting/ n'importe rien de Matthew au chargement")
def _contract():
    banned = ("main", "core", "actions", "plugins", "cognition", "server", "dashboard", "client_desktop",
              "offline", "nightshift", "memory", "context")
    bad = []
    for f in sorted(ROOT.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:                   # le niveau module seulement : les imports paresseux sont gardés
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            bad += [f"{f.name}: {n}" for n in names if n.split(".")[0] in banned]
    assert not bad, bad
    return f"{len(list(ROOT.glob('*.py')))} fichiers ; memory.config_manager lu seulement dans attach()"


@check("2 détection, Windows : sessions WASAPI, Media Foundation, registre — nous exclus")
def _detect_windows():
    sessions = [(0, "System Idle Process", 0), (4242, "Teams.exe", 1), (77, "chrome.exe", 2), (99, "python.exe", 1)]
    assert detect.capture_sessions_active(sessions, own_pid=99) == ["Teams.exe"], "état actif, ni PID 0 ni nous"
    report = {"Integrated Webcam": [(26748, True), (19768, False)], "USB": [(99, True)]}
    assert detect.camera_streaming(report, own_pid=99) == [26748], "en streaming seulement, hors nous"
    own = {r"c:\python314\python.exe"}
    entries = [("C:#Program Files#Zoom#bin#Zoom.exe", 5, 0), ("MSTeams_8wekyb3d8bbwe", 5, 9),
               ("C:#Python314#python.exe", 5, 0), ("Microsoft.WindowsCamera_8wekyb3d8bbwe", 5, 0)]
    assert detect.windows_active(entries, own) == ["Zoom.exe", "Microsoft.WindowsCamera"], "fin = 0 : en cours"
    return "WASAPI, rapport MF et registre : 3 sources, notre processus jamais compté"


@check("3 détection, Linux : flux PulseAudio/PipeWire (moniteurs et pauses exclus), /dev/video*")
def _detect_linux():
    sources = [{"index": 1, "name": "alsa_input.pci.analog-stereo"},
               {"index": 2, "name": "alsa_output.pci.analog-stereo.monitor"}]
    outputs = [{"source": 1, "corked": False, "properties": {"application.name": "Zoom", "application.process.id": "500"}},
               {"source": 2, "corked": False, "properties": {"application.name": "OBS", "application.process.id": "501"}},
               {"source": 1, "corked": True, "properties": {"application.name": "Firefox", "application.process.id": "502"}},
               {"source": 1, "corked": False, "properties": {"application.name": "Matthew", "application.process.id": "7"}}]
    assert detect.pulse_recorders(outputs, sources, own_pid=7) == ["Zoom"]
    text = ('Source Output #12\n\tDriver: protocol-native.c\n\tSource: 1\n\tCorked: no\n\tProperties:\n'
            '\t\tapplication.name = "Google Chrome"\n\t\tapplication.process.id = "900"\n'
            'Source Output #13\n\tSource: 1\n\tCorked: yes\n\tProperties:\n\t\tapplication.name = "Discord"\n')
    parsed = detect.parse_pactl_text(text)
    assert detect.pulse_recorders(parsed, sources, own_pid=7) == ["Google Chrome"], parsed
    with tempfile.TemporaryDirectory() as tmp:
        proc = Path(tmp)
        for pid, comm, target in (("300", "zoom", "/dev/video0"), ("301", "bash", "/dev/pts/1"), ("7", "matthew_assistant", "/dev/video0")):
            (proc / pid / "fd").mkdir(parents=True)
            (proc / pid / "comm").write_text(comm + "\n")
            try:
                (proc / pid / "fd" / "3").symlink_to(target)
            except OSError:
                return "flux PulseAudio et repli texte vérifiés ; /proc sans objet ici (liens symboliques interdits)"
        assert detect.linux_cameras(proc, own_pid=7) == ["zoom"]
    return "flux actifs sur un vrai micro seulement ; repli texte ; caméra par /proc/<pid>/fd"


@check("4 veilleur : 15 s pour commencer, 45 s pour finir, demande, manuel, silence, 4 h")
def _watcher():
    w = watcher.Watcher("auto")
    assert w.step(0, True, False) is None and w.step(10, True, False) is None, "pas avant 15 s"
    assert w.step(16, True, False) == "start"
    assert w.step(30, False, True) is None and w.step(50, True, True) is None, "une coupure de 20 s ne finit rien"
    assert w.step(60, False, True) is None and w.step(106, False, True) == "stop", "45 s sans micro ni caméra"
    w = watcher.Watcher("auto")
    w.step(0, True, False)
    assert w.step(5, False, False) is None and w.step(19, True, False) is None, "une dictée de 5 s ne compte pas"
    w = watcher.Watcher("ask")
    w.step(0, True, False)
    assert w.step(16, True, False) == "ask" and w.step(30, True, False) is None, "demandé une seule fois"
    assert watcher.Watcher("off").step(100, True, False) is None and (w2 := watcher.Watcher("off")).step(0, True, False) is None
    del w2
    m = watcher.Watcher("auto")
    m.begin(0, manual=True)
    assert m.step(1000, False, True) is None, "manuel : l'absence de visio ne l'arrête pas"
    assert m.step(1001, False, True, silent_for=600) == "stop", "10 min sans parole"
    m.begin(0, manual=True)
    assert m.step(4 * 3600, True, True) == "stop", "4 h au plus"
    return "début, fin, coupure tolérée, dictée ignorée, demande unique, off, manuel, silence, durée max"


@check("5 découpage : 12 à 30 s, coupé au plus calme, rien de perdu")
def _chunker():
    got = []
    ch = capture.Chunker("moi", lambda s, t0, x: got.append((s, t0, len(x))))
    loud = np.full(int(capture.RATE * capture.BLOCK_S), 0.3, dtype=np.float32)
    quiet = np.zeros_like(loud)
    t = 0.0
    for i in range(80):                       # 40 s, un silence d'1 s vers 13 s
        ch.feed(quiet if 26 <= i < 28 else loud, t)
        t += capture.BLOCK_S
    ch.flush()
    durs = [n / capture.RATE for _, _, n in got]
    assert abs(sum(durs) - 40.0) < 1e-6, f"tout le son : {durs}"
    assert 12 <= durs[0] <= 14.5, f"coupé au silence : {durs}"
    assert all(d <= capture.MAX_CHUNK_S for d in durs), durs
    assert [round(t0, 1) for _, t0, _ in got][:2] == [0.0, round(durs[0], 1)], "instants de départ suivis"
    dev = lambda i, loop, name="x": type("D", (), {"id": i, "isloopback": loop, "name": name})()   # noqa: E731
    win = [dev("{0.0.0.1}", False), dev("{0.0.0.2}", True)]
    pulse = [dev("alsa_input.mic", False), dev("alsa_output.spk.monitor", True), dev("other.monitor", True)]
    assert capture.loopback_of(dev("{0.0.0.2}", False), win).id == "{0.0.0.2}", "Windows : même id"
    assert capture.loopback_of(dev("alsa_output.spk", False), pulse).id == "alsa_output.spk.monitor", "Pulse : .monitor"
    return f"40 s → {[round(d, 1) for d in durs]} s ; loopback trouvé sous Windows et PulseAudio"


class _Seg:
    def __init__(self, start, text, nsp=0.1, logprob=-0.3):
        self.start, self.text, self.no_speech_prob, self.avg_logprob = start, text, nsp, logprob


class _Whisper:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def transcribe(self, samples, **kw):
        self.calls.append((float(np.abs(samples).max(initial=0)), kw))
        return iter(self.script.pop(0) if self.script else []), None


@check("6 transcription : niveau normalisé, hallucinations et écho retirés, ordre du temps")
def _transcribe():
    w = _Whisper([[_Seg(1.0, "Bonjour à tous"), _Seg(4.0, "Sous-titres réalisés par Amara.org")],
                  [_Seg(1.5, "bonjour à tous !"), _Seg(6.0, "Je m'occupe du devis pour vendredi")],
                  [_Seg(0.0, "…", nsp=0.9), _Seg(3.0, "C'est une idée à des frais", logprob=-1.4)]])
    tr = transcribe.Transcriber(whisper_factory=lambda name: w, log=lambda m: None)
    tr.start()
    tr.submit("participants", 0.0, np.full(16000, 0.001, dtype=np.float32))
    tr.submit("moi", 0.0, np.full(16000, 0.2, dtype=np.float32))
    tr.submit("moi", 30.0, np.zeros(16000, dtype=np.float32))
    lines = tr.finish(timeout=10)
    assert [x.render() for x in lines] == ["[00:00:01] Participants : Bonjour à tous",
                                           "[00:00:06] Moi : Je m'occupe du devis pour vendredi"], [x.render() for x in lines]
    assert abs(w.calls[0][0] - 0.5) < 1e-6, "le son du poste, faible, est remonté à 0,5"
    quiet_mic = transcribe.normalize(np.full(100, 0.01, dtype=np.float32), "moi")
    assert abs(float(quiet_mic.max()) - 0.04) < 1e-6, "le micro : gain plafonné à 4 (le souffle reste un souffle)"
    assert all(kw.get("vad_filter") for _, kw in w.calls), "Silero toujours"
    return "Amara retiré, écho retiré, silence ignoré, décodage peu sûr (logprob −1,4) écarté"


class _Brain:
    def __init__(self, answers):
        self.answers, self.prompts, self.used = list(answers), [], "fake/model"

    def complete(self, system, user, max_tokens=2000):
        self.prompts.append(user)
        return self.answers.pop(0)


@check("7 compte rendu : JSON strict, dates absolues, long découpé, réponse libre gardée")
def _summarize():
    started = datetime(2026, 10, 16, 14, 0)     # un vendredi
    good = ('```json\n{"title": "Point devis", "summary": "On a revu le devis.", "decisions": ["On garde Alpha"], '
            '"actions": [{"who": "moi", "task": "Envoyer le devis", "due": "2026-10-17 10:00"}, '
            '{"who": "Paul", "task": "Relire", "due": "vendredi"}, {"who": "moi", "task": "Prévenir Léa", "due": null}], '
            '"questions": ["Budget 2027 ?"]}\n```')
    b = _Brain([good])
    r = summarize.summarize("[00:00:01] Moi : bonjour\n", b, started, 1800, names=("moi", "Teddy"), now=started)
    assert r["parsed"] and r["title"] == "Point devis" and r["model"] == "fake/model"
    assert "vendredi 2026-10-16, 14:00" in b.prompts[0] and "30 min" in b.prompts[0], "date, heure et durée"
    assert "vendredi 2026-10-23" in b.prompts[0], "le calendrier des jours suivants est donné"
    assert "called Teddy" in b.prompts[0], "le nom de l'utilisateur est donné"
    assert [a["due"] for a in r["actions"]] == ["2026-10-17 10:00", None, None], "« vendredi » n'est pas une date"
    assert [a["task"] for a in summarize.mine(r, ("moi",))] == ["Envoyer le devis", "Prévenir Léa"]
    long = "".join(f"[00:{i // 60:02d}:{i % 60:02d}] Participants : {'blabla ' * 40}\n" for i in range(150))
    parts = summarize.chunks(long)
    b2 = _Brain([f"notes {i}" for i in range(len(parts))] + [good])
    summarize.summarize(long, b2, started, 3600)
    assert len(b2.prompts) == len(parts) + 1 and all(len(p) <= summarize.CHUNK_CHARS for p in parts)
    assert all(p.endswith("\n") for p in parts), "jamais au milieu d'une réplique"
    free = summarize.parse("Désolé, voici un résumé : on a parlé du devis.")
    assert not free["parsed"] and "devis" in free["summary"] and free["actions"] == []
    md = summarize.to_markdown(r, "[00:00:01] Moi : bonjour", started, 1800, "moi capté")
    assert all(h in md for h in ("# Point devis", "## Décisions", "## Actions", "- [ ] **moi** — Envoyer le devis",
                                 "## Questions ouvertes", "## Transcription"))
    return f"{len(parts)} morceaux puis fusion ; JSON en bloc de code lu ; réponse libre gardée"


@check("8 cerveau : Flash-Lite tour à tour, épuisés sautés, Groq, puis Ollama natif (16k, sans <think>)")
def _brain():
    key = "gsk_" + "s" * 40
    gkey = "AIza" + "g" * 35
    calls = []

    def transport(url, headers, body, timeout):
        sent = json.loads(body) if body else {}
        calls.append((url, headers.get("Authorization", ""), sent.get("model"), sent.get("options")))
        if "googleapis" in url:
            return 429, b'{"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "500"}]}]}}'
        if "groq" in url:
            return 429, b'{"error": "rate"}'
        if url.endswith("/api/tags"):
            return 200, json.dumps({"models": [{"name": "nomic-embed-text"}, {"name": "qwen2.5:14b"}, {"name": "qwen3:8b"}]}).encode()
        return 200, json.dumps({"message": {"content": "<think>je réfléchis</think>résumé local"}}).encode()

    cfg = {"gemini_api_key": gkey, "groq_api_key": key, "meeting_brain_order": ["gemini", "cloud", "groq", "local"]}
    b = brain.Brain(cfg, transport=transport)
    assert [p for p in b.providers()][:3] == [("gemini", m) for m in brain.GEMINI_MODELS]
    assert b.complete("s", "u") == "résumé local" and b.used == "local/qwen3:8b", b.used
    gem = [c for c in calls if "googleapis" in c[0]]
    assert [c[2] for c in gem] == list(brain.GEMINI_MODELS), "chaque Flash-Lite essayé (compteurs séparés)"
    chat = next(c for c in calls if c[0].endswith("/api/chat"))
    assert chat[3]["num_ctx"] == brain.LOCAL_CTX and chat[1] == "", "Ollama : contexte 16k, aucune clé"
    calls.clear()
    brain.Brain(cfg, transport=transport).complete("s", "u")
    assert not [c for c in calls if "googleapis" in c[0]], "les Flash-Lite épuisés du jour ne sont plus appelés"
    assert brain.pick_local(["llava:7b", "phi4:latest"]) == "phi4:latest"
    assert brain.pick_local(["qwen3:8b", "mistral:7b"], "mistral:7b") == "mistral:7b", "offline_model respecté"
    try:
        brain.Brain({"groq_api_key": key, "meeting_brain_order": ["groq"]},
                    transport=lambda *a: (500, f"echo {key}".encode())).complete("s", "u")
        raise AssertionError("une panne acceptée")
    except brain.BrainError as e:
        assert key not in str(e) and "HTTP 500" in str(e)
    return "3 Flash-Lite épuisés (registre) → Ollama Cloud absent → Groq 429 → qwen3:8b local, <think> retiré"


class _Rec:
    """Fausse capture : rend deux morceaux dès qu'elle démarre."""
    def __init__(self, on_chunk, log=None):
        self.on_chunk, self.status = on_chunk, {}

    def start(self):
        self.status = {"moi": "capté : faux micro", "participants": "capté : faux loopback"}
        self.on_chunk("participants", 0.0, np.full(16000, 0.3, dtype=np.float32))
        self.on_chunk("moi", 20.0, np.full(16000, 0.3, dtype=np.float32))

    def stop(self):
        pass


@check("9 bout en bout : visio détectée → notes → fin → compte rendu écrit et livré")
def _copilot():
    clock = [0.0]
    usage = {"on": False}
    events, delivered = [], []
    w = _Whisper([[_Seg(2.0, "On valide le devis ?")], [_Seg(1.0, "Oui, je l'envoie demain à 10 h")]])
    with tempfile.TemporaryDirectory() as tmp:
        cop = M.Copilot(
            settings=lambda: {"meeting_mode": "auto", "meeting_dir": tmp},
            summarize=lambda t, s, d: summarize.parse(
                '{"title": "Devis / client", "summary": "Devis validé.", "decisions": [], '
                '"actions": [{"who": "moi", "task": "Envoyer le devis", "due": "2026-10-17 10:00"}], "questions": []}'),
            deliver=lambda r, p, md: delivered.append((r, p, md)),
            notify=lambda kind, text: events.append(kind), log=lambda m: None,
            detect_fn=lambda: detect.Usage(mic=["Teams.exe"] if usage["on"] else [], source="windows"),
            capture_factory=_Rec,
            transcriber_factory=lambda **kw: transcribe.Transcriber(whisper_factory=lambda n: w, log=lambda m: None),
            clock=lambda: clock[0])
        actions = []
        for t, on in ((0, False), (3, True), (10, True), (19, True), (60, True), (70, False), (100, False), (116, False)):
            clock[0], usage["on"] = t, on
            actions.append(cop.tick())
        assert actions == [None, None, None, "start", None, None, None, "stop"], actions
        for _ in range(100):
            if delivered:
                break
            __import__("time").sleep(0.05)
        assert delivered, "compte rendu livré"
        r, path, md = delivered[0]
        assert events == ["start", "stop"], events
        assert path and Path(path).name.endswith("Devis client.md") and Path(path).read_text(encoding="utf-8") == md
        assert "Participants : On valide le devis ?" in md and "Moi : Oui, je l'envoie demain à 10 h" in md
        assert cop.last["lines"] == 2 and "Pas de réunion en cours" in cop.status()
        assert cop.start() and cop.session and cop.start() == "Je prends déjà des notes."
        cop.stop()
        for _ in range(100):                  # le second compte rendu s'écrit dans tmp : l'attendre
            if not cop.finalizing:
                break
            __import__("time").sleep(0.05)
    return "Teams détecté 16 s → notes ; 46 s sans micro → compte rendu « … - Devis client.md »"


class _Registry:
    def __init__(self):
        self.calls = []

    def run(self, name, args, ctx=None, **kw):
        self.calls.append((name, args))
        return "ok"


class _UI:
    def __init__(self):
        self.content, self.logs = [], []

    def show_content(self, title, text):
        self.content.append((title, text))

    def write_log(self, line):
        self.logs.append(line)


@check("10 livraison : HUD, rappels pour les actions datées, objectifs proposés, une phrase")
def _deliver():
    j = type("J", (), {})()
    j.ui, j._action_registry, j._plugin_registry, j.session, j.said = _UI(), _Registry(), _Registry(), object(), []
    j.plugin_say = j.said.append
    r = summarize.parse('{"title": "Sprint", "summary": "Bilan.", "actions": ['
                        '{"who": "Teddy", "task": "Corriger le login", "due": "2026-10-20 14:30"},'
                        '{"who": "moi", "task": "Écrire la doc", "due": "2026-10-21"},'
                        '{"who": "moi", "task": "Former Léa", "due": null},'
                        '{"who": "Paul", "task": "Relire", "due": "2026-10-20"}]}')
    done = M.deliver(j, r, "C:/x.md", M.user_names({"user_name": "Teddy Becard"}))
    assert j._action_registry.calls == [
        ("reminder", {"date": "2026-10-20", "time": "14:30", "message": "Corriger le login"}),
        ("reminder", {"date": "2026-10-21", "time": "09:00", "message": "Écrire la doc"})], j._action_registry.calls
    assert [c[1]["action"] for c in j._plugin_registry.calls] == ["propose"], "un objectif PROPOSÉ, jamais activé"
    assert done["voice"] and "Do not call any tool" in j.said[0] and j.ui.content[0][0].startswith("RÉUNION — Sprint")
    return "2 rappels (dont celui donné à « Teddy »), 1 objectif proposé, l'action de Paul laissée à Paul"


@check("11 PC relié : Oracle fait le compte rendu ; injoignable → Ollama sur ce poste")
def _client():
    from meeting import client
    sent = []

    class _Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.data).encode()

    def opener(req, timeout=0):
        sent.append((req.full_url, req.headers.get("Authorization"), json.loads(req.data)))
        return _Resp({"result": {"title": "Point", "summary": "ok", "actions": []}, "delivered": {}})

    r = client.remote_summary(lambda: ("http://oracle:8000/", "tok"), "[00:00:01] Moi : hé", datetime(2026, 10, 16, 9),
                              60, "C:/R", opener=opener)
    assert r["delivered_by"] == "oracle" and sent[0][0] == "http://oracle:8000/api/meeting/summary"
    assert sent[0][1] == "Bearer tok" and sent[0][2]["started"] == "2026-10-16T09:00:00" and sent[0][2]["where"] == "C:/R"
    try:
        client.remote_summary(lambda: None, "x", datetime.now(), 1, "")
        raise AssertionError("sans session, rien ne part")
    except ConnectionError:
        pass
    return "POST /api/meeting/summary avec le jeton ; pas de session → repli local"


@check("12 Oracle : POST /api/meeting/summary — jeton exigé, compte rendu, livraison")
def _assist():
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except Exception as e:
        return f"sans objet : fastapi absent ({type(e).__name__})"
    from server import assist
    dash = type("D", (), {"app": FastAPI(), "_tokens": {"tok"}})()
    j = type("J", (), {})()
    j.ui, j._action_registry, j._plugin_registry, j.session = _UI(), _Registry(), _Registry(), None
    assist.attach(dash, lambda: j, log=lambda m: None)
    real = brain.Brain
    brain.Brain = lambda cfg: _Brain(['{"title": "Revue", "summary": "Bilan", "actions": '
                                      '[{"who": "moi", "task": "Envoyer", "due": "2030-01-02 10:00"}]}'])
    try:
        c = TestClient(dash.app)
        assert c.post("/api/meeting/summary", json={"transcript": "x"}).status_code == 401
        assert c.post("/api/meeting/summary", json={}, headers={"Authorization": "Bearer tok"}).status_code == 400
        r = c.post("/api/meeting/summary", headers={"Authorization": "Bearer tok"},
                   json={"transcript": "[00:00:01] Moi : je l'envoie", "started": "2026-10-16T09:00:00", "duration_s": 600})
    finally:
        brain.Brain = real
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["result"]["title"] == "Revue" and data["delivered"]["reminders"], data
    assert j._action_registry.calls[0][1]["date"] == "2030-01-02" and j.ui.content[0][0].startswith("RÉUNION")
    return "401 sans jeton, 400 sans texte ; résumé, rappel créé et HUD servis par Oracle"


@check("13 hooks : main.py (mode local seulement), client_desktop, outil d'appareil")
def _hooks():
    repo = ROOT.parent
    main = (repo / "main.py").read_text(encoding="utf-8")
    assert "import meeting\n            meeting.attach(self)\n        except Exception:\n            pass" in main
    app = (repo / "client_desktop" / "app.py").read_text(encoding="utf-8")
    assert "import meeting.client" in app and "Copilote de réunion indisponible" in app, "hook du client gardé"
    j = type("J", (), {"ui": type("U", (), {"local_mode": False})()})()
    M._copilot = None
    M.attach(j)
    assert M.get() is None, "sur Oracle (pas de local_mode), aucun copilote ne démarre"
    from client_desktop import device
    manifest = device._device_plugin_manifest(repo / "plugins" / "meeting.py")
    assert manifest and manifest[0] == "meeting" and "soundcard" in manifest[1], manifest
    return "hook gardé dans main.py et app.py ; inerte sur Oracle ; outil meeting routé vers le PC"


@check("16 aide en direct : la règle repère une question qui te vise, pas les autres")
def _candidate():
    from meeting import live
    names = ("moi", "Teddy")
    yes = ["Teddy, tu te souviens de la date de livraison ?", "Vous avez le budget en tête ?",
           "T'as pu regarder le devis ?", "Teddy, est-ce que tu peux envoyer le devis avant vendredi ?",
           "Can you share your screen?"]
    no = ["Il reste une question : est-ce qu'on fait la version mobile ?", "Bonjour à tous.",
          "Le budget est fixé à 8 000 euros.", "Paul, ça avance ?", "Ok ?"]
    bad = [t for t in yes if not live.candidate(t, names)] + [t for t in no if live.candidate(t, names)]
    assert not bad, bad
    assert live.parse('```json {"for_user": true, "answer": "Le 15 novembre."}```') == {"for_user": True, "answer": "Le 15 novembre."}
    assert live.parse("je ne sais pas") is None
    return f"{len(yes)} questions pour toi repérées, {len(no)} autres ignorées"


@check("17 assistant : contexte des 3 min, une question à la fois (la plus récente gagne), pas de doublon")
def _assistant():
    import threading
    from meeting import live
    gate, shown, asked = threading.Event(), [], []

    def answer(system, user):
        asked.append(user)
        gate.wait(5)
        return '{"for_user": true, "answer": "Le 15 novembre, annoncé au début."}'

    a = live.Assistant(answer, lambda t, x: shown.append((t, x)), ("moi", "Teddy"), log=lambda m: None)
    L = transcribe.Line
    a.on_line(L(1, "participants", "La livraison est prévue le 15 novembre."))
    a.on_line(L(5, "participants", "Teddy, tu as la date de livraison ?"))
    a.on_line(L(6, "participants", "Teddy, tu as le budget ?"))          # pendant que la 1re est traitée
    a.on_line(L(7, "participants", "Teddy, tu as la date de livraison ?"))  # doublon : ignoré
    a.on_line(L(8, "participants", "Teddy, et la maquette, tu l'as ?"))    # remplace « le budget » en attente
    gate.set()
    for _ in range(100):
        if len(shown) >= 2 and not a._busy:
            break
        __import__("time").sleep(0.02)
    assert len(asked) == 2, asked
    assert "15 novembre" in asked[0] and "« Teddy, tu as la date de livraison ? »" in asked[0], "contexte fourni"
    assert "maquette" in asked[1] and "budget ?" not in asked[1].split("Last line")[1], "la plus récente a gagné"
    assert shown[0][1].startswith("Le 15 novembre")
    shown.clear()
    no = live.Assistant(lambda s, u: '{"for_user": false, "answer": "x"}', lambda t, x: shown.append(t),
                        ("moi", "Teddy"), log=lambda m: None)
    no.on_line(L(1, "participants", "Teddy, tu peux répéter ?"))
    __import__("time").sleep(0.2)
    assert shown == [], "le modèle dit « pas pour toi » : rien n'est affiché"
    return "2 appels pour 4 questions (doublon ignoré, attente remplacée) ; « pas pour toi » : rien"


@check("18 découpage à la phrase : silence jeté, fin après 1 s, micro plus exigeant, durée bornée")
def _utterances():
    got = []
    ch = capture.UtteranceChunker("participants", lambda s, t0, x: got.append((round(t0, 1), len(x) / capture.RATE)))
    n = int(capture.RATE * capture.BLOCK_S)
    voice, silence = np.full(n, 0.002, dtype=np.float32), np.zeros(n, dtype=np.float32)
    seq = [silence] * 4 + [voice] * 4 + [silence] * 3 + [voice] * 1 + [silence] * 3 + [voice] * 30
    for i, b in enumerate(seq):
        ch.feed(b, i * capture.BLOCK_S)
    ch.flush()
    assert got[0] == (2.0, 2.5), got           # 2 s de voix + 0,5 s de fin ; le silence d'avant jeté
    assert all(d <= capture.UTT_MAX_S for _, d in got) and len(got) == 3, got   # le bruit de 0,5 s jeté
    mic = []
    m = capture.UtteranceChunker("moi", lambda s, t0, x: mic.append(len(x) / capture.RATE))
    for b in [np.full(n, 0.0005, dtype=np.float32)] * 20:   # souffle de la pièce
        m.feed(b, 0)
    m.flush()
    assert mic == [], "le souffle du micro n'est pas une phrase"
    for b in [np.full(n, 0.05, dtype=np.float32)] * 20:
        m.feed(b, 0)
    assert mic and max(mic) <= capture.UTT_MAX_BY_STREAM["moi"], mic
    return f"phrases {got} ; micro : souffle ignoré, morceaux ≤ {capture.UTT_MAX_BY_STREAM['moi']} s"


@check("19 transcription : les phrases des participants passent devant le micro ; modèle chargé d'avance")
def _priority():
    order = []

    class _W:
        def transcribe(self, samples, **kw):
            order.append(round(float(samples[0]), 1))
            assert kw.get("temperature") == 0.0, "pas de repli en température"
            return iter([]), None

    loaded = []
    tr = transcribe.Transcriber(whisper_factory=lambda n: (loaded.append(n), _W())[1], log=lambda m: None,
                                priority=("participants",))
    tr.warm()
    tr.submit("moi", 0, np.full(10, 0.1, dtype=np.float32))
    tr.submit("moi", 1, np.full(10, 0.2, dtype=np.float32))
    tr.submit("participants", 2, np.full(10, 0.3, dtype=np.float32))
    tr.start()
    tr.finish(timeout=5)
    assert loaded == ["small"] and order[0] == 0.5, (loaded, order)     # 0.3 normalisé à 0,5 : servi en premier
    return f"modèle chargé avant tout ; ordre de décodage {order}"


@check("20 petite fenêtre et raccourci : exclue des captures (Windows), sans écran → repli ; Ctrl+Alt+J")
def _overlay():
    from meeting import hotkey, overlay
    ov = overlay.Overlay()
    try:
        from PyQt6.QtWidgets import QApplication
        has_app = QApplication.instance() is not None
    except Exception:
        has_app = False
    if not has_app:
        assert ov.show("t", "x") is False and ov.available is False, "sans QApplication : faux, l'appelant se rabat"
    notes = []
    cop = M.Copilot(lambda: {}, lambda *a: {}, lambda *a: None, lambda k, t: notes.append((k, t)), log=lambda m: None,
                    overlay=ov)
    cop._show("« Teddy ? »", "Le 15 novembre.")
    assert notes == [("assist", "« Teddy ? » — Le 15 novembre.")], notes
    if sys.platform != "win32":
        return "sans écran Qt : repli par notification ; raccourci et exclusion vérifiés sous Windows seulement"
    h = hotkey.Hotkey(lambda: None, log=lambda m: None)
    ok = h.start()
    h.stop()
    return f"repli par notification ; Ctrl+Alt+J {'enregistré puis libéré' if ok else 'déjà pris par une autre application'}"


@check("21 Matthew ne plante plus quand Whisper tourne dans le HUD : ctranslate2 chargé avant Qt")
def _preload():
    src = (ROOT.parent / "client_desktop" / "__init__.py").read_text(encoding="utf-8")
    assert "import ctranslate2" in src, "le préchargement a disparu"
    import client_desktop  # noqa: F401
    loaded = list(sys.modules)
    if "ctranslate2" not in loaded:
        return "ctranslate2 absent ici : rien à précharger"
    assert loaded.index("ctranslate2") < loaded.index("PyQt6") if "PyQt6" in loaded else True, "Qt chargé avant"
    return "client_desktop charge ctranslate2 avant PyQt6 (segfault 139 sinon, mesuré)"


@check("14 réel : la détection répond sur ce poste, sans lever")
def _real_detect():
    u = detect.in_use()
    assert isinstance(u, detect.Usage)
    if u.error and sys.platform not in ("win32", "darwin") and "pactl absent" in u.error:
        return f"sans objet ici : {u.error}"
    return f"{u.source} : {u.describe()}" + (f" ({u.error})" if u.error else "")


@check("15 réel : périphériques audio pour la capture (son du poste et micro)")
def _real_devices():
    try:
        import soundcard as sc
    except Exception as e:
        return f"sans objet : soundcard absent ({type(e).__name__}) — pip install soundcard"
    try:
        spk, mic = sc.default_speaker(), sc.default_microphone()
        loop = sc.get_microphone(spk.id, include_loopback=True)
    except Exception as e:
        return f"sans objet : pas de périphérique audio ici ({type(e).__name__})"
    return f"son du poste : {loop.name} ; micro : {mic.name}"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    width = max(len(n) for n, _, _ in _results)
    failed = 0
    print()
    for name, ok, detail in _results:
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print(f"\n  {len(_results) - failed}/{len(_results)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
