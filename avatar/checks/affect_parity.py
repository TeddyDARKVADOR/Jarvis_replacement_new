"""Equivalence Python / JavaScript de la derivation d'affect, mesuree.

Le selftest compare les TABLES (ancres, poids, seuils) parce qu'il sait les
lire. Il ne peut pas executer le JavaScript. Ce banc-ci fait tourner les deux
implementations cote a cote sur une grille de plusieurs milliers de points et
signale le moindre desaccord.

    python bench_affect_parity.py

C'est une verification ponctuelle, a relancer apres avoir touche l'une des deux
derivations — pas un garde-fou permanent.
"""
import json
import sys
from pathlib import Path

# Le projet, depuis ici : avatar/checks/x.py -> la racine.
BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer, QUrl  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from presence.affect import (  # noqa: E402
    Affect,
    SocialMode,
    expression_for,
    gaze_for,
    gaze_hold_for,
    intensity_for,
    posture_for,
    stillness_for,
    tempo_for,
)

# ── la grille ────────────────────────────────────────────────────────────────
VALENCES = [-1.0, -0.72, -0.4, -0.1, 0.0, 0.1, 0.45, 0.6, 0.85, 1.0]
LEVELS = [0.0, 0.04, 0.12, 0.3, 0.45, 0.55, 0.62, 0.75, 0.9, 1.0]
URGENCIES = [0.0, 0.25, 0.55, 0.6, 0.9]
MODES = [m.value for m in SocialMode]

points = []
for valence in VALENCES:
    for arousal in LEVELS:
        for attention in LEVELS:
            for confidence in (0.12, 0.5, 0.55, 0.9):
                for urgency in URGENCIES:
                    points.append({
                        "valence": valence, "arousal": arousal,
                        "attention": attention, "confidence": confidence,
                        "urgency": urgency,
                        "social_mode": MODES[len(points) % len(MODES)],
                    })

expected = []
for p in points:
    affect = Affect(valence=p["valence"], arousal=p["arousal"],
                    attention=p["attention"], confidence=p["confidence"],
                    urgency=p["urgency"], social_mode=SocialMode(p["social_mode"]))
    expected.append({
        "expression": expression_for(affect).value,
        "intensity": intensity_for(affect),
        "gaze": gaze_for(affect).value,
        "posture": posture_for(affect).value,
        "tempo": tempo_for(affect),
        "stillness": stillness_for(affect),
        "gaze_hold_s": gaze_hold_for(affect),
    })

print(f"  {len(points)} points de l'espace affectif", flush=True)

# ── la meme grille, cote JavaScript ──────────────────────────────────────────
PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script type="importmap">{"imports":{"three":"./vendor/three.module.min.js"}}</script>
</head><body><script type="module">
import { deriveFrom } from './js/affect.js';
window.__derive = (points) => points.map(p => {
  const d = deriveFrom(p);
  return {
    expression: d.expression, intensity: d.intensity, gaze: d.gaze,
    posture: d.posture, tempo: d.tempo, stillness: d.stillness,
    gaze_hold_s: d.gaze_hold_s,
  };
});
window.__ready = true;
</script></body></html>"""

(BASE / "avatar" / "_parity.html").write_text(PAGE, encoding="utf-8")

app = QApplication(sys.argv)
view = QWebEngineView()
page = view.page()
avatar_scheme.install(page.profile())
view.resize(200, 200)
view.show()

result = {"rows": None}


def compare(raw):
    if not raw:
        print("  ECHEC : le JavaScript n'a rien rendu", flush=True)
        app.quit()
        return
    got = json.loads(raw)
    if len(got) != len(expected):
        print(f"  ECHEC : {len(got)} lignes contre {len(expected)}", flush=True)
        app.quit()
        return

    mismatches = []
    for index, (want, have) in enumerate(zip(expected, got)):
        for key in want:
            a, b = want[key], have[key]
            same = abs(a - b) < 1e-6 if isinstance(a, (int, float)) else a == b
            if not same:
                mismatches.append((index, key, a, b))

    if mismatches:
        print(f"\n  {len(mismatches)} DESACCORDS sur {len(expected) * 7} comparaisons\n", flush=True)
        for index, key, a, b in mismatches[:12]:
            print(f"    point {index} {points[index]}", flush=True)
            print(f"      {key} : python={a!r}  js={b!r}", flush=True)
        if len(mismatches) > 12:
            print(f"    ... et {len(mismatches) - 12} autres", flush=True)
    else:
        print(f"  {len(expected) * 7} comparaisons, aucun desaccord.", flush=True)
        print("  Python et JavaScript derivent le meme comportement.", flush=True)
    app.quit()


tries = {"n": 0}


def probe():
    page.runJavaScript("!!window.__ready", ready)


def ready(ok):
    tries["n"] += 1
    if ok:
        payload = json.dumps(points, separators=(",", ":"))
        page.runJavaScript(f"JSON.stringify(window.__derive({payload}))", compare)
        return
    if tries["n"] > 40:
        print("  ECHEC : la page n'a jamais demarre", flush=True)
        app.quit()
        return
    QTimer.singleShot(250, probe)


view.loadFinished.connect(lambda ok: QTimer.singleShot(400, probe))
view.setUrl(QUrl("jarvis://avatar/_parity.html"))
QTimer.singleShot(90000, app.quit)
app.exec()

(BASE / "avatar" / "_parity.html").unlink(missing_ok=True)
