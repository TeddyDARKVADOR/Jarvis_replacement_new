"""Mesure le TEMPS d'un visage, sur les coefficients reellement ecrits.

CE QUE CE CONTROLE PROUVE, ET QUE LES AUTRES NE PEUVENT PAS
    `presence/selftest.py` prouve que les douze visages sont distincts et que
    leurs 52 coefficients sont justes. C'est une verite sur une image fixe, et
    elle etait deja acquise avant que ce fichier existe.

    La question ici est la suivante : entre l'instant ou le directeur envoie un
    visage et l'instant ou il est installe, QUE SE PASSE-T-IL ? Un systeme peut
    avoir 52/52 formes correctes et changer d'expression comme un interrupteur,
    parce que les 52 arrivent toutes en meme temps. Un visage humain ne fait pas
    ca : l'ironie monte dans les yeux avant d'atteindre la bouche.

    Donc on n'echantillonne pas une cible. On intercepte l'ECRITURE — la
    fonction que `rig.js` appelle pour poser une valeur sur le modele — et on
    enregistre chaque image.

LES CINQ QUESTIONS
    composante perdue      chaque forme demandee arrive-t-elle vraiment ?
    composante ecrasee     une couche en mange-t-elle une autre ?
    snap                   une forme saute-t-elle d'une image a l'autre ?
    decalage reel          `amused` compose-t-il vraiment, et `surprised` non ?
    retour                 `hold_s` relache-t-il, ou tient-il pour toujours ?

POURQUOI LES PAUPIERES SONT EXCLUES DU TEST DE SNAP
    Un clignement EST un saut, et c'est voulu : `rig.js` ecrit les paupieres
    apres le lissage, parce qu'un clignement lisse n'est plus un clignement,
    c'est un endormissement. Les inclure ici ferait echouer le controle sur la
    seule chose qui a raison de sauter.
"""
import json
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer, QUrl, Qt  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from presence.model import Expression, Gaze  # noqa: E402
from presence.vocabulary import face  # noqa: E402

app = QApplication(sys.argv)
view = QWebEngineView()
page = view.page()
page.setBackgroundColor(Qt.GlobalColor.black)
avatar_scheme.install(page.profile())
view.resize(900, 700)
view.show()

#: Remplace la fonction d'ecriture du rig par un enregistreur qui appelle
#: l'originale. C'est la derniere ligne avant le modele : ce qui passe ici est
#: ce que la geometrie recoit, sans interpretation.
INSTALL_RECORDER = """
(function () {
  const rig = window.__lab && window.__lab.rig;
  if (!rig) return 'PAS_ENCORE';
  if (rig.__original) rig.apply = rig.__original;
  rig.__original = rig.apply;
  window.__frames = [];
  window.__recording = false;
  let frame = null;
  const original = rig.apply;
  rig.apply = function (name, weight) {
    if (window.__recording) {
      if (frame === null || frame.__n !== rig.t) {
        frame = { __n: rig.t };
        window.__frames.push(frame);
      }
      frame[name] = weight;
    }
    original(name, weight);
  };
  return 'OK';
})()
"""

#: `amused` compose (la bouche traine), `surprised` arrive d'un bloc. Les deux
#: sont dans la table pour une raison opposee, et un moteur qui les jouerait
#: pareil aurait tort deux fois.
CASES = [
    ("amused", 0.75, True),
    ("surprised", 0.80, False),
    ("thinking", 0.70, True),
]

state = {"i": 0, "failures": [], "lines": []}


def start_case():
    if state["i"] >= len(CASES):
        hold_case()
        return
    name, intensity, composes = CASES[state["i"]]

    # On part d'un visage neutre installe, sinon on mesurerait une transition
    # depuis ce que la case precedente avait laisse.
    page.runJavaScript(
        "window.__recording = false;"
        "document.getElementById('unforce').click(); 0")

    def arm():
        # Pilote par les BOUTONS, pas en ecrivant dans `window.__lab`. `apply()`
        # est privee au module, et un controle qui reproduirait son travail a
        # cote testerait sa propre copie. Cliquer est aussi ce que fait la
        # personne dont on veut reproduire l'experience.
        page.runJavaScript(
            "window.__frames = []; window.__recording = true;"
            f"document.getElementById('intensity').value = {intensity};"
            "document.getElementById('intensity').dispatchEvent(new Event('input'));"
            "[...document.getElementById('expressions').children]"
            f"  .find((b) => b.dataset.value === '{name}').click(); 0")
        QTimer.singleShot(1400, lambda: page.runJavaScript(
            "window.__recording = false; JSON.stringify(window.__frames)",
            lambda raw: measure(name, intensity, composes, raw)))

    QTimer.singleShot(900, arm)


def carried(frames):
    """Chaque image COMPLETE, reconstituee.

    Le rig n'ecrit plus que ce qui change — 52 ecritures par image, presque
    toutes identiques, etaient son plus gros cout evitable. Une image
    enregistree ne contient donc que les formes qui ont bouge ; la valeur des
    autres est celle de l'image d'avant. Sans cette reconstitution, une forme
    tenue immobile a sa cible passerait pour « perdue ».
    """
    state, out = {}, []
    for frame in frames:
        state.update({k: v for k, v in frame.items() if k != "__n"})
        out.append(dict(state))
    return out


def measure(name, intensity, composes, raw):
    frames = carried(json.loads(raw or "[]"))
    target = face(Expression(name), intensity, Gaze.USER)
    lids = {"eyeBlinkLeft", "eyeBlinkRight"}

    if len(frames) < 25:
        state["failures"].append(f"{name}: {len(frames)} images enregistrees, trop peu")
        step()
        return

    wanted = {k: v for k, v in target.items() if v >= 0.05 and k not in lids}

    # 1. aucune composante perdue, 2. aucune composante ecrasee.
    #    Jugee au PIC, pas a la derniere image : un visage a maintien naturel
    #    (la surprise, `performance.js`) retombe de lui-meme apres 0.8 s, et
    #    c'est voulu. Ce qui est interdit est qu'une forme n'arrive jamais.
    #    Les couches se combinent par max(), donc une forme peut monter PLUS
    #    haut que sa cible ; jamais moins.
    #
    #    Les yeux a part : ils sont pilotes en continu par `gaze.js` —
    #    micro-saccades, compensation des mouvements de tete — et oscillent
    #    donc autour de leur cible. On juge leur moyenne sur la fin, avec la
    #    tolerance d'un oeil vivant.
    tail = frames[-20:]
    lost = []
    for k, v in wanted.items():
        if k.startswith("eyeLook"):
            mean = sum(f.get(k, 0) for f in tail) / len(tail)
            if mean < v - 0.06:
                lost.append(k)
        elif max(f.get(k, 0) for f in frames) < v - 0.02:
            lost.append(k)

    # 3. aucun snap : le plus grand ecart d'une image a l'autre, hors paupieres.
    jumps = []
    for a, b in zip(frames, frames[1:]):
        for k in wanted:
            if k in a and k in b:
                jumps.append((abs(b[k] - a[k]), k))
    biggest = max(jumps) if jumps else (0.0, "-")

    # 4. le decalage, mesure : quand chaque groupe atteint la moitie de sa cible.
    half = {}
    for k, v in wanted.items():
        for i, frame in enumerate(frames):
            if frame.get(k, 0) >= v * 0.5:
                half.setdefault(k[:4], []).append(i)
                break
    order = {g: statistics.median(v) for g, v in half.items() if v}
    brow = order.get("brow")
    mouth = order.get("mout")
    spread = (mouth - brow) if (brow is not None and mouth is not None) else None

    problems = []
    if lost:
        problems.append(f"composantes perdues ou ecrasees : {lost[:4]}")
    if biggest[0] > 0.25:
        problems.append(f"snap sur {biggest[1]} : {biggest[0]:.3f} en une image")
    if composes and (spread is None or spread < 2):
        problems.append(f"aucun decalage mesurable (bouche - sourcils = {spread})")
    if not composes and spread is not None and spread > 6:
        problems.append(f"{name} se compose alors qu'il devrait arriver d'un bloc "
                        f"(ecart {spread} images)")

    mark = "OK  " if not problems else "FAUX"
    state["lines"].append(
        f"  [{mark}] {name:<10} {len(wanted):2d} formes · "
        f"plus grand saut {biggest[0]:.3f} · "
        f"bouche - sourcils {spread if spread is not None else '-'} images")
    for problem in problems:
        state["lines"].append(f"         {problem}")
        state["failures"].append(f"{name}: {problem}")
    step()


def step():
    state["i"] += 1
    QTimer.singleShot(150, start_case)


def hold_case():
    """`hold_s` : un visage qui doit tenir puis revenir.

    Il n'etait lu par personne dans le renderer. WAKING demandait 1.2 s de
    surprise et ERROR 2 s d'inquietude ; les deux duraient jusqu'au changement
    d'etat suivant, c'est-a-dire potentiellement pour toujours.
    """
    target = face(Expression.SURPRISED, 0.8, Gaze.USER)
    peak = max(k for k in target if k.startswith("brow"))
    payload = json.dumps(target)
    page.runJavaScript(
        "window.__recording = false;"
        f"window.__lab.rig.setExpression({payload}, 'user', 'surprised', 0.6);"
        "window.__frames = []; window.__recording = true; 0")

    def read():
        page.runJavaScript(
            "window.__recording = false; JSON.stringify(window.__frames)",
            lambda raw: measure_hold(raw, peak, target[peak]))
    QTimer.singleShot(2200, read)


def measure_hold(raw, shape, top):
    frames = carried(json.loads(raw or "[]"))
    values = [f.get(shape, 0) for f in frames if shape in f]
    if len(values) < 25:
        state["failures"].append("hold_s : trop peu d'images")
        report()
        return
    high = max(values)
    tail = values[-8:]
    released = max(tail) < high * 0.4

    mark = "OK  " if released else "FAUX"
    state["lines"].append(
        f"  [{mark}] hold_s 0.6 s  {shape} monte a {high:.2f}, "
        f"retombe a {max(tail):.2f}")
    if not released:
        state["lines"].append("         le visage n'est jamais revenu au repos")
        state["failures"].append("hold_s ne relache pas")
    report()


def report():
    print()
    for line in state["lines"]:
        print(line, flush=True)
    print()
    if state["failures"]:
        print(f"  {len(state['failures'])} PROBLEME(S)", flush=True)
        app.exit(1)
        return
    print("  Un visage s'installe : chaque composante arrive, aucune ne saute,", flush=True)
    print("  et celles qui doivent trainer trainent.", flush=True)
    app.exit(0)


tries = {"n": 0}


def probe():
    page.runJavaScript(INSTALL_RECORDER, ready)


def ready(raw):
    tries["n"] += 1
    if raw == "OK":
        QTimer.singleShot(600, start_case)
        return
    if tries["n"] > 60:
        print("  le labo n'a jamais ete pret", flush=True)
        app.exit(1)
        return
    QTimer.singleShot(250, probe)


view.loadFinished.connect(lambda ok: QTimer.singleShot(1500, probe))
view.setUrl(QUrl("jarvis://avatar/lab.html"))
QTimer.singleShot(90000, lambda: app.exit(1))
sys.exit(app.exec())
