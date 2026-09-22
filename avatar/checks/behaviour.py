"""Banc d'essai du labo comportemental.

Decrit des situations, pose des etats interieurs, et verifie que la decision en
DECOULE — et que la trace affichee dit la meme chose que l'etat interne.

La console du navigateur est captee en JavaScript : surcharger
QWebEnginePage.javaScriptConsoleMessage fait tomber le processus en PyQt6 sur
cette machine. Le produit ne la surcharge pas.
"""
import json
import sys
from pathlib import Path

# Le projet, depuis ici : avatar/checks/x.py -> la racine.
BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer, QUrl, Qt  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from presence import Affect, SocialMode  # noqa: E402
from presence.affect import (  # noqa: E402
    expression_for, gaze_for, intensity_for, posture_for, stillness_for, tempo_for,
)

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

view.resize(1400, 820)
view.show()

# ── les situations ───────────────────────────────────────────────────────────
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
step = {"i": 0, "bad": 0}


def tick():
    i = step["i"]
    if i >= len(SITUATIONS):
        page.runJavaScript("JSON.stringify(window.__log)", drain)
        return

    label, affect = SITUATIONS[i]
    payload = json.dumps({
        "emotion": {"valence": affect.valence, "arousal": affect.arousal},
        "attention": affect.attention,
        "confidence": affect.confidence,
        "urgency": affect.urgency,
        "socialMode": affect.social_mode.value,
        "reason": label,
    })
    page.runJavaScript(
        f"document.getElementById('json').value = {json.dumps(payload)};"
        "document.getElementById('send').click(); 0")

    def after():
        page.runJavaScript(
            "JSON.stringify({"
            " decision: window.__lab.decision,"
            " forced: !!window.__lab.forced,"
            " situation: document.getElementById('situation-line').textContent,"
            " trace: document.getElementById('trace-decision').textContent,"
            " exec: document.getElementById('trace-exec').textContent})",
            lambda raw: compare(i, label, affect, raw))
    QTimer.singleShot(700, after)


def compare(i, label, affect, raw):
    data = json.loads(raw)
    d = data["decision"]

    want = {
        "expression": expression_for(affect).value,
        "gaze": gaze_for(affect).value,
        "posture": posture_for(affect).value,
        "intensity": intensity_for(affect),
        "tempo": tempo_for(affect),
        "stillness": stillness_for(affect),
    }
    problems = []
    for field, expected in want.items():
        got = d[field]
        same = abs(got - expected) < 1e-9 if isinstance(expected, float) else got == expected
        if not same:
            problems.append(f"{field}: labo={got!r} python={expected!r}")
    if label not in data["situation"]:
        problems.append("la situation n'est pas affichee")
    if data["forced"]:
        problems.append("le labo a force un visage au lieu de le deriver")

    mark = "OK  " if not problems else "FAUX"
    print(f"  [{mark}] {label[:44]:<44} -> {d['expression']:<10} "
          f"{d['intensity']:.2f}  regard {d['gaze']:<7} posture {d['posture']:<9} "
          f"tempo {d['tempo']:.2f} immob {d['stillness']:.2f}", flush=True)
    for problem in problems:
        print(f"         {problem}", flush=True)
        step["bad"] += 1

    step["i"] += 1
    QTimer.singleShot(120, tick)


def drain(raw):
    lines = json.loads(raw or "[]")
    if lines:
        print(f"\n  PROBLEMES JS ({len(lines)}) :", flush=True)
        for line in lines:
            print(f"    {line}", flush=True)
    else:
        print("\n  aucune erreur ni avertissement JavaScript", flush=True)
    if step["bad"]:
        print(f"  {step['bad']} DESACCORDS entre le labo et Python", flush=True)
    else:
        print("  le labo derive exactement ce que Python derive", flush=True)
    app.quit()


tries = {"n": 0}


def probe():
    page.runJavaScript(
        "JSON.stringify({pret: !!(window.__lab && window.__lab.decision),"
        " curseurs: document.getElementById('shapes').children.length,"
        " axes: document.getElementById('affect').children.length,"
        " gestes: document.getElementById('gestures').children.length,"
        " rapport: (document.getElementById('report').textContent||'').split('\\n')[0]})",
        ready)


def ready(raw):
    data = json.loads(raw)
    tries["n"] += 1
    if data["pret"]:
        print(f"  modele    : {data['rapport']}", flush=True)
        print(f"  {data['axes']} axes · {data['curseurs']} formes · "
              f"{data['gestes']} gestes\n", flush=True)
        QTimer.singleShot(400, tick)
        return
    if tries["n"] > 60:
        page.runJavaScript("JSON.stringify(window.__log)", drain)
        return
    QTimer.singleShot(250, probe)


view.loadFinished.connect(lambda ok: (print(f"  page      : {ok}", flush=True), probe()))
view.setUrl(QUrl("jarvis://avatar/lab.html"))
QTimer.singleShot(60000, app.quit)
app.exec()
