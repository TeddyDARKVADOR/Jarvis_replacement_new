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
       "min_new_examples": 3, "bootstrap_budget": 0, "dedup": False,
       "vitrine_publish": False, "min_eval": 1}


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
    assert E.gate(a, None, min_eval=1)[0] and not E.gate(b, None, min_eval=1)[0]
    assert E.gate(a, b, min_eval=1)[0] and not E.gate(b, a, min_eval=1)[0]
    assert not E.gate({**a, "composite": 0.9, "tool_name": 0.5, "n": 2}, {**a, "composite": 0.8}, min_eval=1)[0], "recul outil"
    assert "trop petite" in E.gate(a, None)[1], "18 exemples ne suffisent plus : 30 requis par défaut"
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
        if "AUCUN" in prompt:
            return {"role": "assistant", "content": json.dumps({"requests": ["commande une pizza", "appelle un vrai taxi"]})}
        if "nom : weather" in prompt:
            return {"role": "assistant", "content": json.dumps({"requests": [
                "quelle est la météo à paris", "météo demain à lyon", "ouvre spotify"]})}
        if "nom : " in prompt:
            return {"role": "assistant", "content": "pas du JSON"}
        if "AUCUN" in prompt:
            return {"role": "assistant", "content": json.dumps({"requests": ["commande une pizza", "appelle un vrai taxi"]})}
        return {"role": "assistant", "content": json.dumps({"requests": ["salut toi", "merci beaucoup"]})}
    return smart_teacher(payload)


@check("amorçage : demandes inventées, vérifiées à l'aveugle, reprise, quota")
def _bootstrap():
    from matthew.bootstrap import Bootstrap
    root = TMP / "boot"
    j = Journal(root, clock=Clock())
    j.save_tools(DECLS)
    c = Client("http://x", "gpt-oss-120b", post=fake_post(fake_bootstrap_teacher), usage_root=root)
    import matthew.bootstrap as BS
    saved_targets = dict(BS.NO_TOOL_TARGETS)
    BS.NO_TOOL_TARGETS.update({"__chat__": 2, "__none__": 2})
    b = Bootstrap(j, c, cfg=CFG, sleep=lambda s: None)
    st = b.run(per_tool=3, chitchat=2)
    # conversation et refus D'ABORD (2 + 2) ; weather : 2 vérifiées, « ouvre spotify » rejetée ;
    # open_app et volume : génération illisible.
    targets = [r["target"] for r in b.records() if r["status"] == "ok"]
    assert targets[:4] == ["__chat__", "__chat__", "__none__", "__none__"], targets
    assert st["ok"] == 6 and st["rejected"] == 3, st
    recs = [r for r in b.records() if r["status"] == "ok"]
    tool_ex = next(r for r in recs if r["target"] == "weather")
    assert tool_ex["messages"][-1]["tool_calls"][0]["function"]["arguments"]["city"] == "paris"
    assert "weather" in tool_ex["tools"] and tool_ex["synthetic"]
    again = b.run(per_tool=3, chitchat=2)
    assert again["calls"] == 2 and again["ok"] == 0, ("reprise : seuls open_app et volume sont retentés", again)
    b.run(per_tool=3, chitchat=2)
    assert b.run(per_tool=3, chitchat=2)["calls"] == 0, "après 3 échecs, l'outil est abandonné"
    man = D.build(j, None, cfg=CFG, clock=Clock())
    assert man["synthetic"] == 6 and man["origins"] == {"teacher": 6}, man

    def quota_post(url, headers, payload, timeout):
        from matthew.client import QuotaError
        raise QuotaError("429", retry_after=1)
    j2 = Journal(TMP / "boot2")
    j2.save_tools(DECLS)
    waits = []
    st = Bootstrap(j2, Client("http://x", "m", post=quota_post, usage_root=TMP), cfg=CFG,
                   sleep=waits.append).run(1, 0)
    assert "quota" in st["stopped"] and len(waits) == 3, (st, waits)
    # Professeur de secours : quota épuisé → le modèle local prend le relais.
    j3 = Journal(TMP / "boot3")
    j3.save_tools(DECLS)
    fb = Bootstrap(j3, None, cfg={**CFG, "teacher_fallback": {"base_url": "http://local", "model": "qwen3.6:latest",
                                                               "verifier_model": "gemma4:12b", "license": "apache-2.0"}},
                   sleep=lambda s: None)
    fb.client = fb.verifier = Client("http://x", "m", post=quota_post, usage_root=TMP)
    fb._fallback = fb.cfg["teacher_fallback"]
    try:
        fb._chat([{"role": "user", "content": "x"}])
    except Exception:
        pass
    assert fb.fell_back and fb.teacher["model"] == "qwen3.6:latest" and fb.verifier_model == "gemma4:12b"
    BS.NO_TOOL_TARGETS.update(saved_targets)
    return "6 exemples vérifiés (conversation et refus d'abord), 3 rejetés ; quota : relais local"


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
        assert matthew.cognition_order(base, {}) == base, "pas de banc de cognition : en retrait"
        (matthew.data_dir() / "champion.json").write_text(json.dumps({"score": {"composite": 0.8}, "cognition": {"score": 0.5}}))
        assert matthew.cognition_order(base, {}) == base, "banc insuffisant"
        (matthew.data_dir() / "champion.json").write_text(json.dumps({"score": {"composite": 0.8}, "cognition": {"score": 0.9}}))
        assert matthew.cognition_order(base, {}) == ["matthew", "local", "groq"]
        assert matthew.cognition_order(base, {"cognition_provider_order": base}) == base, "ordre manuel"
        assert matthew.cognition_order(base, {"matthew": {"use_in_cognition": False}}) == base
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    return "6 cas"


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


def _vitrine_world(root: Path):
    """Un journal réel (privé), un amorçage synthétique, un champion et un rapport."""
    from matthew.bootstrap import Bootstrap
    j = Journal(root, clock=Clock())
    j.save_tools(DECLS)
    j.episode(session="s", user="appelle ma sœur Camille au 06 11 22 33 44", tools=[], reply="Je l'appelle.")
    j.episode(session="s", user="mon secret : le coffre est derrière le tableau", tools=[], reply="Noté.")
    Bootstrap(j, Client("http://x", "gpt-oss-120b", post=fake_post(fake_bootstrap_teacher), usage_root=root),
              cfg=CFG, sleep=lambda s: None).run(per_tool=3, chitchat=0)
    (root / "champion.json").write_text(json.dumps({"model": "matthew", "since": "20261020-205246",
        "base": "Qwen/Qwen3.5-4B", "score": {"composite": 0.85, "tool_name": 1.0, "secret_field": "x"}}), encoding="utf-8")
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / "20261020-205246.json").write_text(json.dumps({"at": "20261020-205246", "outcome": "nouveau champion",
        "steps": [{"step": "données", "train": 4, "eval": 1}, {"step": "évaluation", "promote": True,
                   "candidate": {"composite": 0.85, "tool_name": 1.0, "failures": [{"user": "mon secret"}]}}]}), encoding="utf-8")
    (root / "last_eval.json").write_text(json.dumps({"per_tool": {"weather": 0.9}, "failures": [
        {"user": "appelle ma sœur Camille"}]}), encoding="utf-8")
    return j


@check("vitrine : liste blanche, aucun texte du journal, garde-fou qui refuse chaque fuite")
def _vitrine():
    import copy as _copy
    from matthew import vitrine as V
    root = TMP / "vitrine"
    _vitrine_world(root)

    class Showy:                                  # le champion rejoue l'examen
        model = "matthew"
        def chat(self, messages, tools=None, **kw):
            return smart_teacher({"messages": messages})
    V.replay(Showy(), root=root, day="2026-10-20")
    data = V.gather(root)
    assert V.check_public(data, root) > 20
    blob = json.dumps(data, ensure_ascii=False)
    for leak in ("Camille", "06 11", "coffre", "secret_field", "failures", "Je l'appelle", str(TMP)):
        assert leak not in blob, f"fuite : {leak}"
    assert data["showcase"] and all(s["results"] for s in data["showcase"])
    assert data["champion"]["score"]["composite"] == 0.85 and "secret_field" not in data["champion"]["score"]
    # Chaque fuite fabriquée doit être refusée.
    attempts = {
        "texte du journal": lambda d: d["showcase"][0].__setitem__("request", "mon secret : le coffre est derrière le tableau"),
        "champ en plus": lambda d: d["champion"].__setitem__("journal", "x"),
        "téléphone": lambda d: d["tools"][0].__setitem__("label", "appelle le 06 11 22 33 44"),
        "chemin local": lambda d: d["tools"][0].__setitem__("label", "D:/DEV/secret"),
        "outil inconnu": lambda d: d["tools"][0].__setitem__("id", "rm_rf"),
        "demande inventée hors amorçage": lambda d: d["showcase"][0].__setitem__("request", "une demande qui n'existe pas"),
        "jour mal formé": lambda d: d["showcase"][0]["results"].__setitem__("hier", {"chosen": None, "ok": False, "ms": 0}),
    }
    for name, tamper in attempts.items():
        bad = _copy.deepcopy(data)
        tamper(bad)
        try:
            V.check_public(bad, root)
        except V.PrivacyError:
            continue
        raise AssertionError(f"fuite acceptée : {name}")
    # build écrit le site complet ; publish committe et pousse via git (faux runner).
    site = TMP / "site"
    res = V.build(site, root)
    assert (site / "index.html").exists() and (site / "data" / "vitrine.json").exists() and (site / ".nojekyll").exists()
    assert res["og"] and (site / "og.png").stat().st_size > 1000
    repo = TMP / "vrepo"
    (repo / ".git").mkdir(parents=True)
    calls = []

    def git(cmd, **kw):
        calls.append(cmd[3])
        return Proc(out=" M data/vitrine.json" if cmd[3] == "status" else "")
    os.environ["MATTHEW_HOME"] = str(root)
    try:
        out = V.publish(repo, CFG, runner=git, do_replay=False)
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    assert out["pushed"] and calls == ["add", "status", "commit", "push"], (out, calls)
    return f"{len(attempts)} fuites refusées, {res['fields_checked']} valeurs vérifiées"


@check("vitrine : jamais le nom de l'hôte, journal des améliorations nettoyé, drapeau")
def _vitrine_flag():
    from matthew import vitrine as V
    assert V.clean_title("JARVIS portable : clé USB nomade") == "Matthew portable : clé USB nomade"
    assert V.clean_title("Déploiement vers MARK LIII") == "Déploiement vers le serveur"
    for bad in ("x", "fix C:/Users/moi/secret.txt", "envoie à bob@ex.fr", "voir https://exemple.com"):
        assert V.clean_title(bad) is None, bad
    assert V.pub("shutdown_jarvis") == "shutdown"
    git_log = ("@@2026-10-20|JARVIS : nouveau HUD\nclient_desktop/hud/web/index.html\nclient_desktop/hud/window.py\n"
               "@@2026-10-19|Matthew : routeur par le sens\nmatthew/router.py\ncapabilities/graphs/matthew.json\n"
               "@@2026-10-18|clé AIza" + "x" * 35 + "\nmain.py\n")
    ch = V.changes(runner=lambda *a, **k: Proc(out=git_log))
    assert ch == [{"date": "2026-10-20", "title": "Matthew : nouveau HUD", "area": "Interface"},
                  {"date": "2026-10-19", "title": "Matthew : routeur par le sens", "area": "IA"}], ch
    root = TMP / "vitrine"                      # monde construit par le test précédent
    data = V.gather(root)
    data["changes"] = ch
    V.check_public(data, root)
    bad = json.loads(json.dumps(data))
    bad["tools"][0]["label"] = "Arrêt de Jarvis"
    try:
        V.check_public(bad, root)
        raise AssertionError("le nom de l'hôte est passé")
    except V.PrivacyError:
        pass
    # Drapeau : rien sans lui ; publié et baissé avec lui ; pas deux fois en 5 minutes.
    clock = Clock()
    cfg = {**CFG, "vitrine_publish": True, "vitrine_repo": str(TMP)}
    calls = []
    pub_ok = lambda: calls.append(1) or {"ok": True, "pushed": True}
    assert V.run(root, cfg, publisher=pub_ok, clock=clock)["ran"] is False
    V.flag("code : nouveau commit", root)
    V.flag("IA : nouveau champion", root)
    out = V.run(root, cfg, publisher=pub_ok, clock=clock)
    assert out["ran"] and out["ok"] and calls == [1] and not (root / "vitrine" / "FLAG").exists(), out
    V.flag("code : encore", root)
    assert V.run(root, cfg, publisher=pub_ok, clock=clock)["ran"] is False, "moins de 5 min après"
    clock.t += 600
    fail = lambda: {"ok": False, "reason": "réseau"}
    assert V.run(root, cfg, publisher=fail, clock=clock)["ok"] is False
    assert (root / "vitrine" / "FLAG").exists(), "échec : le drapeau reste levé"
    # Crochets Git : ajoutés à la fin d'un crochet existant, une seule fois.
    repo = TMP / "hookrepo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / ".git" / "hooks" / "post-commit").write_text("#!/bin/sh\necho déjà là\n", encoding="utf-8")
    V.install_hooks(repo)
    V.install_hooks(repo)
    pc = (repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
    assert "echo déjà là" in pc and pc.count(V.HOOK_MARK) == 1 and "flag" in pc and "--spawn" in pc
    assert (repo / ".git" / "hooks" / "post-merge").exists()
    return "marque filtrée, 2 améliorations sur 3, drapeau levé / baissé / gardé"


@check("tokens : clés hachées, budget du jour, requêtes par minute, comptes, vraie passerelle HTTP")
def _gateway():
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from matthew import gateway as G
    root = TMP / "gw"
    root.mkdir()
    clock = Clock()
    keys = G.Keys(root, clock=lambda: clock.t)
    tel = keys.create("telephone", budget=100, rpm=10)
    pc = keys.create("pc", budget=10_000, rpm=50)
    assert tel.startswith("mk-") and tel not in (root / "keys.json").read_text(encoding="utf-8"), "seulement le hash"
    seen = []

    def fake_ollama(url, payload, timeout):
        seen.append(payload)
        return 200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                     "usage": {"prompt_tokens": 30, "completion_tokens": 10}}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), G.make_handler(keys, "http://ollama/v1", post=fake_ollama))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"

    def call(key, body=None, path="/chat/completions"):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read()), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), dict(e.headers)
    try:
        msg = {"messages": [{"role": "user", "content": "salut"}]}
        assert call("mk-faux", msg)[0] == 401
        code, data, h = call(tel, msg)
        assert code == 200 and h["X-Matthew-Tokens-Today"] == "40", (code, h)
        assert seen[-1]["model"] == "matthew" and seen[-1]["stream"] is False and seen[-1]["reasoning_effort"] == "none"
        assert call(tel, {**msg, "model": "autre-modele"})[0] == 403
        assert call(tel, msg)[0] == 200                     # 80 / 100
        assert call(tel, msg)[0] == 200                     # reste 20 : admis (vérifié avant l'appel), 120 / 100
        code, data, h = call(tel, msg)
        assert code == 429 and "budget" in data["error"]["message"] and int(h["Retry-After"]) > 0, (code, data)
        for _ in range(3):
            clock.t += 1
        code, *_ = call(pc, msg)
        assert code == 200, "une autre clé n'est pas touchée"
        assert call(pc, path="/usage")[1]["today"] == 40
        keys2 = G.Keys(root, clock=lambda: clock.t)
        assert keys2.revoke("pc") and call(pc, msg)[0] == 401
        rpm_key = keys.create("rafale", budget=10_000, rpm=2)
        assert [call(rpm_key, msg)[0] for _ in range(3)] == [200, 200, 429]
        assert {r["name"]: r["today"] for r in keys.table()} == {"pc": 40, "rafale": 80, "telephone": 120}
        assert usage_summary(root)["gateway:telephone"]["calls"] == 3
    finally:
        httpd.shutdown()
    return "401 / 403 / 429 budget / 429 rpm / révocation"


@check("juge des arguments : dates, heures, chemins, nombres, casse, inclusion")
def _norm():
    from matthew.norm import same
    yes = [("12 juillet", "2027-07-12"), ("12/07", "le 12 juillet"), ("15h", "15:00"), ("15 h 00", "3 pm"),
           ("C:\\Projet\\app.js", "c:/projet/app.js/"), ("8081", 8081), ("1,5", 1.5), ("oui", True),
           ("Tour Eiffel", "page Wikipédia de la Tour Eiffel"), ("Écrans", "ecrans")]
    no = [("Paris", "Lyon"), ("Spotify", "Discord"), ("12 juillet", "13 juillet"), ("1", "11"), ("15h", "16h")]
    bad = [p for p in yes if not same(*p)] + [p for p in no if same(*p)]
    assert not bad, bad
    from matthew import evaluate as E
    exp = {"role": "assistant", "tool_calls": [{"function": {"name": "flight", "arguments": {"date": "12 juillet", "to": "Rome"}}}]}
    got = {"tool_calls": [{"function": {"name": "flight", "arguments": {"date": "2027-07-12", "to": "rome"}}}]}
    assert E.judge(exp, got)["tool_args"] == 1.0, "même sens, forme différente : juste"
    return f"{len(yes)} égalités, {len(no)} différences"


@check("évaluation par outil : au moins un tiers de chaque outil, jusqu'à 3 ; refus mesurés")
def _split_per_tool():
    exs = [{"key": f"{t}{i}", "synthetic": True, "target": t, "ts": i} for t in ("a", "b", "c") for i in range(6)]
    exs += [{"key": f"n{i}", "synthetic": True, "target": "__none__", "ts": i} for i in range(9)]
    tr, ev = D.split(exs, 0.15)
    per = {t: sum(e["target"] == t for e in ev) for t in ("a", "b", "c", "__none__")}
    assert per == {"a": 2, "b": 2, "c": 2, "__none__": 3}, per
    from matthew import evaluate as E

    class NoTool:
        model = "m"
        def chat(self, *a, **k):
            return {"content": "Je ne peux pas faire ça.", "_latency": 0.1}
    rows = [{"messages": [{"role": "user", "content": "commande une pizza"}, {"role": "assistant", "content": "Je ne peux pas."}],
             "tools": [], "target": "__none__"}]
    sc = E.score(NoTool(), rows)
    assert sc["irrelevance"] == 1.0, sc
    return f"évaluation {per}"


@check("banc de cognition : vrais prompts de la cognition (lus sans import), notés par règles")
def _cogbench():
    from matthew import cogbench as CB
    assert "Answer with exactly one word: YES or NO" in CB._prompt("cognition/policy.py", "JUDGE_PROMPT")
    assert "ONE JSON object" in CB._prompt("cognition/autonomy.py", "PLANNER_PROMPT")

    class Oracle:
        """Répond juste : OUI si le fait n'ajoute rien, un plan JSON valide sinon."""
        model = "oracle"
        def chat(self, messages, *a, **k):
            p = messages[-1]["content"]
            if "YES or NO" in p:
                for said, fact, want in CB.JUDGE_CASES:
                    if f"store:\n{fact}\n" in p and said[0] in p:
                        return {"content": "YES" if want else "NO"}
            return {"content": '{"action": "web_search", "args": {"query": "vols Rome mai"}, "reason": "r"}'}

    class Dumb:
        model = "dumb"
        def chat(self, *a, **k):
            return {"content": "bof"}
    good, bad = CB.run(Oracle()), CB.run(Dumb())
    assert good["score"] == 1.0 and bad["score"] == 0.0, (good, bad)
    assert CB.plan_ok('{"action": "rm", "args": {}}')[0] is False and CB.plan_ok('{"action": "stop", "args": {}}')[0]
    assert {w for _, _, w in CB.JUDGE_CASES} == {True, False}
    return f"{len(CB.JUDGE_CASES)} jugements, {len(CB.PLANNER_CASES)} plans"


@check("streaming : morceaux relayés dès leur arrivée, tokens comptés à la fin")
def _gateway_stream():
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from matthew import gateway as G
    root = TMP / "gws"
    root.mkdir()
    keys = G.Keys(root)
    k = keys.create("pc", budget=10_000, rpm=50)
    sent = []

    def fake_stream(url, payload, timeout):
        sent.append(payload)
        for w in ("Bon", "jour", " !"):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": w}}]})
            yield ""
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3}})
        yield ""
        yield "data: [DONE]"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), G.make_handler(keys, "http://o/v1", stream=fake_stream))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat/completions",
                                     json.dumps({"stream": True, "messages": [{"role": "user", "content": "salut"}]}).encode(),
                                     {"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.headers["Content-Type"].startswith("text/event-stream")
            body = r.read().decode()
        text = "".join(json.loads(l[6:])["choices"][0]["delta"]["content"] for l in body.splitlines()
                       if l.startswith("data: {") and json.loads(l[6:]).get("choices"))
        assert text == "Bonjour !" and body.rstrip().endswith("data: [DONE]"), body
        assert sent[0]["stream"] is True and sent[0]["stream_options"] == {"include_usage": True}
        import time as _t
        _t.sleep(0.2)
        assert keys.spent_today("pc") == 12, keys.spent_today("pc")
    finally:
        httpd.shutdown()
    assert G.tailscale_ip(runner=lambda *a, **k: Proc(out="100.101.102.103\n")) in ("100.101.102.103", None)
    return "3 morceaux, 12 tokens"


@check("entraînement en direct : journal du Trainer lu, live.json, publié sur la vitrine")
def _live():
    from matthew import train as T
    from matthew import vitrine as V
    assert T.parse_progress("{'loss': '0.1234', 'grad_norm': '1.2', 'epoch': '0.57'}") == {"loss": 0.1234, "epoch": 0.57}
    assert T.parse_progress("{'eval_loss': 0.12, 'eval_runtime': 15.2, 'epoch': 2.0}") == {"eval_loss": 0.12, "epoch": 2.0}
    assert T.parse_progress("Unsloth: bonjour") is None
    root = TMP / "vitrine"
    fw = Flywheel(root, CFG, clock=Clock())
    fw._live({"phase": "entraînement", "epoch": 1.5, "epochs": 3, "losses": [0.3, 0.2]})
    live = json.loads((root / "vitrine" / "live.json").read_text(encoding="utf-8"))
    import time as _t
    live["at"] = _t.time()
    (root / "vitrine" / "live.json").write_text(json.dumps(live), encoding="utf-8")
    data = V.gather(root)
    assert data["live"]["epochs"] == 3 and data["live"]["losses"] == [0.3, 0.2]
    V.check_public(data, root)
    assert "<feed" in V.atom(data) and "Matthew" in V.atom(data)
    (root / "vitrine" / "live.json").unlink()
    assert V.gather(root)["live"] is None
    return "époque 1,5 / 3, deux pertes"


@check("HUD : onglet MATTHEW et badge « qui répond » (matthew.hud, sans backend réel)")
def _hud():
    from types import SimpleNamespace
    from matthew import hud as H
    assert H.who_answers(None)["brain"] == "Gemini"
    sup = SimpleNamespace(active=True, voice=SimpleNamespace(model="matthew:latest"))
    assert H.who_answers(SimpleNamespace(_offline=SimpleNamespace(supervisor=sup)))["brain"] == "Matthew"
    sup.voice.model = "gemma4:12b"
    assert H.who_answers(SimpleNamespace(_offline=SimpleNamespace(supervisor=sup)))["brain"] == "hors-ligne"
    os.environ["MATTHEW_HOME"] = str(TMP / "hudhome")
    try:
        st = H.call("matthew.status")
        assert "who" in st and "champion" in st and st["vitrine_url"].startswith("https://"), st
        assert H.call("matthew.correct", {"text": ""})["error"]
        assert H.call("matthew.nope")["error"]
    finally:
        os.environ["MATTHEW_HOME"] = str(TMP / "home")
    js = (REPO / "client_desktop" / "hud" / "web" / "js" / "matthew.js").read_text(encoding="utf-8")
    html = (REPO / "client_desktop" / "hud" / "web" / "index.html").read_text(encoding="utf-8")
    assert "rpc('matthew.status'" in js and 'data-tab="matthew"' in html and 'id="brain-chip"' in html
    assert 'startswith("matthew.")' in (REPO / "local_hud.py").read_text(encoding="utf-8")
    return "Gemini / Matthew / hors-ligne"


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
