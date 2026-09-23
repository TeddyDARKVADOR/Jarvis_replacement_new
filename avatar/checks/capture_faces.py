"""Neuf visages, photographies dans le vrai labo, sur le modele installe.

    python avatar/checks/capture_faces.py [prefixe]       (demande PyQt6-WebEngine)

Les autres controles mesurent des nombres ; aucun ne dit si le visage A L'AIR
juste — une texture absente, des cils opaques, une tete retournee passent tous
les tests. Celui-ci pose neuf etats par les boutons du labo (neutre, ecoute,
reflexion, parole, surprise, inquietude, amusement, joie, gravite), attend que
chacun s'installe, et enregistre une image dans `avatar/checks/shots/`
(ignore par git). Il ne juge rien : il donne a regarder, et une planche qui les
reunit pour comparer deux modeles cote a cote.

Ce n'est pas une comparaison au pixel pres, et ca ne doit pas en devenir une :
on regarde si les sourcils, les yeux, la bouche et la tete changent comme
l'etat le dit.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QRect, QTimer, QUrl, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "visage"
SHOTS = BASE / "avatar" / "checks" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

#: (nom, ce que le labo recoit) — par ses BOUTONS, le chemin d'une personne :
#: un etat machine, ou un visage nomme a une intensite.
def button(group, value):
    return (f"[...document.getElementById('{group}').children]"
            f".find((b) => b.dataset.value === '{value}').click();")


def face(value, intensity=0.7):
    return (f"document.getElementById('intensity').value = {intensity};"
            "document.getElementById('intensity').dispatchEvent(new Event('input'));"
            + button('expressions', value))


TALK = ("window.__talk = setInterval(() => { const t = performance.now() / 1000;"
        " window.__lab.engine.speak(Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3)) * 0.8); }, 40);")
QUIET = "clearInterval(window.__talk); window.__lab.engine.speak(0);"

CASES = [
    ("neutre", "document.getElementById('clear').click();" + button('states', 'ACTIVE')),
    ("ecoute", button('states', 'LISTENING')),
    ("reflexion", button('states', 'THINKING')),
    ("parole", button('states', 'SPEAKING') + TALK),
    ("surprise", QUIET + button('states', 'ACTIVE') + face('surprised', 0.8)),
    ("inquietude", face('concerned')),
    ("amusement", face('amused')),
    ("joie", face('happy')),
    ("gravite", face('serious')),
]

app = QApplication(sys.argv)
view = QWebEngineView()
avatar_scheme.install(view.page().profile())
view.resize(1400, 860)
view.show()
state = {"i": 0, "files": []}


def step():
    if state["i"] >= len(CASES):
        sheet()
        return
    name, js = CASES[state["i"]]
    view.page().runJavaScript(js)
    # La surprise culmine vite puis retombe : on la photographie a son pic.
    QTimer.singleShot(260 if name == "surprise" else 1500, lambda: shoot(name))


HEAD = """JSON.stringify((() => {
  const app = window.__lab, head = app.body.nodes.head || app.body.nodes.neck;
  const r = document.getElementById('stage').getBoundingClientRect();
  if (!head || !app.camera) return null;
  const v = head.getWorldPosition(new head.position.constructor()).project(app.camera);
  const k = window.devicePixelRatio || 1;
  return [(r.x + (v.x + 1) / 2 * r.width) * k, (r.y + (1 - v.y) / 2 * r.height) * k];
})())"""


def shoot(name):
    view.page().runJavaScript(HEAD, lambda raw: grab(name, json.loads(raw) if raw else None))


def grab(name, head):
    pixmap = view.grab()
    # La tete, en cadrage `face` : le labo la place a gauche du centre du
    # canevas (le panneau de droite le recouvre en partie).
    x, y, w, h = state.get("rect") or [0, 0, pixmap.width(), pixmap.height()]
    side = int(h * 0.42)
    if head:
        # Centre sur l'os de tete projete, un peu au-dessus (l'os est a la
        # base du crane) : le meme cadre pour tout modele.
        cx, cy = int(head[0]), int(head[1] + side * 0.02)
        stage = pixmap.copy(QRect(cx - side // 2, cy - int(side * 0.55), side, side))
    else:
        stage = pixmap.copy(QRect(x + int(w * 0.42), y + int(h * 0.12), side, side))
    path = SHOTS / f"{PREFIX}_{state['i']:02d}_{name}.png"
    stage.save(str(path))
    state["files"].append((name, path))
    state["i"] += 1
    step()


def sheet():
    rh = (state.get("rect") or [0, 0, 800, 620])[3]
    w = h = int(rh * 0.42) // 2
    board = QPixmap(w * 3, (h + 22) * 3)
    board.fill(QColor(12, 16, 22))
    p = QPainter(board)
    p.setFont(QFont("Segoe UI", 11))
    for k, (name, path) in enumerate(state["files"]):
        x, y = (k % 3) * w, (k // 3) * (h + 22)
        img = QPixmap(str(path)).scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(x, y + 22, img)
        p.setPen(QColor(125, 211, 252))
        p.drawText(x + 8, y + 16, name)
    p.end()
    out = SHOTS / f"{PREFIX}_planche.png"
    board.save(str(out))
    print(json.dumps({"planche": str(out), "images": [str(f) for _, f in state["files"]]}))
    app.exit(0)


def ready():
    view.page().runJavaScript(
        "JSON.stringify(!!(window.__lab && window.__lab.engine && window.__lab.performance))",
        lambda raw: start() if raw == "true" else QTimer.singleShot(300, ready))


def start():
    # Le canevas de la scene, tel que la page le place : on photographie lui,
    # et pas les panneaux autour.
    view.page().runJavaScript(
        "document.querySelector('[data-frame=\"face\"]').click();"
        "document.getElementById('trace').style.display = 'none';"
        "JSON.stringify((() => { const r = document.getElementById('stage').getBoundingClientRect();"
        " const k = window.devicePixelRatio || 1;"
        " return [r.x * k, r.y * k, r.width * k, r.height * k].map(Math.round); })())",
        lambda raw: (state.__setitem__("rect", json.loads(raw)), QTimer.singleShot(800, step)))


view.loadFinished.connect(lambda ok: ready())
view.setUrl(QUrl("jarvis://avatar/lab.html"))
QTimer.singleShot(120000, lambda: app.exit(1))
sys.exit(app.exec())
