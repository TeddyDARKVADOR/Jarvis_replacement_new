"""Le labo joue-t-il ses situations quotidiennes, et dit-il vrai en les jouant ?

    python avatar/checks/lab_situations.py        (demande PyQt6-WebEngine)

`avatar/checks/situations.mjs` joue les situations de `avatar/js/situations.js`
sous Node et mesure leur signal. Celui-ci clique leurs boutons dans le VRAI
labo, sur le modele installe, et verifie trois choses a la fin de chacune :

    aucune erreur JavaScript             la page ne ment pas en silence
    la ligne « Pourquoi » est remplie     et elle dit ce que le moteur a FAIT :
                                          le geste qu'elle nomme est celui que
                                          `engine.decision` rapporte
    la sortie a bouge                     des formes ont ete ecrites, la tete
                                          a tourne — la situation a joue

La console est captee en JavaScript, pour la meme raison que `behaviour.py`.
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

#: Celles qui se jouent en quelques secondes ; « il ne se passe rien » dure une
#: minute et n'apprend rien de plus ici que sous Node.
PLAYED = [
    "quelqu'un arrive", "resultat inattendu", "besoin d'une confirmation", "avertissement",
    "l'utilisateur remercie", "l'utilisateur plaisante", "JARVIS reflechit",
    "JARVIS est interrompu", "tache reussie", "tache echouee",
]

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

state = {"i": 0, "problems": 0}

READ = """
JSON.stringify({
  why: document.getElementById('trace-why').innerText,
  expect: document.getElementById('situation-expect').innerText,
  gesture: window.__lab.engine.decision && window.__lab.engine.decision.gesture,
  played: window.__lab.engine.decision && window.__lab.engine.decision.gesture_played,
  shapes: Object.keys(window.__lab.engine.output().shapes).length,
  head: Math.abs(window.__lab.engine.gestures.smoothed.headRx)
      + Math.abs(window.__lab.engine.gestures.smoothed.headRy),
  log: window.__log.splice(0),
})
"""


def next_case():
    if state["i"] >= len(PLAYED):
        print(f"\n  {len(PLAYED)} situations cliquees dans le vrai labo, {state['problems']} probleme(s)")
        if not state["problems"]:
            print("  Le labo joue ses situations, et sa ligne « Pourquoi » dit ce que le moteur a fait.")
        app.exit(1 if state["problems"] else 0)
        return
    name = PLAYED[state["i"]]
    page.runJavaScript(
        "[...document.getElementById('situations').children]"
        f".find((b) => b.dataset.value === {json.dumps(name)}).click(); 0")
    QTimer.singleShot(4200, lambda: page.runJavaScript(READ, lambda raw: judge(name, raw)))


def judge(name, raw):
    r = json.loads(raw or "{}")
    problems = []
    if r.get("log"):
        problems.append(f"console : {r['log'][:2]}")
    why = r.get("why") or ""
    if "WHY" not in why or "OUTPUT" not in why:
        problems.append("la ligne « Pourquoi » est vide")
    head = next((line for line in why.splitlines() if line.startswith("HEAD")), "")
    if r.get("gesture") and r["gesture"] not in head:
        problems.append(f"« Pourquoi » dit {head!r}, le moteur a joue {r['gesture']}")
    if r.get("shapes", 0) == 0 and r.get("head", 0) < 1e-3:
        problems.append("rien n'a bouge")
    if not (r.get("expect") or "").startswith("attendu"):
        problems.append("l'attendu de la situation n'est pas affiche")
    mark = "OK  " if not problems else "FAUX"
    print(f"  [{mark}] {name:<28} {head[:70]}", flush=True)
    for p in problems:
        print(f"         {p}")
    state["problems"] += len(problems)
    state["i"] += 1
    next_case()


def probe():
    page.runJavaScript(
        "JSON.stringify(!!(window.__lab && window.__lab.engine && window.__lab.performance"
        " && document.getElementById('situations').children.length))",
        lambda raw: next_case() if raw == "true" else QTimer.singleShot(300, probe))


view.loadFinished.connect(lambda ok: probe())
view.setUrl(QUrl("jarvis://avatar/lab.html"))
QTimer.singleShot(180000, lambda: app.exit(1))
sys.exit(app.exec())
