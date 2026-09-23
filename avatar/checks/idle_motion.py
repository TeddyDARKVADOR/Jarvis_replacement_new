"""Mesure le repos : est-ce que le personnage bouge vraiment, est-ce que l'etat
interieur change l'amplitude de ce mouvement — et est-ce que tout ca continue
quand PLUS AUCUNE decision n'arrive ?

Un idle ne se juge pas sur une image fixe. On echantillonne la rotation de la
tete pendant plusieurs secondes et on regarde l'amplitude.

LA SEPARATION QUE LA DERNIERE PHASE VERIFIE
    Deux choses distinctes animent ce visage, et elles ne doivent pas dependre
    l'une de l'autre :

        COMPORTEMENT   decide par JARVIS, quelques fois par minute
        VIE            clignements, saccades, respiration, derive — jamais
                       decidee, jamais transmise, generee sur l'horloge locale

    Si la vie s'arretait faute de decision, le modele finirait par devoir dire
    « maintenant je cligne des yeux » : un aller-retour reseau pour fermer une
    paupiere, et un visage qui ne bouge que quand le serveur parle — ce qui se
    lit immediatement comme une marionnette.

    La derniere phase coupe donc toute decision pendant douze secondes et compte
    ce qui se passe quand meme.
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

#: La vie, lue la ou elle est generee. `blinkPhase` vaut -1 hors clignement, et
#: la saccade porte sa cible courante — deux generateurs sur l'horloge locale,
#: qui ne recoivent rien de personne.
LIFE = """
(function () {
  const r = window.__rig;
  if (!r) return 'PAS_ENCORE';
  return JSON.stringify({
    blinking: r.blinkPhase >= 0,
    sx: r.saccade.x, sy: r.saccade.y,
    micro: Object.keys(window.__gestures.idle.shapes).length
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
        QTimer.singleShot(200, life_phase)
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


# ── la vie, quand plus rien n'est decide ─────────────────────────────────────

life = {"ticks": 0, "blinks": 0, "wasBlinking": False, "saccades": 0,
        "last": None, "micro": 0}


def life_phase():
    """Douze secondes sans une seule decision. Que se passe-t-il quand meme ?

    Rien n'est envoye : pas de `perform`, pas d'affect, pas de changement
    d'etat. Ce qui bouge ici bouge parce que le moteur le genere, ce qui est
    toute la separation entre COMPORTEMENT et VIE.
    """
    print(flush=True)
    print("  vie sans decision — 12 s, aucune performance envoyee", flush=True)
    life_sample()


def life_sample():
    widget._view.page().runJavaScript(LIFE, life_took)


def life_took(raw):
    if raw and raw != "PAS_ENCORE":
        data = json.loads(raw)
        if data["blinking"] and not life["wasBlinking"]:
            life["blinks"] += 1
        life["wasBlinking"] = data["blinking"]
        point = (round(data["sx"], 4), round(data["sy"], 4))
        if life["last"] is not None and point != life["last"]:
            life["saccades"] += 1
        life["last"] = point
        if data["micro"]:
            life["micro"] += 1
    life["ticks"] += 1
    if life["ticks"] < 240:            # ~12 s a 50 ms
        QTimer.singleShot(50, life_sample)
        return

    ok = life["blinks"] >= 2 and life["saccades"] >= 20
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {life['blinks']} clignements · "
          f"{life['saccades']} deplacements du regard · "
          f"micro-expressions sur {life['micro']}/{life['ticks']} images",
          flush=True)
    if not ok:
        print("         le visage s'arrete quand personne ne decide — "
              "le modele devrait alors commander ses propres clignements",
              flush=True)
        print(flush=True)
        app.exit(1)
        return
    print(flush=True)
    print("  Le comportement est decide, la vie ne l'est pas.", flush=True)
    app.exit(0)


def ready(raw):
    if json.loads(raw).get("pret"):
        QTimer.singleShot(300, start_case)
        return
    QTimer.singleShot(250, probe)


def probe():
    widget._view.page().runJavaScript(
        "JSON.stringify({pret: !!(window.JARVIS && window.JARVIS.ready)})", ready)


widget._view.loadFinished.connect(lambda ok: QTimer.singleShot(1200, probe))
QTimer.singleShot(90000, lambda: app.exit(1))
sys.exit(app.exec())
