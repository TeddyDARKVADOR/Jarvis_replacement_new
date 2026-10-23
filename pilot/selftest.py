"""
pilot/selftest.py — le pilote, sans toucher à l'écran.

    python -m pilot.selftest

Faux écran, faux clavier, faux modèle : rien n'est cliqué ni tapé. Les deux
derniers contrôles lisent le vrai poste (arbre de la fenêtre active,
applications et schémas déclarés), en lecture seule, et se déclarent « sans
objet » sans bureau (CI, conteneur).
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pilot as P
from pilot import agent as A
from pilot import brain as B
from pilot import discover as D
from pilot import model as Mo
from pilot import recipes as R
from pilot import screen as S

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


@check("1 contrat V2 : pilot/ n'importe rien de Matthew au chargement")
def _contract():
    banned = ("main", "core", "actions", "plugins", "cognition", "server", "dashboard", "client_desktop",
              "offline", "nightshift", "memory", "context", "meeting")
    bad = []
    for f in sorted(ROOT.glob("*.py")):
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            bad += [f"{f.name}: {n}" for n in names if n.split(".")[0] in banned]
    assert not bad, bad
    return f"{len(list(ROOT.glob('*.py')))} fichiers ; core.confirm et memory lus seulement à l'appel"


@check("2 éléments : visibles, utiles, sans doublon, numérotés ; coordonnées 0-1000 dans les deux sens")
def _model():
    screen = (0, 0, 2000, 1000)
    els = [Mo.Element(0, "button", "Envoyer", rect=(100, 100, 200, 140)),
           Mo.Element(0, "button", "Envoyer", rect=(100, 100, 200, 140)),          # doublon
           Mo.Element(0, "pane", "Fond", rect=(0, 0, 2000, 1000)),                 # ni actionnable ni texte
           Mo.Element(0, "text", "", rect=(5, 5, 50, 20)),                         # texte vide
           Mo.Element(0, "edit", "Tapez un message", rect=(300, 900, 900, 940)),
           Mo.Element(0, "button", "Hors écran", rect=(2100, 10, 2200, 40)),
           Mo.Element(0, "text", "Paul : à demain", rect=(300, 800, 700, 820))]
    kept = Mo.keep(els, screen)
    assert [(e.id, e.name) for e in kept] == [(1, "Envoyer"), (2, "Tapez un message"), (3, "Paul : à demain")], kept
    assert Mo.to_norm(kept[0].center, screen) == (75, 120)
    assert Mo.from_norm(75, 120, screen) == (150, 120)
    assert '[1] button "Envoyer" @(75,120)' in kept[0].render(screen)
    many = [Mo.Element(0, "text", f"t{i}", rect=(i, 0, i + 10, 10)) for i in range(300)] + \
        [Mo.Element(0, "button", "OK", rect=(500, 500, 540, 520))]
    cut = Mo.keep(many, (0, 0, 1000, 1000), limit=20)
    assert len(cut) == 20 and any(e.name == "OK" for e in cut), "à la coupe, le bouton passe avant les textes"
    return "doublon, fond, vide, hors écran écartés ; bouton gardé à la coupe ; 0-1000 aller-retour"


@check("3 découverte : rang des applications, entrées .desktop, sites regroupés sans jeton ni paramètre")
def _discover():
    items = [{"name": "Microsoft Teams"}, {"name": "Steam"}, {"name": "Visual Studio Code"}, {"name": "Calculatrice"}]
    assert D.rank("teams", items)[0]["name"] == "Microsoft Teams"
    assert D.rank("calculatrice", items) == [{"name": "Calculatrice"}]
    entry = D.parse_desktop("[Desktop Entry]\nType=Application\nName=Zoom\nExec=zoom %U\n"
                            "MimeType=x-scheme-handler/zoommtg;x-scheme-handler/zoomus;application/x-zoom\n", "zoom.desktop")
    assert entry == {"name": "Zoom", "launch": "zoom.desktop", "schemes": ["zoommtg", "zoomus"]}, entry
    assert D.parse_desktop("[Desktop Entry]\nType=Application\nName=X\nNoDisplay=true\n", "x.desktop") is None
    rows = [{"url": "https://online.visual-paradigm.com/app/diagrams/#proj=1&tok=SECRET", "title": "Untitled | Visual Paradigm",
             "visits": 40, "last": "2026-10-02"},
            {"url": "https://www.online.visual-paradigm.com/x", "title": "VP", "visits": 7, "last": "2026-09-01"},
            {"url": "https://onedrive.live.com/:p:/g/personal/B2FE/s!Aoc?resid=SECRET", "title": "Chapitre 5 &#39;UML",
             "visits": 8, "last": "2026-09-24"}]
    sites = D.by_site(rows)
    assert [s["site"] for s in sites] == ["online.visual-paradigm.com", "onedrive.live.com"] and sites[0]["visits"] == 47
    assert "SECRET" not in json.dumps(sites) and sites[1]["example"] == "https://onedrive.live.com/:p:", sites
    assert sites[1]["title"] == "Chapitre 5 'UML", "entités HTML décodées"
    return "Teams avant Steam ; schémas zoommtg/zoomus lus ; jetons et paramètres retirés de l'historique"


@check("4 requête au modèle : consigne, historique, écran, image ; réponse lue ; 429 → retryDelay attendu")
def _brain():
    body = B.build_request("envoie à Paul", ["1: open(target='msteams:') → ok"], "Foreground window: 'Teams'",
                           b"\xff\xd8jpeg", ["« envoie à Léa » (Teams) : open(target='msteams:')"])
    parts = body["contents"][0]["parts"]
    assert "TASK: envoie à Paul" in parts[0]["text"] and "msteams" in parts[0]["text"] and "Approaches" in parts[0]["text"]
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert body["tool_config"]["function_calling_config"]["mode"] == "ANY"
    names = {t["name"] for t in body["tools"][0]["function_declarations"]}
    assert {"open", "click", "type_text", "confirm", "done", "visited_sites", "url_schemes"} <= names
    assert B.parse({"candidates": [{"content": {"parts": [{"thoughtSignature": "x"},
                   {"functionCall": {"name": "click", "args": {"element": 3}}}]}}]}) == ("click", {"element": 3})
    try:
        B.parse({"candidates": [{"content": {"parts": [{"text": "je ne sais pas"}]}}]})
        raise AssertionError("une réponse sans action acceptée")
    except B.BrainError:
        pass
    calls, slept = [], []

    def transport(url, headers, req, timeout):
        calls.append(url.split("/models/")[1].split(":")[0])
        if len(calls) == 1:
            return 429, b'{"error": {"details": [{"retryDelay": "12s"}]}}'
        if len(calls) == 2:
            return 503, b"overloaded"
        return 200, json.dumps({"candidates": [{"content": {"parts": [{"functionCall": {"name": "done", "args": {}}}]}}]}).encode()

    out = B.call(body, "k", ("m1", "m2"), transport=transport, sleep=slept.append)
    assert out["_model"] == "m2" and calls == ["m1", "m2", "m2"] and slept == [2.0], (calls, slept)
    # Dernier modèle en 429 de la minute : là seulement on attend le délai demandé.
    calls.clear()
    slept.clear()
    out = B.call(body, "k", ("m3",), transport=transport, sleep=slept.append)
    assert out["_model"] == "m3" and slept == [12.0, 5.0], (calls, slept)
    day = b'{"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}'
    seen, slept2 = [], []

    def daily(url, headers, req, timeout):
        m = url.split("/models/")[1].split(":")[0]
        seen.append(m)
        if m == "d1":
            return 429, day
        return 200, json.dumps({"candidates": [{"content": {"parts": [{"functionCall": {"name": "done", "args": {}}}]}}]}).encode()

    assert B.call(body, "k", ("d1", "d2"), transport=daily, sleep=slept2.append)["_model"] == "d2"
    assert seen == ["d1", "d2"] and slept2 == [], "quota du jour : modèle suivant tout de suite, sans attendre"
    seen.clear()
    B.call(body, "k", ("d1", "d2"), transport=daily, sleep=slept2.append)
    assert seen == ["d2"], "le modèle épuisé n'est plus appelé de la journée"
    B._spent.pop("d1", None)
    assert B.MODELS[0].endswith("flash-lite") and B.MODELS[-1] == "gemini-3.5-flash", "le modèle à 20/jour en dernier"
    try:
        B.call(body, "k", ("m1", "m2"), transport=lambda *a: (403, b"denied"), sleep=slept.append)
        raise AssertionError("clé refusée acceptée")
    except B.BrainError as e:
        assert "403" in str(e)
    return "429 minute : modèle suivant tout de suite (attente seulement sur le dernier) ; 429 jour : écarté jusqu à minuit ; 403 : arrêt"


class _Inputs:
    def __init__(self):
        self.done = []

    def click(self, x, y, button="left", double=False):
        self.done.append(("click", x, y))
        return "clic"

    def type_text(self, text):
        self.done.append(("type", text))
        return "tapé"

    def keys(self, combo):
        self.done.append(("keys", combo))
        return f"touches {combo}"

    def scroll(self, x, y, direction, amount=5):
        self.done.append(("scroll", direction))
        return "défilé"


def _obs(elements, title="Conversation avec Paul | Microsoft Teams", pid=1):
    return Mo.Observation(Mo.Window(title, "ms-teams.exe", pid, (0, 0, 1000, 1000)), ["Autre"],
                          Mo.keep(elements, (0, 0, 1000, 1000)), (0, 0, 1000, 1000), b"", "fake")


def _agent(script, obs, confirm_answers, inputs=None, acted=None):
    answers = list(confirm_answers)
    asked = []

    class _Brain:
        def step(self, body):
            name, args = script.pop(0)
            return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args}}]}}]}

    def act(el, kind, value=""):
        (acted if acted is not None else []).append((el.name, kind))
        return None                           # l'accessibilité ne suffit pas : le clic prend le relais

    ag = A.Agent(_Brain(), confirm=lambda t: (asked.append(t), answers.pop(0) if answers else False)[1],
                 log=lambda m: None, observe=lambda: obs, act=act, inputs=inputs or _Inputs(),
                 discover=D, sleep=lambda s: None)
    ag.focus_window = lambda t: False
    return ag, asked


@check("5 garde : « Envoyer » demande confirmation, avec le texte ; refus → rien n'est cliqué, pas de nouvelle demande")
def _guard():
    els = [Mo.Element(0, "edit", "Tapez un message", rect=(100, 900, 800, 940)),
           Mo.Element(0, "button", "Envoyer", rect=(850, 900, 900, 940))]
    obs = _obs(els)
    inputs = _Inputs()
    script = [("type_text", {"element": 1, "text": "Bonjour Paul, je serai en retard"}), ("click", {"element": 2}),
              ("click", {"element": 2}), ("fail", {"reason": "Pas envoyé : tu as refusé."})]
    ag, asked = _agent(script, obs, [False], inputs)
    r = ag.run("envoie à Paul : je serai en retard")
    assert r.outcome == "fail" and len(asked) == 1, (r, asked)
    assert "Envoyer" in asked[0] and "Bonjour Paul, je serai en retard" in asked[0], asked[0]
    assert ("click", 875, 920) not in inputs.done, "refusé : le bouton n'a pas été cliqué"
    assert "REFUSED" in ag.history[1] and "REFUSED" in ag.history[2], ag.history
    # Accepté : le clic part.
    inputs2 = _Inputs()
    script = [("type_text", {"element": 1, "text": "ok"}), ("click", {"element": 2}), ("done", {"summary": "Envoyé."})]
    ag, asked = _agent(script, _obs(els), [True], inputs2)
    assert ag.run("envoie ok").outcome == "done" and ("click", 875, 920) in inputs2.done
    return "texte exact montré ; refus = aucun clic et aucune nouvelle demande ; accord = clic"


@check("6 garde : Entrée après un message demande, pas après une recherche ; submit demande ; mot de passe refusé")
def _guard_keys():
    compose = _obs([Mo.Element(0, "edit", "Tapez un nouveau message", rect=(100, 900, 800, 940))])
    ag, asked = _agent([("type_text", {"element": 1, "text": "Coucou"}), ("press_keys", {"keys": "enter"}),
                        ("done", {"summary": "x"})], compose, [False])
    ag.run("dis coucou")
    assert len(asked) == 1 and "Coucou" in asked[0], asked
    search = _obs([Mo.Element(0, "edit", "Rechercher", rect=(100, 50, 800, 90))])
    ag, asked = _agent([("type_text", {"element": 1, "text": "Paul"}), ("press_keys", {"keys": "enter"}),
                        ("done", {"summary": "x"})], search, [])
    ag.run("cherche Paul")
    assert asked == [], "une recherche ne demande rien"
    ag, asked = _agent([("type_text", {"text": "vu", "submit": True}), ("done", {"summary": "x"})], search, [False])
    ag.run("x")
    assert len(asked) == 1 and "valider" in asked[0]
    pwd = _obs([Mo.Element(0, "edit", "Mot de passe", rect=(100, 50, 800, 90))])
    inputs = _Inputs()
    ag, asked = _agent([("type_text", {"element": 1, "text": "hunter2"}), ("done", {"summary": "x"})], pwd, [], inputs)
    ag.run("connecte-toi")
    assert ("type", "hunter2") not in inputs.done and "mot de passe" in ag.history[0]
    return "Entrée après « Tapez un nouveau message » : demandé ; après « Rechercher » : non ; mot de passe : refusé"


@check("7 jamais d'écrasement : un document déjà rempli reçoit le texte à la fin (Ctrl+Fin), pas un remplacement")
def _no_replace():
    doc = _obs([Mo.Element(0, "document", "Éditeur de texte", value="Contenu de l'utilisateur", rect=(0, 100, 1000, 900))],
               title="notes.txt – Bloc-notes")
    inputs, acted = _Inputs(), []
    ag, _ = _agent([("type_text", {"element": 1, "text": "ajout"}), ("done", {"summary": "x"})], doc, [], inputs, acted)
    ag.run("ajoute une ligne")
    assert ("keys", "ctrl+end") in inputs.done and ("type", "ajout") in inputs.done, inputs.done
    assert not any(kind == "set_text" for _, kind in acted), "aucun SetValue sur un document rempli"
    return "Ctrl+Fin puis frappe ; aucun SetValue"


@check("8 HUD de Matthew au premier plan : la fenêtre de travail repasse devant ; écran verrouillé : arrêt")
def _own_window():
    import os
    calls = []
    hud = _obs([], title="Matthew", pid=os.getpid())
    work = _obs([], title="Teams")
    seq = [hud, work]
    ag, _ = _agent([("done", {"summary": "x"})], work, [])
    ag.observe = lambda: seq.pop(0) if seq else work
    ag.focus_window = lambda t: (calls.append(t), True)[1]
    ag.run("x")
    assert calls == ["Autre"], calls
    locked = Mo.Observation(Mo.Window(), [], [], (0, 0, 0, 0), b"", "fake", ["écran verrouillé"], locked=True)
    ag, _ = _agent([("click", {"x": 1, "y": 1})], locked, [])
    r = ag.run("x")
    assert r.outcome == "fail" and "verrouillé" in r.message and not ag.history, "écran verrouillé : aucun appel au modèle"
    return "focus sur « Autre » (la fenêtre sous le HUD) ; écran verrouillé : arrêt sans appel au modèle"


@check("9 recettes : retrouvées par les mots, jamais le texte tapé")
def _recipes():
    with tempfile.TemporaryDirectory() as tmp:
        store = R.Recipes(Path(tmp) / "r.json")
        els = [Mo.Element(0, "edit", "Rechercher", rect=(0, 0, 100, 20))]
        ag, _ = _agent([("find_apps", {"query": "teams"}), ("type_text", {"element": 1, "text": "secret perso"}),
                        ("done", {"summary": "ok"})], _obs(els), [])
        ag.recipes = store
        ag.run("envoie un message à Paul sur Teams")
        hint = store.hints("envoie un message à Léa sur Teams")
        assert hint and "find_apps(query=" in hint[0] and "secret perso" not in hint[0], hint
        assert store.hints("quelle heure est-il") == []
    return "« Léa sur Teams » retrouve « Paul sur Teams » ; texte tapé absent"


@check("10 écrans multiples, Wayland : le bon écran ; sans outil, on le dit")
def _screen():
    mons = [{"left": 0, "top": 0, "width": 1920, "height": 1080}, {"left": 1920, "top": 0, "width": 2560, "height": 1440}]
    assert S.monitor_for((2000, 100, 3000, 900), mons) == (1920, 0, 4480, 1440)
    assert S.monitor_for((100, 100, 500, 500), mons) == (0, 0, 1920, 1080)
    import os
    from pilot import inputs as I
    old = dict(os.environ)
    try:
        os.environ["XDG_SESSION_TYPE"], os.environ["PATH"] = "wayland", ""
        if sys.platform.startswith("linux"):
            try:
                I.click(1, 1)
                raise AssertionError("Wayland sans outil : un clic a prétendu réussir")
            except I.InputUnavailable as e:
                assert "wdotool" in str(e)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return "fenêtre sur l'écran 2 → capture de l'écran 2 ; Wayland sans wdotool → InputUnavailable"


@check("11 hooks et tâches : main.py et client gardés, inerte sur Oracle, une tâche à la fois")
def _hooks():
    repo = ROOT.parent
    assert "import pilot\n            pilot.attach(self)\n        except Exception:\n            pass" in \
        (repo / "main.py").read_text(encoding="utf-8")
    app = (repo / "client_desktop" / "app.py").read_text(encoding="utf-8")
    assert "import pilot.client" in app and "Pilote indisponible" in app
    P._pilot = None
    P.attach(type("J", (), {"ui": type("U", (), {"local_mode": False})()})())
    assert P.get() is None, "sur Oracle, aucun pilote"
    import threading
    gate = threading.Event()

    class _Slow:
        def __init__(self, *a, **k):
            self.status = ""

        def run(self, task):
            gate.wait(5)
            return A.Result("done", "fait", ["1: x"])

        def stop(self):
            gate.set()

    reports = []
    p = P.Pilot(lambda: None, lambda t: True, lambda o, m: reports.append((o, m)), log=lambda m: None,
                agent_factory=_Slow)
    assert "m'en occupe" in p.start("fais x") and "déjà sur une tâche" in p.start("fais y")
    assert "En cours" in p.status() and p.stop() == "J'arrête."
    p._thread.join(5)
    assert reports == [("done", "fait")] and "Dernière tâche" in p.status()
    from client_desktop import device
    assert device._device_plugin_manifest(repo / "plugins" / "pilot.py")[0] == "pilot"
    return "hooks gardés ; une seule tâche ; stop ; compte rendu à la fin ; outil routé vers le PC"


@check("12 réel : l'arbre de la fenêtre active se lit (lecture seule)")
def _real_observe():
    try:
        from pilot import a11y
        obs = a11y.observe(with_image=False)
    except Exception as e:
        return f"sans objet : {type(e).__name__}: {e}"
    if not obs.window.title and not obs.elements:
        return f"sans objet : pas de bureau ({'; '.join(obs.notes) or obs.backend})"
    return f"{obs.backend} : « {obs.window.title[:40]} », {len(obs.elements)} éléments"


@check("13 réel : applications et schémas d'URL déclarés par ce poste (lecture seule)")
def _real_discover():
    apps, schemes = D.all_apps(), D.all_schemes()
    if not apps and not schemes:
        return "sans objet : ni applications ni schémas déclarés ici"
    return f"{len(apps)} applications, {len(schemes)} schémas d'URL"


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
