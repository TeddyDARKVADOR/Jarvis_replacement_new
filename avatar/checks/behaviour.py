"""Banc d'essai du labo comportemental : dit-il la verite sur le modele installe ?

Pilote `avatar/lab.html` comme une personne le ferait — la demande collee dans
le champ, le bouton « Exécuter » — et confronte trois choses au Python :

    la DECISION     la Performance que le labo a resolue (miroir de
                    `presence/director.py`) contre celle du vrai directeur, sur
                    le catalogue du modele reellement installe
    le REPLI        le geste JOUE apres `FALLBACK_CHAIN`, pas le geste demande.
                    C'est ce que l'ancien labo ratait : `greet` y affichait
                    `wave -> idle` pendant que le panneau hochait la tete
    la SORTIE       ce que le moteur rapporte avoir fait, relu dans le moteur :
                    le geste a-t-il joue ou ete gele, l'accent a-t-il joue

`director_parity.py` couvre 2 112 decisions sous Node ; celui-ci verifie que la
PAGE, avec son vrai modele et ses vrais boutons, fait la meme chose.

La console du navigateur est captee en JavaScript : surcharger
QWebEnginePage.javaScriptConsoleMessage fait tomber le processus en PyQt6 sur
cette machine. Le produit ne la surcharge pas.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer, QUrl, Qt  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from presence import Affect, Intent, SocialMode  # noqa: E402
from presence.catalog import catalogue  # noqa: E402
from presence.director import Director, parse  # noqa: E402

CAPTURE = """
window.__log = [];
(function () {
  for (const level of ['warn', 'error']) {
    const original = console[level];
    console[level] = function (...a) {
      window.__log.push(level.toUpperCase() + ' ' + a.map(String).join(' '));
      original.apply(console, a);
    };
  }
  window.addEventListener('error', (e) => window.__log.push('ERREUR ' + e.message));
  window.addEventListener('unhandledrejection',
    (e) => window.__log.push('REJET ' + (e.reason && e.reason.message || e.reason)));
})();
"""

app = QApplication(sys.argv)
view = QWebEngineView()
page = view.page()
page.setBackgroundColor(Qt.GlobalColor.black)
avatar_scheme.install(page.profile())

script = QWebEngineScript()
script.setName("capture")
script.setSourceCode(CAPTURE)
script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
page.scripts().insert(script)

view.resize(1400, 860)
view.show()

CAT = catalogue(force=True)


def affect_form(label, a: Affect) -> dict:
    return {"emotion": {"valence": a.valence, "arousal": a.arousal},
            "attention": a.attention, "confidence": a.confidence,
            "urgency": a.urgency, "socialMode": a.social_mode.value, "reason": label}


SITUATIONS = [
    ("j'ai trouve quelque chose d'interessant",
     Affect(valence=0.35, arousal=0.55, attention=0.92, confidence=0.70)),
    ("la sauvegarde a echoue",
     Affect(valence=-0.55, arousal=0.68, attention=0.95, confidence=0.45, urgency=0.75)),
    ("il relance la meme commande pour la troisieme fois",
     Affect(valence=0.45, arousal=0.30, attention=0.88, confidence=0.85,
            social_mode=SocialMode.CASUAL)),
    ("je ne comprends pas la demande",
     Affect(valence=-0.15, arousal=0.45, attention=0.80, confidence=0.10)),
    ("confirmer la suppression de 12 fichiers",
     Affect(valence=-0.05, arousal=0.50, attention=0.98, confidence=0.92,
            urgency=0.50, social_mode=SocialMode.FORMAL)),
    ("session de sept heures, plus rien a faire",
     Affect(valence=-0.20, arousal=0.05, attention=0.20, confidence=0.60)),
]

CASES = ([(label, affect_form(label, a)) for label, a in SITUATIONS]
         + [(f"intent {i.value}", {"intent": i.value}) for i in Intent]
         + [("intent investigate + regard user", {"intent": "investigate", "gaze": "user"}),
            ("intent warn + regard user", {"intent": "warn", "gaze": "user"}),
            ("visage force angry 1.0", {"expression": "angry", "intensity": 1.0}),
            ("visage force serious, sans posture", {"expression": "serious", "intensity": 0.8})])

FIELDS = ("expression", "gaze", "gaze_source", "posture", "gesture",
          "requested_gesture", "accent", "intent")

step = {"i": 0, "bad": 0}


def expected(request: dict) -> dict:
    director = Director(CAT)
    director.set_intent(parse(json.dumps(request)), now=0.0)
    return director.resolve("ACTIVE", now=0.0).as_json()


def tick():
    i = step["i"]
    if i >= len(CASES):
        page.runJavaScript("JSON.stringify(window.__log)", drain)
        return
    label, request = CASES[i]
    payload = json.dumps(request)
    page.runJavaScript(
        "document.getElementById('clear').click();"
        f"document.getElementById('json').value = {json.dumps(payload)};"
        "document.getElementById('send').click(); 0")

    def after():
        page.runJavaScript(
            "JSON.stringify({perf: window.__lab.performance,"
            " decision: window.__lab.engine.decision,"
            " exec: document.getElementById('trace-exec').textContent,"
            " shown: document.getElementById('trace-decision').textContent})",
            lambda raw: compare(label, request, raw))
    QTimer.singleShot(700, after)


def compare(label, request, raw):
    data = json.loads(raw)
    perf = data["perf"]
    want = expected(request)
    problems = []
    for field in FIELDS:
        if perf.get(field) != want.get(field):
            problems.append(f"{field}: labo={perf.get(field)!r} python={want.get(field)!r}")
    for field in ("intensity", "tempo", "stillness"):
        if abs(perf[field] - want[field]) > 1.5e-3:
            problems.append(f"{field}: labo={perf[field]} python={want[field]}")

    # La sortie : le geste a-t-il REELLEMENT joue, sur ce corps ?
    d = data["decision"]
    if d["gesture"] != want["gesture"]:
        problems.append(f"le moteur a recu {d['gesture']} au lieu de {want['gesture']}")
    if d["gesture_played"] not in ("procedural", "clip", "deja en cours"):
        problems.append(f"geste {d['gesture']} : {d['gesture_played']}")
    if want.get("accent") and not d["accent_played"]:
        problems.append(f"accent {want['accent']} non joue")
    if want.get("requested_gesture") and "→" not in data["shown"]:
        problems.append("le repli n'est pas affiche")

    mark = "OK  " if not problems else "FAUX"
    fallback = (f"{want['requested_gesture']} -> " if want.get("requested_gesture") else "")
    print(f"  [{mark}] {label[:40]:<40} {perf['expression']:<10} {perf['intensity']:.2f} "
          f"regard {perf['gaze']:<6} ({perf['gaze_source']:<8}) geste {fallback}{perf['gesture']}"
          + (f" +{perf['accent']}" if perf.get("accent") else ""), flush=True)
    for problem in problems:
        print(f"         {problem}", flush=True)
        step["bad"] += 1

    step["i"] += 1
    QTimer.singleShot(90, tick)


def drain(raw):
    lines = json.loads(raw or "[]")
    if lines:
        print(f"\n  PROBLEMES JS ({len(lines)}) :", flush=True)
        for line in lines:
            print(f"    {line}", flush=True)
    else:
        print("\n  aucune erreur ni avertissement JavaScript", flush=True)
    if step["bad"] or lines:
        print(f"  {step['bad']} DESACCORDS entre le labo et Python", flush=True)
        app.exit(1)
        return
    print("  le labo decide et joue exactement ce que le panneau jouerait", flush=True)
    app.exit(0)


tries = {"n": 0}


def probe():
    page.runJavaScript(
        "JSON.stringify({pret: !!(window.__lab && window.__lab.performance && window.__lab.engine),"
        " rapport: (document.getElementById('report').textContent||'').split('\\n').slice(0, 12).join(' | ')})",
        ready)


def ready(raw):
    data = json.loads(raw)
    tries["n"] += 1
    if data["pret"]:
        print(f"  modele : {data['rapport']}", flush=True)
        print(f"  catalogue Python : motion={CAT.motion}, {len(CAT.vocabulary)} gestes\n", flush=True)
        QTimer.singleShot(400, tick)
        return
    if tries["n"] > 80:
        page.runJavaScript("JSON.stringify(window.__log)", drain)
        return
    QTimer.singleShot(250, probe)


view.loadFinished.connect(lambda ok: (print(f"  page : {ok}", flush=True), probe()))
view.setUrl(QUrl("jarvis://avatar/lab.html"))
QTimer.singleShot(120000, lambda: app.exit(1))
sys.exit(app.exec())
