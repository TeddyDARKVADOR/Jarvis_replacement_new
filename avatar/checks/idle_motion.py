"""Mesure le repos : est-ce que le personnage bouge vraiment, et est-ce que
l'etat interieur change l'amplitude de ce mouvement ?

Un idle ne se juge pas sur une image fixe. On echantillonne la rotation de la
tete pendant plusieurs secondes et on regarde l'amplitude.
"""
import json
import sys
from pathlib import Path

# Le projet, depuis ici : avatar/checks/x.py -> la racine.
BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from client_desktop.ui.avatar_view import JarvisAvatarWidget  # noqa: E402
from presence import Affect, SocialMode  # noqa: E402

app = QApplication(sys.argv)
widget = JarvisAvatarWidget()
widget.resize(360, 420)
widget.show()

SAMPLE = """
(function () {
  const g = window.__gestures;
  if (!g) return 'PAS_ENCORE';
  const s = g.smoothed;
  return JSON.stringify({
    hx: s.headRx, hy: s.headRy, hz: s.headRz,
    ry: s.rootY, micro: Object.keys(g.idle.shapes).length,
    stillness: g.idle.stillness, tempo: g.tempo
  });
})()
"""

CASES = [
    ("calme    ", Affect(valence=0.1, arousal=0.08, attention=0.85, confidence=0.92)),
    ("agite    ", Affect(valence=-0.3, arousal=0.9, attention=0.95, confidence=0.35, urgency=0.85)),
]
state = {"i": 0, "samples": [], "ticks": 0, "microSeen": 0}


def start_case():
    if state["i"] >= len(CASES):
        app.quit()
        return
    name, affect = CASES[state["i"]]
    widget._director.set_affect(affect)
    widget._last_state = ""
    widget._push(widget._director.resolve("ACTIVE"))   # geste idle, aucune transition
    state["samples"] = []
    state["ticks"] = 0
    state["microSeen"] = 0
    QTimer.singleShot(2600, sample)


def sample():
    widget._view.page().runJavaScript(SAMPLE, took)


def took(raw):
    if raw and raw != "PAS_ENCORE":
        data = json.loads(raw)
        state["samples"].append(data)
        if data["micro"]:
            state["microSeen"] += 1
    state["ticks"] += 1
    if state["ticks"] < 90:            # ~9 s a 100 ms
        QTimer.singleShot(100, sample)
        return

    name, _ = CASES[state["i"]]
    rows = state["samples"]
    if not rows:
        print(f"  {name} aucun echantillon", flush=True)
    else:
        def span(key):
            values = [r[key] for r in rows]
            return (max(values) - min(values)) * 180 / 3.14159

        print(f"  {name} amplitude tete : Rx {span('hx'):.2f} deg  "
              f"Ry {span('hy'):.2f} deg  Rz {span('hz'):.2f} deg  "
              f"| immobilite {rows[0]['stillness']:.2f} tempo {rows[0]['tempo']:.2f} "
              f"| micro-expressions sur {state['microSeen']}/{len(rows)} images",
              flush=True)
    state["i"] += 1
    QTimer.singleShot(200, start_case)


def ready(raw):
    if json.loads(raw).get("pret"):
        QTimer.singleShot(300, start_case)
        return
    QTimer.singleShot(250, probe)


def probe():
    widget._view.page().runJavaScript(
        "JSON.stringify({pret: !!(window.JARVIS && window.JARVIS.ready)})", ready)


widget._view.loadFinished.connect(lambda ok: QTimer.singleShot(1200, probe))
QTimer.singleShot(45000, app.quit)
app.exec()
