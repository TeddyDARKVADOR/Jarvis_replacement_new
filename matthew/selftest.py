"""
python -m matthew.selftest — Matthew, sans réseau, sans GPU, sans toucher memory/.

Tout tourne dans un dossier temporaire (MATTHEW_HOME), avec de faux modèles.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="matthew-selftest-"))
os.environ["MATTHEW_HOME"] = str(TMP / "home")

import matthew  # noqa: E402
from matthew import dataset as D  # noqa: E402
from matthew import evaluate as E  # noqa: E402
from matthew import train as T  # noqa: E402
from matthew.client import Client, to_openai_tools, usage_summary  # noqa: E402
from matthew.flywheel import Flywheel, schedule_command  # noqa: E402
from matthew.journal import Journal, Tap, scrub  # noqa: E402
from matthew.teacher import LicenseError, School, calls_match, check_teacher  # noqa: E402

REPO = matthew.REPO
CHECKS = []
DECLS = [
    {"name": "weather", "description": "Météo d'une ville",
     "parameters": {"type": "OBJECT", "properties": {"city": {"type": "STRING"}}, "required": ["city"]}},
    {"name": "open_app", "description": "Ouvre une application",
     "parameters": {"type": "OBJECT", "properties": {"app": {"type": "STRING"}}, "required": ["app"]}},
    {"name": "volume", "description": "Volume", "parameters": {"type": "OBJECT", "properties": {}}},
]
CFG = {**matthew.DEFAULTS, "teacher": {"base_url": "http://x", "model": "gpt-oss-120b",
                                        "license": "apache-2.0"},
       "min_new_examples": 3, "bootstrap_budget": 0, "dedup": False}


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        self.t += 60
        return self.t


def fake_post(script):
    """script(payload) -> message dict ; enveloppé en réponse OpenAI avec usage."""
    def post(url, headers, payload, timeout):
        msg = script(payload)
        calls = msg.get("tool_calls")
        if calls:
            msg = {**msg, "tool_calls": [{**c, "function": {**c["function"],
                   "arguments": json.dumps(c["function"]["arguments"])}} for c in calls]}
        return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return post


def smart_teacher(payload):
    """Professeur factice : météo → appelle weather(city) ; sinon répond."""
    msgs = payload["messages"]
    if msgs[-1]["role"] == "tool":
        return {"role": "assistant", "content": f"Voilà : {msgs[-1]['content']}"}
    user = msgs[-1]["content"].lower()
    if "météo" in user:
        city = user.split()[-1].strip("?")
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "a", "type": "function",
                                "function": {"name": "weather", "arguments": {"city": city}}}]}
    if "ouvre" in user:      # se trompe exprès d'outil
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "a", "type": "function",
                                "function": {"name": "volume", "arguments": {}}}]}
    return {"role": "assistant", "content": "Bonjour, je suis là."}


def world(root: Path):
    clock = Clock()
    j = Journal(root, clock=clock)
    j.save_tools(DECLS)
    tap = Tap(j, session_of=lambda: "s1")
    # 1 : météo avec outil
    tap.on_tool("weather", {"city": "paris"}, "18 degrés, nuageux")
    tap.on_turn("user", "quelle est la météo à paris")
    tap.on_turn("assistant", "Il fait 18 degrés à Paris.")      # Gemini
    # 2 : salut, sans outil
    tap.on_turn("user", "salut")
    tap.on_turn("assistant", "Bonjour monsieur.")
    # 3 : ouvre (le professeur se trompera)
    tap.on_tool("open_app", {"app": "spotify"}, "Spotify ouvert")
    tap.on_turn("user", "ouvre spotify")
    tap.on_turn("assistant", "C'est fait.")
    # 4 : corrigé par l'utilisateur
    tap.on_turn("user", "tu es qui")
    tap.on_turn("assistant", "Je suis un assistant Gemini.")
    j.correction("Je suis Matthew, votre assistant.")
    # 5 : oublié
    tap.on_turn("user", "mon code de carte est 4970 1234 5678 9012")
    tap.on_turn("assistant", "Noté.")
    j.forget_last()
    # 6, 7 : doublon de 1, plus récent
    tap.on_tool("weather", {"city": "lyon"}, "12 degrés")
    tap.on_turn("user", "quelle est la météo à lyon")
    tap.on_turn("assistant", "12 degrés.")
    tap.on_tool("weather", {"city": "lyon"}, "13 degrés")
    tap.on_turn("user", "Quelle est la météo à  Lyon")
    tap.on_turn("assistant", "13 degrés.")
    return j, clock


# ── 1. collecte ──────────────────────────────────────────────────────────────

@check("scrub masque clés, jetons, e-mails, téléphones, cartes, IBAN")
def _scrub():
    fake_key = "AI" + "za" + "SyA1234567890abcdefghijklmnopqrstuv"     # jamais une vraie clé dans le dépôt
    s = scrub({"a": f"clé {fake_key} mail bob@ex.fr",
               "b": ["appelle 06 12 34 56 78", "carte 4970 1234 5678 9012", "gs" + "k_abcdefghijklmnop1234",
                     "FR76 3000 6000 0112 3456 7890 189", "password: hunter2"]})
    blob = json.dumps(s, ensure_ascii=False)
    for leaked in ("AIzaSy", "bob@ex.fr", "06 12", "4970", "gsk_", "FR76", "hunter2"):
        assert leaked not in blob, (leaked, blob)
    return "7 motifs"


@check("Tap : outils pendant le tour + demande + réponse = un épisode, ordre Live respecté")
def _tap():
    root = TMP / "tap"
    j, _ = world(root)
    eps = [r for r in j.records() if r["kind"] == "episode"]
    assert len(eps) == 7, len(eps)
    assert eps[0]["tools"][0]["name"] == "weather" and eps[0]["tools"][0]["result"].startswith("18")
    assert eps[0]["reply"]["origin"] == "gemini"
    assert eps[1]["tools"] == []
    assert "4970" not in json.dumps(eps[4]), "la carte devait être masquée"
    return f"{len(eps)} épisodes"


@check("Tap.install se greffe sur JarvisLive (interface + outils) sans rien changer")
def _install():
    import asyncio
    from types import SimpleNamespace
    shown = []

    class UI:
        def write_log(self, text):
            shown.append(text)

    class Live:
        _asst_name = "Jarvis"
        def __init__(self):
            self.ui = UI()
        async def _execute_tool(self, fc):
            return SimpleNamespace(response={"result": f"{fc.name} fait"})

    os.environ["MATTHEW_HOME"] = str(TMP / "install")
    try:
        live = Live()
        tap = matthew.attach(live)
        fr = asyncio.run(live._execute_tool(SimpleNamespace(name="weather", args={"city": "x"})))
        assert fr.response == {"result": "weather fait"}, "la réponse à Gemini doit rester identique"
        for line in ("You: météo x", "Jarvis: Il fait beau.", "SYS: rien"):
            live.ui.write_log(line)
        assert shown == ["You: météo x", "Jarvis: Il fait beau.", "SYS: rien"], shown
        eps = [r for r in tap.journal.records() if r["kind"] == "episode"]
        assert len(eps) == 1 and eps[0]["user"] == "météo x" and eps[0]["tools"][0]["name"] == "weather", eps
        assert matthew.attach(SimpleNamespace(ui=UI())) is None, "faux JarvisLive : rien à capter"
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    return "transparent"


@check("PAUSED coupe la collecte")
def _paused():
    os.environ["MATTHEW_HOME"] = str(TMP / "paused")
    try:
        (matthew.data_dir() / "PAUSED").write_text("1")
        j = Journal()
        assert j.episode(session="s", user="u", tools=[], reply="r") is None
        assert list(j.records()) == []
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    return "rien écrit"


# ── 2. licence et professeur ─────────────────────────────────────────────────

@check("un professeur Gemini ou sans licence ouverte est refusé")
def _license():
    for bad in ({"model": "gemini-3.1-flash-lite", "license": "apache-2.0"},
                {"model": "some-model", "license": "proprietary"}, {"model": "x"}):
        try:
            check_teacher(bad)
        except LicenseError:
            continue
        raise AssertionError(f"accepté : {bad}")
    check_teacher({"model": "openai/gpt-oss-120b", "license": "apache-2.0"})
    return "3 refus, 1 accepté"


@check("concordance des appels : trace stricte, schéma souple")
def _match():
    by = {d["name"]: d for d in DECLS}
    trace = [{"name": "weather", "args": {"city": "Paris"}}]
    good = [{"function": {"name": "weather", "arguments": {"city": "paris "}}}]
    other = [{"function": {"name": "weather", "arguments": {"city": "Lyon"}}}]
    wrong = [{"function": {"name": "volume", "arguments": {}}}]
    assert calls_match(good, trace, by, "trace")[0]
    assert not calls_match(other, trace, by, "trace")[0]
    assert calls_match(other, trace, by, "schema")[0]
    assert not calls_match(wrong, trace, by, "schema")[0]
    assert not calls_match([{"function": {"name": "weather", "arguments": {}}}], trace, by, "schema")[0]
    return "5 cas"


def school_for(root):
    j, clock = world(root)
    client = Client("http://x", "gpt-oss-120b", purpose="teacher", post=fake_post(smart_teacher),
                    usage_root=root)
    return School(j, client, cfg=CFG, clock=clock), j


@check("école : vrais résultats d'outils, réponse du professeur, désaccords rejetés")
def _school():
    sch, _ = school_for(TMP / "school")
    st = sch.relabel()
    # 1, 2, 6, 7 ok ; 3 rejeté ; 4 (corrigé) et 5 (oublié) hors école
    assert st == {"ok": 4, "rejected": 1, "errors": 0}, st
    recs = list(sch.records())
    met = next(r for r in recs if r["status"] == "ok" and len(r["messages"]) > 3)
    roles = [m["role"] for m in met["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"], roles
    assert met["messages"][3]["content"] == "18 degrés, nuageux"       # le vrai résultat
    assert met["messages"][-1]["content"].startswith("Voilà")           # le professeur, pas Gemini
    assert "weather" in met["tools"] and len(met["tools"]) <= 1 + 5
    assert sch.relabel() == {"ok": 0, "rejected": 0, "errors": 0}, "déjà traité"
    return f"{st['ok']} ok, {st['rejected']} rejeté"


# ── 3. données ───────────────────────────────────────────────────────────────

@check("jeu de données : zéro cible Gemini, correction humaine, oubli respecté, doublons")
def _dataset():
    root = TMP / "ds"
    sch, j = school_for(root)
    sch.relabel()
    man = D.build(j, sch, cfg=CFG, clock=Clock())
    rows = [json.loads(l) for f in ("train.jsonl", "eval.jsonl")
            for l in (Path(man["dir"]) / f).read_text(encoding="utf-8").splitlines()]
    gemini = {r["reply"]["text"] for r in j.records() if r["kind"] == "episode"}
    for r in rows:
        assert r["origin"] in matthew.TRAINABLE_ORIGINS
        for m in r["messages"]:
            if m["role"] == "assistant":
                assert m["content"] not in gemini, f"cible Gemini : {m['content']}"
    users = [r["messages"][1]["content"] for r in rows]
    assert "tu es qui" in users and any(r["origin"] == "human" for r in rows)
    assert not any("carte" in u for u in users), "épisode oublié appris"
    assert not any("spotify" in u for u in users), "désaccord appris"
    assert sum("lyon" in u.lower() for u in users) == 1, "doublon"
    assert man["gemini_targets"] == 0 and man["train"] + man["eval"] == len(rows) == 4
    tools = rows[0]["tools"] or next(r["tools"] for r in rows if r["tools"])
    assert tools[0]["type"] == "function" and tools[0]["function"]["parameters"]["type"] == "object"
    return f"{man['train']} train / {man['eval']} eval, {man['origins']}"


@check("hold-out temporel : l'évaluation prend les plus récents")
def _split():
    root = TMP / "split"
    clock = Clock()
    j = Journal(root, clock=clock)
    for i in range(10):
        j.episode(session="s", user=f"question {i}", tools=[], reply="r")
    client = Client("http://x", "gpt-oss-120b", post=fake_post(smart_teacher), usage_root=root)
    sch = School(j, client, cfg=CFG, clock=clock)
    sch.relabel()
    man = D.build(j, sch, cfg=CFG, clock=clock)
    ev = [json.loads(l)["messages"][1]["content"]
          for l in (Path(man["dir"]) / "eval.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ev == ["question 9"], ev
    return "1 / 10, le dernier"


@check("DPO : correction humaine contre réponse du professeur")
def _dpo():
    root = TMP / "dpo"
    clock = Clock()
    j = Journal(root, clock=clock)
    j.episode(session="s", user="dis bonjour", tools=[], reply="Salut")
    client = Client("http://x", "gpt-oss-120b", post=fake_post(smart_teacher), usage_root=root)
    sch = School(j, client, cfg=CFG, clock=clock)
    sch.relabel()
    j.correction("Bonjour, ici Matthew.")
    man = D.build(j, sch, cfg=CFG, clock=clock)
    pairs = [json.loads(l) for l in (Path(man["dir"]) / "dpo.jsonl").read_text(encoding="utf-8").splitlines()]
    assert pairs and pairs[0]["chosen"] == "Bonjour, ici Matthew." and pairs[0]["rejected"] == "Bonjour, je suis là."
    return "1 paire"


# ── 4. entraînement, évaluation, boucle ──────────────────────────────────────

@check("plan d'entraînement : depuis la base, script valide, Modelfile")
def _plan():
    root = TMP / "plan"
    sch, j = school_for(root)
    sch.relabel()
    man = D.build(j, sch, cfg=CFG, clock=Clock())
    run = T.plan(Path(man["dir"]), CFG, root=root, clock=Clock())
    p = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    assert p["from"] == "base" and p["base_model"] == "Qwen/Qwen3.5-4B" and p["base_license"] == "apache-2.0"
    ast.parse((run / "train_matthew.py").read_text(encoding="utf-8"))
    assert "FROM ./gguf/" in (run / "Modelfile").read_text(encoding="utf-8")
    return run.name


@check("évaluation : juge, passes répétées, gate qui refuse un recul")
def _evaluate():
    rows = [{"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "météo à paris"},
                          {"role": "assistant", "content": "", "tool_calls": [
                              {"id": "c", "type": "function", "function": {"name": "weather", "arguments": {"city": "paris"}}}]}],
             "tools": to_openai_tools(DECLS)},
            {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "salut"},
                          {"role": "assistant", "content": "Bonjour"}], "tools": []}]
    perfect = Client("http://x", "m", post=fake_post(smart_teacher), usage_root=TMP)
    dumb = Client("http://x", "d", post=fake_post(lambda p: {"role": "assistant", "content": ""}), usage_root=TMP)
    a, b = E.score(perfect, rows, 2), E.score(dumb, rows, 2)
    assert a["composite"] == 1.0 and a["repeats"] == 2 and a["tool_args"] == 1.0, a
    assert b["composite"] == 0.0, b
    assert E.gate(a, None)[0] and not E.gate(b, None)[0]
    assert E.gate(a, b)[0] and not E.gate(b, a)[0]
    assert not E.gate({**a, "composite": 0.9, "tool_name": 0.5, "n": 2}, {**a, "composite": 0.8})[0], "recul outil"
    return f"parfait {a['composite']}, vide {b['composite']}"


@check("tokens de Matthew comptés à part, par usage")
def _usage():
    root = TMP / "usage"
    root.mkdir()
    c = Client("http://x", "matthew", purpose="matthew", post=fake_post(smart_teacher), usage_root=root)
    c.chat([{"role": "user", "content": "salut"}])
    c.chat([{"role": "user", "content": "salut"}])
    s = usage_summary(root)
    assert s == {"matthew": {"calls": 2, "in": 20, "out": 10}}, s
    return "2 appels, 30 tokens"


class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@check("boucle : seuil, entraînement, import, évaluation, promotion, verrou")
def _flywheel():
    root = TMP / "fly"
    sch, j = school_for(root)
    for i in range(3):          # assez d'exemples pour qu'un hold-out existe
        j.episode(session="s", user=f"bonjour numéro {i}", tools=[], reply="r")
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd[:2])
        return Proc()

    def trainer(run):
        return {"ok": True, "gguf": ["m.gguf"]}

    fw = Flywheel(root, CFG, school=sch, runner=runner, trainer=trainer, clock=Clock(),
                  client_factory=lambda m: Client("http://x", m, post=fake_post(smart_teacher), usage_root=root))
    dry = fw.cycle(execute=False)
    assert dry["outcome"] == "plan prêt (à sec)", dry
    rep = fw.cycle(execute=True)
    assert rep["outcome"] == "nouveau champion", rep
    assert ["ollama", "create"] in calls and ["ollama", "cp"] in calls
    assert json.loads((root / "champion.json").read_text())["model"] == "matthew"
    again = fw.cycle(execute=True)
    assert again["outcome"] == "en attente de données", again["outcome"]
    # Challengers : même données, autre base ; licence fermée ignorée ; une fois par palier.
    fw.cfg = {**fw.cfg, "base_challengers": [{"model": "corp/closed-7b", "license": "proprietary"},
                                             {"model": "Qwen/Qwen3.5-9B", "license": "apache-2.0"}]}
    ch = fw.cycle(execute=True)
    plan_step = next(s for s in ch["steps"] if s["step"] == "plan")
    assert plan_step["base"] == "Qwen/Qwen3.5-9B" and plan_step["why"] == "challenger", plan_step
    assert json.loads((Path(plan_step["run"]) / "plan.json").read_text(encoding="utf-8"))["base_model"] == "Qwen/Qwen3.5-9B"
    assert fw.cycle(execute=True)["outcome"] == "en attente de données", "challenger déjà essayé à ce palier"
    (root / "flywheel.lock").write_text("1")
    assert fw.cycle()["outcome"] == "occupé"
    (root / "flywheel.lock").unlink()
    st = fw.status()
    assert dry["steps"][0] == {"step": "école", "ok": True, "written": 7, "rejected": 1, "errors": 0}, dry["steps"][0]
    assert st["episodes"] == 10 and st["champion"] and st["tokens_total"]["teacher"]["calls"] > 0
    cmd = schedule_command(root=root)
    assert cmd[0] == "schtasks" and "matthew.flywheel --execute" in (root / "flywheel.cmd").read_text(encoding="utf-8")
    return f"{len(list((root / 'reports').glob('*.json')))} rapports"


@check("plugin matthew : status, correct, forget_last, pause / resume")
def _plugin():
    os.environ["MATTHEW_HOME"] = str(TMP / "plugin")
    try:
        sys.path.insert(0, str(REPO))
        from plugins import matthew as P
        j = Journal()
        j.episode(session="s", user="salut", tools=[], reply="yo")
        assert "Matthew" in P.run({"action": "status"})
        assert "Noté" in P.run({"action": "correct", "text": "Bonjour."})
        assert "pas" in P.run({"action": "forget_last"})
        assert "plus" in P.run({"action": "pause"}) and matthew.paused()
        assert "nouveau" in P.run({"action": "resume"}) and not matthew.paused()
        kinds = [r["kind"] for r in Journal().records()]
        assert kinds == ["episode", "correction", "forget"], kinds
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    return "5 actions"


@check("routeur : le bon outil en tête, l'apprentissage aide, recall sans triche")
def _router():
    from matthew.router import Router, recall, tokens
    assert tokens("Quelle MÉTÉO à Paris ?") == ["meteo", "paris"], tokens("Quelle MÉTÉO à Paris ?")
    r = Router(DECLS)
    assert r.pick("donne-moi la météo de Lyon", 1) == ["weather"]
    assert r.pick("ouvre l'application spotify", 1) == ["open_app"]
    eps = [{"user": f"monte le son un peu {i}", "tools": [{"name": "volume", "ok": True}]} for i in range(3)]
    assert Router(DECLS, eps).pick("monte le son", 1) == ["volume"], "appris du journal"
    rec = recall(DECLS, eps + [{"user": "météo à nice", "tools": [{"name": "weather", "ok": True}]}], k=1)
    assert rec["recall"] == 1.0 and rec["recall_lexical_only"] < 1.0, rec
    return f"recall@1 {rec['recall']} (lexical seul {rec['recall_lexical_only']})"


@check("routeur par le sens : demande française → outil décrit en anglais, exemples, repli lexical")
def _router_sem():
    from matthew.router import Router
    en = [{"name": "reminder", "description": "Schedule a reminder notification at a given time"},
          {"name": "weather_report", "description": "Get the weather forecast for a city"},
          {"name": "open_app", "description": "Launch a desktop application"}]
    concepts = {"remind": ("rappel", "remind", "schedule"), "weather": ("temps", "weather", "météo"),
                "app": ("ouvre", "launch", "application")}

    def embed(texts, query=False):
        return [[float(any(w in t.lower() for w in ws)) for ws in concepts.values()] for t in texts]
    lexical = Router(en)
    assert lexical.pick("rappelle-moi d'appeler maman", 1) != ["reminder"], "le lexical seul échoue (langues différentes)"
    r = Router(en, embed=embed)
    assert r.pick("rappelle-moi d'appeler maman", 1) == ["reminder"]
    assert r.pick("quel temps fera-t-il demain", 1) == ["weather_report"]
    ex = Router(en, embed=embed, exemplars=[("ouvre discord", "open_app")])
    assert ex.pick("ouvre spotify", 1) == ["open_app"]
    assert ex.pick("ouvre discord", 1, exclude_exemplar="ouvre discord") == ["open_app"]

    def broken(texts, query=False):
        raise OSError("Ollama éteint")
    assert Router(en, embed=broken).embed is None, "repli lexical si les plongements tombent"
    return "3 demandes, repli vérifié"


@check("validation des arguments : obligatoires, types, enum, inconnus")
def _valid():
    from matthew.bootstrap import valid_args
    s = {"type": "object", "properties": {"city": {"type": "string"}, "n": {"type": "integer"},
                                          "mode": {"type": "string", "enum": ["a", "b"]}},
         "required": ["city"]}
    assert valid_args({"city": "Lyon", "n": 2, "mode": "a"}, s)[0]
    for bad in ({}, {"city": ""}, {"city": "x", "n": "2"}, {"city": "x", "n": True},
                {"city": "x", "mode": "c"}, {"city": "x", "zz": 1}, {"_invalid_json": "{"}):
        assert not valid_args(bad, s)[0], bad
    return "1 accepté, 7 refusés"


def fake_bootstrap_teacher(payload):
    msgs = payload["messages"]
    if payload.get("response_format"):
        prompt = msgs[-1]["content"]
        if "nom : weather" in prompt:
            return {"role": "assistant", "content": json.dumps({"requests": [
                "quelle est la météo à paris", "météo demain à lyon", "ouvre spotify"]})}
        if "nom : " in prompt:
            return {"role": "assistant", "content": "pas du JSON"}
        return {"role": "assistant", "content": json.dumps({"requests": ["salut toi", "merci beaucoup"]})}
    return smart_teacher(payload)


@check("amorçage : demandes inventées, vérifiées à l'aveugle, reprise, quota")
def _bootstrap():
    from matthew.bootstrap import Bootstrap
    root = TMP / "boot"
    j = Journal(root, clock=Clock())
    j.save_tools(DECLS)
    c = Client("http://x", "gpt-oss-120b", post=fake_post(fake_bootstrap_teacher), usage_root=root)
    b = Bootstrap(j, c, cfg=CFG, sleep=lambda s: None)
    st = b.run(per_tool=3, chitchat=2)
    # weather : 2 vérifiées, « ouvre spotify » rejetée (le vérificateur choisit un autre outil) ;
    # open_app et volume : génération illisible ; conversation : 2 sans outil.
    assert st["ok"] == 4 and st["rejected"] == 3, st
    recs = [r for r in b.records() if r["status"] == "ok"]
    tool_ex = next(r for r in recs if r["target"] == "weather")
    assert tool_ex["messages"][-1]["tool_calls"][0]["function"]["arguments"]["city"] == "paris"
    assert "weather" in tool_ex["tools"] and tool_ex["synthetic"]
    again = b.run(per_tool=3, chitchat=2)
    assert again["calls"] == 2 and again["ok"] == 0, ("reprise : seuls open_app et volume sont retentés", again)
    b.run(per_tool=3, chitchat=2)
    assert b.run(per_tool=3, chitchat=2)["calls"] == 0, "après 3 échecs, l'outil est abandonné"
    man = D.build(j, None, cfg=CFG, clock=Clock())
    assert man["synthetic"] == 4 and man["origins"] == {"teacher": 4}, man

    def quota_post(url, headers, payload, timeout):
        from matthew.client import QuotaError
        raise QuotaError("429", retry_after=1)
    j2 = Journal(TMP / "boot2")
    j2.save_tools(DECLS)
    waits = []
    st = Bootstrap(j2, Client("http://x", "m", post=quota_post, usage_root=TMP), cfg=CFG,
                   sleep=waits.append).run(1, 0)
    assert "quota" in st["stopped"] and len(waits) == 3, (st, waits)
    return "4 exemples vérifiés, 3 rejetés ; quota : 3 attentes puis arrêt propre"


@check("remédiation : outils faibles du dernier candidat, demandes plus dures, une fois ; vérificateur distinct")
def _remediation():
    from matthew.bootstrap import Bootstrap
    root = TMP / "remed"
    j = Journal(root, clock=Clock())
    j.save_tools(DECLS)
    prompts, verified_by = [], []

    def gen(payload):
        if payload.get("response_format"):
            prompts.append(payload["messages"][-1]["content"])
        return fake_bootstrap_teacher(payload)

    def verify(payload):
        verified_by.append(payload["model"])
        return smart_teacher(payload)
    b = Bootstrap(j, Client("http://x", "gpt-oss-120b", post=fake_post(gen), usage_root=root),
                  cfg=CFG, sleep=lambda s: None,
                  verifier=Client("http://x", "gpt-oss-20b", post=fake_post(verify), usage_root=root))
    b.run(per_tool=3, chitchat=0)
    assert verified_by and set(verified_by) == {"gpt-oss-20b"}, "la vérification passe par l'autre modèle"
    (root / "last_eval.json").write_text(json.dumps({"at": "R1", "weak": ["weather"], "failures": [
        {"target": "weather", "user": "x", "got": ["volume"]}]}), encoding="utf-8")
    prompts.clear()
    st = b.run(per_tool=3, chitchat=0)
    assert st.get("remediated") == ["weather"], st
    assert any("DIFFICILES" in p and "volume" in p for p in prompts), "consigne dure + outil confondu"
    assert any(r.get("round") == "R1" and r["status"] == "ok" for r in b.records())
    assert "remediated" not in b.run(per_tool=3, chitchat=0), "une seule ronde par évaluation"
    return "ronde R1 : weather, confondu avec volume"


@check("synthétique réparti par tirage stable, pas par date")
def _syn_split():
    exs = [{"key": f"k{i}", "synthetic": True, "ts": i} for i in range(200)]
    tr, ev = D.split(exs, 0.15)
    assert 15 <= len(ev) <= 45 and len(tr) + len(ev) == 200, len(ev)
    assert {e["key"] for e in ev} == {e["key"] for e in D.split(exs, 0.15)[1]}, "stable"
    assert any(e["ts"] < 100 for e in ev), "pas seulement les plus récents"
    return f"{len(ev)}/200 en évaluation"


@check("phase 1 : Matthew en tête de la cognition seulement s'il est champion")
def _cog_order():
    os.environ["MATTHEW_HOME"] = str(TMP / "cog")
    try:
        base = ["local", "groq"]
        assert matthew.cognition_order(base, {}) == base, "pas de champion"
        (matthew.data_dir() / "champion.json").write_text(json.dumps({"score": {"composite": 0.3}}))
        assert matthew.cognition_order(base, {}) == base, "sous le plancher"
        (matthew.data_dir() / "champion.json").write_text(json.dumps({"score": {"composite": 0.8}}))
        assert matthew.cognition_order(base, {}) == ["matthew", "local", "groq"]
        assert matthew.cognition_order(base, {"cognition_provider_order": base}) == base, "ordre manuel"
        assert matthew.cognition_order(base, {"matthew": {"use_in_cognition": False}}) == base
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    return "4 cas"


@check("GPU loué : paquet autonome (données, plan 9B, run.sh) et retour évalué")
def _cloud():
    import zipfile
    from matthew import cloud
    root = TMP / "cloud"
    sch, j = school_for(root)
    sch.relabel()
    os.environ["MATTHEW_HOME"] = str(root)
    try:
        z = cloud.pack("Qwen/Qwen3.5-9B", CFG, root=root, clock=Clock())
        names = set(zipfile.ZipFile(z).namelist())
        assert {"plan.json", "train_matthew.py", "run.sh", "requirements.txt",
                "data/train.jsonl", "data/eval.jsonl"} <= names, names
        plan = json.loads(zipfile.ZipFile(z).read("plan.json"))
        assert plan["base_model"] == "Qwen/Qwen3.5-9B" and plan["from"] == "base"
        assert plan["manifest"]["train"] >= 3, plan["manifest"]
        run = z.parent
        assert not cloud.import_run(run, CFG)["ok"], "sans GGUF : refus"
        (run / "gguf").mkdir()
        (run / "gguf").mkdir(exist_ok=True) or (run / "gguf" / "m.Q4_K_M.gguf").write_bytes(b"x")
        (run / "data").mkdir()
        rows = [{"messages": [{"role": "user", "content": "salut"},
                              {"role": "assistant", "content": "Bonjour"}], "tools": []}]
        (run / "data" / "eval.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        res = cloud.import_run(run, CFG, runner=lambda *a, **k: Proc(),
                               client_factory=lambda m: Client("http://x", m, post=fake_post(smart_teacher),
                                                               usage_root=root))
        assert res["ok"] and "FROM ./gguf/m.Q4_K_M.gguf" in (run / "Modelfile").read_text(encoding="utf-8"), res
        assert matthew.champion()["base"] == "Qwen/Qwen3.5-9B"
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    return z.name


def _tool_row(i, city="paris", origin="teacher"):
    return {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": f"météo numéro {i} à {city}"},
                         {"role": "assistant", "content": "", "tool_calls": [
                             {"id": "c", "type": "function", "function": {"name": "weather", "arguments": {"city": city}}}]},
                         {"role": "tool", "tool_call_id": "c", "name": "weather", "content": "18"},
                         {"role": "assistant", "content": "18 degrés."}],
            "tools": to_openai_tools(DECLS), "origin": origin, "ts": i, "key": f"k{i}"}


@check("augmentations : pertinence (~10 %), masquage cohérent (~15 %), doublons, équilibre")
def _augment():
    from matthew import augment as A
    rows = [_tool_row(i) for i in range(400)]
    irr = A.irrelevance(rows)
    assert 20 <= len(irr) <= 60, len(irr)
    r = irr[0]
    assert all(t["function"]["name"] != "weather" for t in r["tools"]) and r["tools"]
    assert r["messages"][-1]["role"] == "assistant" and "tool_calls" not in r["messages"][-1]
    assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
    msk = A.mask(rows)
    assert 35 <= len(msk) <= 90, len(msk)
    m = msk[0]
    call = m["messages"][2]["tool_calls"][0]["function"]
    decl = next(t["function"] for t in m["tools"] if t["function"]["name"] == call["name"])
    assert call["name"].startswith("fn_") and list(call["arguments"]) == list(decl["parameters"]["properties"])
    assert decl["parameters"]["required"] == list(call["arguments"]), "obligatoires renommés aussi"
    assert m["messages"][3]["name"] == call["name"] and rows[0]["messages"][2]["tool_calls"][0]["function"]["name"] == "weather", "copie, pas modification"
    assert "Météo" in decl["description"] or decl["description"], "description gardée"
    # doublons par le sens : faux plongements, la correction humaine survit toujours
    near = [_tool_row(1), _tool_row(2), {**_tool_row(3), "origin": "human"}]
    kept, dropped = A.dedup(near, lambda texts: [[1.0, 0.0]] * len(texts))
    assert dropped == 2 and [k["origin"] for k in kept] == ["human"], kept
    assert A.dedup(near, None) == (near, 0)
    # équilibre : 300 météo contre 10 conversations → plafonné
    chat = [{"messages": [{"role": "user", "content": f"salut {i}"}, {"role": "assistant", "content": "yo"}],
             "origin": "teacher", "ts": i} for i in range(10)]
    vol = [{"messages": [{"role": "user", "content": f"volume {i}"}, {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "volume", "arguments": {}}}]}],
            "origin": "teacher", "ts": i} for i in range(10)]
    bal, cut = A.balance(rows[:300] + chat + vol)        # médiane 10 → plafond 30
    assert cut == 300 - 30 and len(bal) == 50, (cut, len(bal))
    return f"pertinence {len(irr)}/400, masqués {len(msk)}/400"


@check("contrat V2 : trois hooks gardés, données ignorées par Git")
def _contract():
    rt = (REPO / "cognition" / "runtime.py").read_text(encoding="utf-8")
    hook = rt.index('__import__("matthew").attach(jarvis)')
    assert hook < rt.index('if not getattr(ui, "local_mode", False)'), "le hook doit précéder le test du mode local"
    assert "except ImportError" in rt
    prov = (REPO / "cognition" / "providers.py").read_text(encoding="utf-8")
    assert '"matthew":' in prov
    off = (REPO / "offline" / "llm.py").read_text(encoding="utf-8")
    assert '"matthew:latest"' in off and "from matthew.router import pick" in off and "except ImportError" in off
    assert '__import__("matthew").cognition_order' in prov
    # Lu, pas importé : seuls main.py et les plugins ont le droit d'importer cognition.
    order = next(l for l in prov.splitlines() if l.startswith("DEFAULT_ORDER"))
    assert "matthew" not in order, "Matthew ne doit pas répondre avant d'être évalué"
    assert "memory/matthew/" in (REPO / ".gitignore").read_text(encoding="utf-8")
    for f in (REPO / "matthew").glob("*.py"):
        if f.name == "selftest.py":       # le test, lui, vérifie le plugin
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = (n.module or "") if isinstance(n, ast.ImportFrom) else n.names[0].name
                assert not mod.split(".")[0] in ("main", "core", "actions", "server", "plugins"), (f.name, mod)
    return "retirable"


def main() -> int:
    ok = 0
    for name, fn in CHECKS:
        try:
            detail = fn()
            ok += 1
            print(f"  ok   {name}" + (f" — {detail}" if detail else ""))
        except Exception as e:
            print(f"  ÉCHEC {name} : {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\nmatthew.selftest : {ok}/{len(CHECKS)}")
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
