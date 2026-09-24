"""Le cadrage portrait, mesure dans la vraie page, pour chaque visage installe.

    python avatar/checks/framing.py [--shots]        (demande PyQt6-WebEngine)

Ce que le telephone et le panneau montrent est un PORTRAIT : la tete et le
haut des epaules, centres, sans bras. Les deux choses qui l'avaient casse :

  * la camera visait le milieu de la boite du CORPS ; des bras en pose A un peu
    asymetriques le deplacaient, et le visage glissait vers un bord ;
  * un visage sans repos de bras (`armRest` vide) gardait mains et avant-bras
    dans le cadre, coupes par le bord.

On ne fige aucune coordonnee. Pour chaque modele humanoide installe, et a
chaque taille reelle (telephone 280x280, panneau 300x180 et 520x180), on
projette les os dans l'image (x, y de -1 a 1) et on verifie des invariants :

  tete centree        |x des yeux|            < 0.08
  yeux en haut        y des yeux              entre 0.1 et 0.45
  crane dans le cadre sommet du modele        sous le bord (y < 1)
  visage lisible      ecart entre les yeux    > 0.10 de la largeur utile
  pas de bras         avant-bras et mains     hors de l'image

Le manifeste de chaque modele est construit par presence.models.activate(),
la fonction de `install_model --use`, en memoire : rien n'est ecrit, le
modele actif ne change pas. `--shots` enregistre les images dans
`avatar/checks/shots/` (ignore par git) pour les regarder.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))

from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer, QUrl  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from presence import models  # noqa: E402

SIZES = [(280, 280), (300, 180), (520, 180)]
SHOTS = BASE / "avatar" / "checks" / "shots"
SAVE = "--shots" in sys.argv

PROBE = r"""(() => {
  const s = window.__stage, b = window.__body;
  if (!s || !b || !document.body.classList.contains('ready')) return null;
  const V = s.camera.position.constructor, v = new V();
  const at = (o) => { o.getWorldPosition(v); const p = v.clone().project(s.camera); return [p.x, p.y]; };
  const n = b.nodes || {};
  const arms = [];
  const ARM = /(fore.?arm|lowerarm|hand)$/i;
  b.object3D.traverse((o) => {
    const name = (o.name || '').replace(/[ _.:]/g, '');
    if (o.isBone && ARM.test(name) && !/finger|thumb|index|middle|ring|pinky/i.test(name)) arms.push([name, ...at(o)]);
  });
  const crown = new V(s.framing.target.x, s.framing.top, s.framing.front).project(s.camera);
  return JSON.stringify({
    model: window.JARVIS.status().model, procedural: window.JARVIS.status().procedural,
    mode: s.framing.mode, kind: s.framing.kind,
    eyes: [n.eyeLeft, n.eyeRight].filter(Boolean).map(at), crown: crown.y, arms,
    head: n.head && n.head !== b.object3D && !b.headIsModel ? at(n.head) : null,
  });
})()"""


def humanoids():
    """(slot, manifest) for each installed model that has a body and a file."""
    base = models.read_json(BASE / "avatar" / "manifest.json")
    out = []
    for path, profile in models.installed():
        parts = set((profile.get("calibration") or {}).get("parts") or [])
        if "legs" not in parts or not path.is_file():
            continue
        if not models.relative(path).startswith("jarvis/"):
            # Les modeles de test/ sont des cas limites pour asset_robustness.py,
            # sans calibration : jamais actifs, ils ne sont pas cadres ici.
            print(f"  [SKIP] {models.relative(path):<28} modele de test, pas un visage JARVIS")
            continue
        out.append((models.relative(path), models.activate(base, path, profile)))
    return out


def judge(m, w, h):
    """[(ok, what)] for one render."""
    res = []
    if m["procedural"]:
        return [(False, f"pas de modele reel ({m['model']})")]
    if not m["eyes"] and not m["head"]:
        return [(False, "ni yeux ni os de tete : rien pour centrer")]
    if m["eyes"]:
        ex = sum(e[0] for e in m["eyes"]) / len(m["eyes"])
        ey = sum(e[1] for e in m["eyes"]) / len(m["eyes"])
        res.append((abs(ex) < 0.08, f"tete centree (x yeux {ex:+.2f})"))
        res.append((0.1 < ey < 0.45, f"yeux en haut (y {ey:+.2f})"))
    else:
        # Sans os d'yeux, la camera se centre sur l'os de tete ; on mesure pareil,
        # et la hauteur des yeux n'est pas verifiable : elle n'est pas inventee.
        hx = m["head"][0]
        res.append((abs(hx) < 0.08, f"tete centree (x os de tete {hx:+.2f}, pas d'os d'yeux)"))
    res.append((m["crown"] < 1.0, f"crane dans le cadre (y {m['crown']:+.2f})"))
    if len(m["eyes"]) == 2:
        # En largeur d'image utile : sur un panneau large, c'est la hauteur qui
        # limite, et l'ecart se mesure contre elle.
        gap = abs(m["eyes"][0][0] - m["eyes"][1][0]) * max(1.0, w / h)
        res.append((gap > 0.10, f"visage lisible (ecart yeux {gap:.2f})"))
    inside = [a[0] for a in m["arms"] if abs(a[1]) <= 1 and abs(a[2]) <= 1]
    res.append((not inside, "pas de bras dans l'image" + (f" : {', '.join(inside)}" if inside else "")))
    return res


def main() -> int:
    cases = [(slot, man, w, h) for slot, man in humanoids() for (w, h) in SIZES]
    if not cases:
        print("  aucun modele humanoide installe : rien a cadrer (SKIP)")
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)      # une vue par cas, fermee apres
    state = {"i": 0, "fail": 0, "tries": 0, "view": None}

    def start():
        if state["i"] >= len(cases):
            n = len(cases)
            print(f"\n  {n - state['fail']}/{n} cadrages conformes.")
            app.exit(1 if state["fail"] else 0)
            return
        slot, man, w, h = cases[state["i"]]
        view = QWebEngineView()
        avatar_scheme.install(view.page().profile())
        script = QWebEngineScript()
        script.setSourceCode("window.JARVIS_MANIFEST = " + json.dumps(man) + ";")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        view.page().scripts().insert(script)
        view.resize(w, h)
        view.show()
        state.update(view=view, tries=0)
        view.setUrl(QUrl("jarvis://avatar/index.html"))
        QTimer.singleShot(1500, poll)

    def poll():
        state["view"].page().runJavaScript(PROBE, got)

    def got(raw):
        slot, man, w, h = cases[state["i"]]
        if raw is None:
            state["tries"] += 1
            if state["tries"] < 120:
                QTimer.singleShot(500, poll)
                return
            results = [(False, "la page ne s'est jamais dite prete")]
        else:
            results = judge(json.loads(raw), w, h)
        bad = [what for ok, what in results if not ok]
        state["fail"] += bool(bad)
        tag = "PASS" if not bad else "FAIL"
        print(f"  [{tag}] {slot:<28} {w}x{h:<4} " + ("; ".join(bad) if bad else "; ".join(what for _, what in results)))
        # Le fondu d'apparition (600 ms) et une image WebGL a peindre : une
        # capture immediate est blanche.
        QTimer.singleShot(1500 if SAVE else 0, lambda: done(slot, w, h))

    def done(slot, w, h):
        if SAVE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            state["view"].grab().save(str(SHOTS / f"cadrage_{slot.replace('/', '_')}_{w}x{h}.png"))
        state["view"].close()
        state["view"].deleteLater()
        state["i"] += 1
        QTimer.singleShot(0, start)

    QTimer.singleShot(0, start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
