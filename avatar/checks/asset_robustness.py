"""Un modele incomplet coute une capacite, jamais le visage entier.

    python avatar/checks/asset_robustness.py

Charge dans le VRAI labo (meme chargeur, meme moteur que le panneau) une serie
de modeles auxquels il manque quelque chose — fabriques par `fixtures.py`, plus
deux vrais fichiers de test — puis, pour chacun :

    1. le chargement ne leve pas
    2. le PROFIL dit la verite : pas de visage annonce sans formes, pas de tete
       annoncee sans os, les visemes natifs seulement s'ils y sont
    3. le moteur joue une seance complete dessus — intentions, parole, regard —
       sans ecrire une seule valeur invalide dans le modele
    4. aucune erreur JavaScript, sauf celles que le cas provoque exprès (une
       texture introuvable se plaint, et c'est le comportement attendu)

La regle : une capacite optionnelle absente est une degradation, jamais une
panne. Et l'annoncer comme presente serait pire que l'absence.
"""
import base64
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
from client_desktop.ui import avatar_scheme  # noqa: E402
avatar_scheme.register()

from PyQt6.QtCore import QTimer, QUrl, Qt  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

CAPTURE = """
window.__log = [];
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
"""

#: Une seance : de quoi solliciter visage, regard, tete, bouche et accents.
SESSION = """
(function () {
  const lab = window.__lab;
  const intents = ['greet', 'investigate', 'warn', 'apologise', 'explain', 'think'];
  let i = 0;
  const timer = setInterval(() => {
    lab.send({ intent: intents[i % intents.length] });
    i += 1;
    if (i >= intents.length) clearInterval(timer);
  }, 220);
  let t = 0;
  const voice = setInterval(() => {
    t += 0.04;
    lab.engine.speak(Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3)) * 0.8);
    if (t > 1.6) { clearInterval(voice); lab.engine.speak(0); }
  }, 40);
  return 'ok';
})()
"""

#: Tout ce que le moteur a ecrit dans le modele, relu dans three.js.
READ = """
(function () {
  const lab = window.__lab;
  const bad = [];
  let influences = 0;
  lab.body.object3D && lab.body.object3D.traverse((node) => {
    if (!node.morphTargetInfluences) return;
    node.morphTargetInfluences.forEach((v, i) => {
      influences += 1;
      if (!Number.isFinite(v) || v < -1e-6 || v > 1 + 1e-6) bad.push(node.name + '#' + i + '=' + v);
    });
  });
  const rot = [];
  for (const key in (lab.body.nodes || {})) {
    const n = lab.body.nodes[key];
    if (n && n.rotation) rot.push(n.rotation.x, n.rotation.y, n.rotation.z);
  }
  const out = lab.engine.output();
  return JSON.stringify({
    profile: lab.profile,
    bad: bad.slice(0, 5),
    influences,
    rotationsFinite: rot.every(Number.isFinite),
    eyesFinite: Number.isFinite(out.eyes.x) && Number.isFinite(out.eyes.y),
    decision: lab.engine.decision,
    frames: lab.engine.frame,
    log: window.__log.splice(0),
  });
})()
"""

REAL = {
    "facecap_compresse": (BASE / "avatar" / "models" / "test" / "facecap.glb",
                          {"arkit": 52, "compression": "KTX2", "limbs": "head"}),
    "michelle_sans_visage": (BASE / "avatar" / "models" / "test" / "Michelle.glb",
                             {"arkit": 0, "animations": 2}),
}

#: Les messages qu'un cas provoque volontairement.
EXPECTED_NOISE = {
    "texture_manquante": ("texture", "introuvable.png", "couldn't load", "404"),
    "vrm_buste": ("VRM non conforme",),
}

app = QApplication(sys.argv)
view = QWebEngineView()
page = view.page()
page.setBackgroundColor(Qt.GlobalColor.black)
avatar_scheme.install(page.profile())
script = QWebEngineScript()
script.setSourceCode(CAPTURE)
script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
page.scripts().insert(script)
view.resize(1200, 800)
view.show()

cases = []
for name, (make, expect) in fixtures.CASES.items():
    cases.append((name, make(), expect))
for name, (path, expect) in REAL.items():
    if path.is_file():
        cases.append((name, path.read_bytes(), expect))

state = {"i": 0, "bad": 0}


def start():
    if state["i"] >= len(cases):
        print(f"\n  {len(cases)} modeles, {state['bad']} probleme(s)", flush=True)
        if not state["bad"]:
            print("  Aucun ne fait tomber le visage ; chacun dit ce qu'il sait faire.", flush=True)
        app.exit(1 if state["bad"] else 0)
        return
    name, data, _ = cases[state["i"]]
    payload = base64.b64encode(data).decode("ascii")
    suffix = ".vrm" if name.startswith("vrm") else ".glb"
    page.runJavaScript(
        f"window.__loaded = null; window.__lab.loadBase64('{name}{suffix}', '{payload}')"
        ".then((r) => { window.__loaded = r || {}; })"
        ".catch((e) => { window.__loaded = {error: String(e)}; }); 0")
    QTimer.singleShot(300, wait_loaded)


def wait_loaded(tries=0):
    def got(raw):
        if raw in (None, "null") and tries < 60:
            QTimer.singleShot(150, lambda: wait_loaded(tries + 1))
            return
        loaded = json.loads(raw or "{}") or {}
        if loaded.get("error"):
            report({"load_error": loaded["error"]})
            return
        page.runJavaScript(SESSION)
        QTimer.singleShot(2400, lambda: page.runJavaScript(READ, lambda r: report(json.loads(r))))
    page.runJavaScript("JSON.stringify(window.__loaded)", got)


def report(data):
    name, _, expect = cases[state["i"]]
    problems = []
    if "load_error" in data:
        problems.append(f"chargement : {data['load_error']}")
    else:
        p = data["profile"]
        if "arkit" in expect and p["arkit"] != expect["arkit"]:
            problems.append(f"ARKit annonce {p['arkit']}, attendu {expect['arkit']}")
        if "eyes" in expect and p["eyes"]["ok"] != expect["eyes"]:
            problems.append(f"yeux annonces {p['eyes']}, attendu {expect['eyes']}")
        if "head" in expect and p["head"] != expect["head"]:
            problems.append(f"tete annoncee {p['head']}, attendu {expect['head']}")
        if "limbs" in expect and ",".join(p["limbs"]) != expect["limbs"]:
            problems.append(f"membres {p['limbs']}, attendu {expect['limbs']}")
        if "visemes" in expect and p["visemes"] != expect["visemes"]:
            problems.append(f"visemes {p['visemes']}, attendu {expect['visemes']}")
        if "format" in expect and p["format"] != expect["format"]:
            problems.append(f"format {p['format']}, attendu {expect['format']}")
        if "animations" in expect and p["animations"] != expect["animations"]:
            problems.append(f"{p['animations']} animations, attendu {expect['animations']}")
        if "compression" in expect and expect["compression"] not in p["compression"]:
            problems.append(f"compression {p['compression']}, attendu {expect['compression']}")
        if data["bad"]:
            problems.append(f"valeurs invalides ecrites : {data['bad']}")
        if not data["rotationsFinite"] or not data["eyesFinite"]:
            problems.append("rotation ou regard non fini")
        if data["frames"] < 60:
            problems.append(f"le moteur n'a tourne que {data['frames']} images")
        noise = EXPECTED_NOISE.get(name, ())
        unexpected = [l for l in data["log"]
                      if not any(n.lower() in l.lower() for n in noise)]
        if unexpected:
            problems.append(f"JS : {unexpected[:2]}")

    mark = "OK  " if not problems else "FAUX"
    detail = ""
    if "profile" in data:
        p = data["profile"]
        face = (f"VRM {p['vrmExpressions']:>2} expr" if p.get("faceBy") == "vrm"
                else f"ARKit {str(p['arkit']):>2}")
        detail = (f"{p['format']:<8} {face} · yeux {'✓' if p['eyes']['ok'] else '✗'} "
                  f"{p['eyes']['by']:<9} · tete {'✓' if p['head'] else '✗'}"
                  f"{' (modele)' if p['headIsModel'] else ''} · visemes {p['visemes']:<6} · "
                  f"{'/'.join(p['limbs'])} · {data['influences']} influences saines")
    print(f"  [{mark}] {name:<22} {detail}", flush=True)
    for problem in problems:
        print(f"         {problem}", flush=True)
        state["bad"] += 1
    state["i"] += 1
    QTimer.singleShot(200, start)


def probe(tries=0):
    def ready(raw):
        if raw:
            QTimer.singleShot(500, start)
        elif tries < 80:
            QTimer.singleShot(250, lambda: probe(tries + 1))
        else:
            print("  le labo n'a jamais ete pret", flush=True)
            app.exit(1)
    page.runJavaScript("!!(window.__lab && window.__lab.engine && window.__lab.loadBase64)", ready)


view.loadFinished.connect(lambda ok: probe())
view.setUrl(QUrl("jarvis://avatar/lab.html"))
QTimer.singleShot(240000, lambda: app.exit(1))
sys.exit(app.exec())
