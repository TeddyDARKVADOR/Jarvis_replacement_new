"""Prouve le contrat de l'adaptateur, sans avoir besoin du modele Ready Player Me.

Le moteur comportemental parle ARKit et rien d'autre. Un modele dont les yeux
sont des os — Ready Player Me, la plupart des humanoides du commerce — doit donc
etre traduit par l'adaptateur. Ce banc construit un rig minimal EN MEMOIRE
(deux os d'yeux, aucun blendshape), lui envoie des poids ARKit, et verifie que
les os ont tourne.

Verifie aussi qu'un modele qui a les FORMES ne bascule pas sur les os : les
formes sont plus fines et leur auteur les a reglees lui-meme.

    python bench_adapter.py
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

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script type="importmap">{"imports":{"three":"./vendor/three.module.min.js"}}</script>
</head><body><script type="module">
import * as THREE from 'three';
import { GltfBody } from './js/body_gltf.js';

/** Un humanoide minimal : os nommes comme Ready Player Me, aucun morph. */
function skeleton({ withEyes = true, withMorphs = false } = {}) {
  const scene = new THREE.Group();
  const add = (name, parent) => {
    const node = new THREE.Object3D();
    node.name = name;
    (parent || scene).add(node);
    return node;
  };
  const hips = add('Hips');
  const spine = add('Spine', hips);
  const spine2 = add('Spine2', spine);
  const neck = add('Neck', spine2);
  const head = add('Head', neck);
  add('LeftArm', spine2); add('RightArm', spine2);
  add('LeftForeArm', spine2); add('RightForeArm', spine2);
  add('LeftUpLeg', hips); add('RightUpLeg', hips);
  add('LeftFoot', hips); add('RightFoot', hips);
  if (withEyes) { add('LeftEye', head); add('RightEye', head); }

  if (withMorphs) {
    // Un mesh portant les huit formes de regard, comme un modele ARKit complet.
    const names = ['eyeLookInLeft','eyeLookOutLeft','eyeLookUpLeft','eyeLookDownLeft',
                   'eyeLookInRight','eyeLookOutRight','eyeLookUpRight','eyeLookDownRight'];
    const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
    mesh.name = 'Wolf3D_Head';
    mesh.morphTargetDictionary = {};
    mesh.morphTargetInfluences = [];
    names.forEach((n, i) => { mesh.morphTargetDictionary[n] = i; mesh.morphTargetInfluences.push(0); });
    head.add(mesh);
  }
  return { scene, animations: [] };
}

const MANIFEST = { model: { file: 'test.glb', scale: 1, position: [0,0,0], morphAliases: {} }, rig: {} };

window.__run = () => {
  const out = {};

  // ── 1. yeux sur os, aucune forme : l'adaptateur doit traduire ───────────
  const boned = new GltfBody(skeleton({ withEyes: true }), MANIFEST);
  out.bone = {
    gazeByBone: boned.gazeByBone,
    capabilities: boned.capabilities(),
    parts: [...boned.detectedParts].sort(),
  };

  // Le moteur ecrit de l'ARKit, comme toujours.
  boned.setMorph('eyeLookInLeft', 1.0);
  boned.setMorph('eyeLookOutRight', 1.0);
  boned.update();
  out.bone.lookRight = {
    left: +boned.nodes.eyeLeft.rotation.y.toFixed(4),
    right: +boned.nodes.eyeRight.rotation.y.toFixed(4),
  };

  // Regard oppose : les deux yeux doivent partir de l'autre cote.
  boned.setMorph('eyeLookInLeft', 0); boned.setMorph('eyeLookOutRight', 0);
  boned.setMorph('eyeLookOutLeft', 1.0); boned.setMorph('eyeLookInRight', 1.0);
  boned.update();
  out.bone.lookLeft = {
    left: +boned.nodes.eyeLeft.rotation.y.toFixed(4),
    right: +boned.nodes.eyeRight.rotation.y.toFixed(4),
  };

  // Haut / bas.
  boned.setMorph('eyeLookOutLeft', 0); boned.setMorph('eyeLookInRight', 0);
  boned.setMorph('eyeLookUpLeft', 1.0); boned.setMorph('eyeLookUpRight', 1.0);
  boned.update();
  out.bone.lookUp = {
    left: +boned.nodes.eyeLeft.rotation.x.toFixed(4),
    right: +boned.nodes.eyeRight.rotation.x.toFixed(4),
  };

  // Retour au repos : le regard doit revenir exactement a zero.
  for (const n of ['eyeLookUpLeft','eyeLookUpRight']) boned.setMorph(n, 0);
  boned.update();
  out.bone.rest = {
    left: +boned.nodes.eyeLeft.rotation.y.toFixed(6),
    right: +boned.nodes.eyeRight.rotation.x.toFixed(6),
  };

  // ── 2. formes presentes : elles doivent gagner ──────────────────────────
  const shaped = new GltfBody(skeleton({ withEyes: true, withMorphs: true }), MANIFEST);
  shaped.setMorph('eyeLookInLeft', 1.0);
  shaped.update();
  const mesh = shaped.morphTargets.get('eyeLookInLeft')[0];
  out.shaped = {
    gazeByBone: shaped.gazeByBone,
    gazeBy: shaped.capabilities().gazeBy,
    morphWritten: mesh.mesh.morphTargetInfluences[mesh.index],
    eyeBoneUntouched: shaped.nodes.eyeLeft.rotation.y === 0,
  };

  // ── 3. aucun oeil du tout : pas de regard, et rien ne casse ─────────────
  const blind = new GltfBody(skeleton({ withEyes: false }), MANIFEST);
  blind.setMorph('eyeLookInLeft', 1.0);
  blind.update();
  out.blind = { gazeByBone: blind.gazeByBone, gaze: blind.capabilities().gaze };

  return JSON.stringify(out);
};
window.__ready = true;
</script></body></html>"""

(BASE / "avatar" / "_adapter.html").write_text(PAGE, encoding="utf-8")

app = QApplication(sys.argv)
view = QWebEngineView()
page = view.page()
avatar_scheme.install(page.profile())
view.resize(200, 200)
view.show()

problems = []


def check(label, ok, detail=""):
    print(f"  [{'OK  ' if ok else 'FAUX'}] {label:<52} {detail}", flush=True)
    if not ok:
        problems.append(label)


def verdict(raw):
    if not raw:
        print("  ECHEC : rien rendu", flush=True)
        app.quit()
        return
    d = json.loads(raw)
    b, s, blind = d["bone"], d["shaped"], d["blind"]

    check("yeux sur os detectes", b["gazeByBone"] is True, f"gazeBy={b['capabilities']['gazeBy']}")
    check("corps entier reconnu", b["parts"] == ["arms", "head", "legs", "torso"],
          ", ".join(b["parts"]))
    check("capacite regard annoncee", b["capabilities"]["gaze"] is True)
    check("aucune expression annoncee sans morphs", b["capabilities"]["expression"] is False,
          "un rig nu n'a pas de visage")

    # Regard a droite : oeil gauche « in », oeil droit « out ».
    check("regard lateral : les deux yeux vont du meme cote",
          b["lookRight"]["left"] > 0 and b["lookRight"]["right"] > 0,
          f"g={b['lookRight']['left']} d={b['lookRight']['right']}")
    check("regard oppose : les deux yeux repartent de l'autre cote",
          b["lookLeft"]["left"] < 0 and b["lookLeft"]["right"] < 0,
          f"g={b['lookLeft']['left']} d={b['lookLeft']['right']}")
    check("regard vers le haut",
          b["lookUp"]["left"] < 0 and b["lookUp"]["right"] < 0,
          f"{b['lookUp']['left']} rad")
    check("retour exact au repos",
          b["rest"]["left"] == 0 and b["rest"]["right"] == 0)

    check("les formes gagnent sur les os quand elles existent",
          s["gazeByBone"] is False and s["gazeBy"] == "formes")
    check("la forme est bien ecrite", s["morphWritten"] == 1.0)
    check("l'os n'est pas touche dans ce cas", s["eyeBoneUntouched"] is True)

    check("modele sans yeux : pas de regard, aucune erreur",
          blind["gazeByBone"] is False and blind["gaze"] is False)

    print()
    if problems:
        print(f"  {len(problems)} PROBLEME(S) : " + ", ".join(problems), flush=True)
    else:
        print("  L'adaptateur traduit l'ARKit vers les os. Le moteur n'en sait rien.",
              flush=True)
    app.quit()


tries = {"n": 0}


def probe():
    page.runJavaScript("!!window.__ready", ready)


def ready(ok):
    tries["n"] += 1
    if ok:
        page.runJavaScript("window.__run()", verdict)
        return
    if tries["n"] > 40:
        print("  ECHEC : la page n'a pas demarre", flush=True)
        app.quit()
        return
    QTimer.singleShot(250, probe)


view.loadFinished.connect(lambda ok: QTimer.singleShot(400, probe))
view.setUrl(QUrl("jarvis://avatar/_adapter.html"))
QTimer.singleShot(40000, app.quit)
app.exec()

(BASE / "avatar" / "_adapter.html").unlink(missing_ok=True)
