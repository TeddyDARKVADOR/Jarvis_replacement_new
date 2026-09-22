"""Mesure le gel du corps : en mode visage, est-ce que ce qui est sous la nuque
tient vraiment en place — et est-ce que la tete, elle, continue de vivre ?

CE QUE CE CONTROLE PROUVE, ET QUE LES AUTRES NE PEUVENT PAS
    `presence/selftest.py` verifie que le vocabulaire retrecit : en mode visage,
    JARVIS ne se voit plus proposer `wave`. C'est la premiere moitie, et elle se
    verifie sans navigateur.

    La seconde moitie ne se verifie qu'ici. Un geste retire du vocabulaire n'est
    plus *demande*, mais quatre autres couches ecrivent encore sur les memes os
    — la posture, le regard, le repos, et le labo qui peut tout jouer a la main.
    Un gel qui n'attrape que les gestes laisserait le buste deriver et les
    hanches se balancer, ce qui est exactement ce qu'on a decide de ne pas
    faire.

    Donc on mesure les os, pas les intentions.

POURQUOI UN SEUL CHARGEMENT ET UN DRAPEAU BASCULE
    Le mode vient du manifeste, lu a la creation de la page. Comparer deux
    modes voudrait dire deux pages, deux modeles charges, deux bruits aleatoires
    differents — et un ecart qui ne prouverait plus grand-chose.

    On charge donc une fois et on bascule `faceOnly` en cours de route. Le
    corps, l'etat interieur et la graine du bruit sont les memes des deux cotes
    : le seul facteur qui change est celui qu'on teste.
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
from presence import Affect  # noqa: E402

app = QApplication(sys.argv)
widget = JarvisAvatarWidget()
widget.resize(360, 420)
widget.show()

#: Les canaux d'os, lus la ou ils sont ecrits : `smoothed` est ce que `_write`
#: applique, donc c'est la derniere valeur avant le modele.
#:
#: `arm` est lu sur le noeud lui-meme parce que la respiration des epaules ne
#: passe pas par `smoothed` — elle ecrit directement, et c'est la seule chose
#: sous la nuque qui a le droit de bouger en mode visage.
SAMPLE = """
(function () {
  const g = window.__gestures;
  if (!g) return 'PAS_ENCORE';
  const s = g.smoothed;
  const arm = g.nodes.armLeftUpper;
  return JSON.stringify({
    faceOnly: !!g.faceOnly,
    hx: s.headRx, hy: s.headRy, hz: s.headRz,
    spx: s.spineRx, spy: s.spineRy,
    ry: s.rootY, rz: s.rootZ, rry: s.rootRy,
    arm: arm ? arm.rotation.z : 0
  });
})()
"""

#: Agite volontairement : c'est l'etat ou le repos bouge le plus, donc celui ou
#: un gel qui ne marche pas se verrait le mieux. Geler un JARVIS deja immobile
#: ne prouverait rien.
AFFECT = Affect(valence=-0.2, arousal=0.9, attention=0.95, confidence=0.4, urgency=0.8)

CASES = [
    ("visage  ", True),
    ("complet ", False),
]
state = {"i": 0, "rows": [], "ticks": 0, "results": {}}


def start_case():
    if state["i"] >= len(CASES):
        verdict()
        return
    _, face_only = CASES[state["i"]]
    widget._view.page().runJavaScript(
        f"window.__gestures && (window.__gestures.faceOnly = {str(face_only).lower()})")
    widget._director.set_affect(AFFECT)
    widget._last_state = ""
    widget._push(widget._director.resolve("ACTIVE"))
    state["rows"] = []
    state["ticks"] = 0
    # Une seconde pour que le lissage a 90 ms ait fini de converger vers le
    # nouveau regime avant qu'on commence a mesurer.
    QTimer.singleShot(1400, sample)


def sample():
    widget._view.page().runJavaScript(SAMPLE, took)


def took(raw):
    if raw and raw != "PAS_ENCORE":
        state["rows"].append(json.loads(raw))
    state["ticks"] += 1
    if state["ticks"] < 110:           # ~11 s a 100 ms
        QTimer.singleShot(100, sample)
        return

    name, _ = CASES[state["i"]]
    rows = state["rows"]
    if not rows:
        print(f"  [FAIL] {name} aucun echantillon", flush=True)
        state["results"][name.strip()] = None
    else:
        def span(key):
            values = [r[key] for r in rows]
            return (max(values) - min(values)) * 180 / 3.14159

        head = max(span("hx"), span("hy"), span("hz"))
        body = max(span("spx"), span("spy"), span("ry"), span("rz"), span("rry"))
        arm = span("arm")
        state["results"][name.strip()] = {"head": head, "body": body, "arm": arm}
        print(f"  {name} tete {head:5.2f} deg  |  corps {body:5.2f} deg  "
              f"|  epaule {arm:4.2f} deg  |  faceOnly={rows[0]['faceOnly']}",
              flush=True)

    state["i"] += 1
    QTimer.singleShot(200, start_case)


def verdict():
    face = state["results"].get("visage")
    full = state["results"].get("complet")
    print(flush=True)
    if not face or not full:
        print("  [FAIL] mesures manquantes", flush=True)
        app.exit(1)
        return

    failures = []
    # Le seuil n'est pas zero : `smoothed` converge vers zero sans jamais
    # l'atteindre, et un flottant qui decroit exponentiellement laisse toujours
    # une queue. Un vingtieme de degre est en-dessous de ce qu'un ecran montre.
    if face["body"] > 0.05:
        failures.append(f"le corps bouge encore en mode visage ({face['body']:.2f} deg)")
    # Et le gel doit geler quelque chose : si le corps ne bougeait pas non plus
    # en mode complet, ce controle passerait sans rien mesurer du tout.
    if full["body"] < 0.20:
        failures.append(f"le corps ne bougeait pas non plus en mode complet "
                        f"({full['body']:.2f} deg) — ce controle ne prouve rien")
    if face["head"] < 0.50:
        failures.append(f"la tete s'est figee avec le corps ({face['head']:.2f} deg)")

    for line in failures:
        print(f"  [FAIL] {line}", flush=True)
    if failures:
        app.exit(1)
        return

    print(f"  [OK  ] corps {full['body']:.2f} deg -> {face['body']:.2f} deg, "
          f"tete vivante a {face['head']:.2f} deg", flush=True)
    print(flush=True)
    print("  Le corps est present et tenu au repos. Tout le mouvement est "
          "au-dessus de la nuque.", flush=True)
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
QTimer.singleShot(60000, lambda: app.exit(1))
sys.exit(app.exec())
