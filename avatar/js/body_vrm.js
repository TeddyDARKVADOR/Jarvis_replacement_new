/**
 * body_vrm.js — a VRM avatar, wearing the same interface as everything else.
 *
 * WHY VRM NEEDS ITS OWN FILE WHEN IT IS "JUST A GLB"
 *   A .vrm is a glTF with extensions, so `GltfBody` loads it. It would then be
 *   a mannequin: VRM does not express a face through 52 ARKit morph targets. It
 *   expresses one through a small set of named *expressions* — `happy`, `angry`,
 *   `sad`, `relaxed`, `surprised`, the five visemes `aa/ih/ou/ee/oh`, `blink`,
 *   `blinkLeft`, `blinkRight` — each of which is itself a blend the author
 *   decided on. And it aims the eyes through a `lookAt` object, not through
 *   `eyeLookInLeft`.
 *
 *   So the translation is not a rename, it is a reduction: fifty-two continuous
 *   dials onto roughly a dozen. That is a real loss of nuance and it is stated
 *   here rather than hidden — a VRM JARVIS is more stylised and less subtle
 *   than an ARKit one, by construction.
 *
 * WHY IT IS WORTH IT ANYWAY
 *   VRoid Studio is free and makes a complete, rigged, expressive VRM in an
 *   afternoon with no modelling skill. For someone who wants a character of
 *   their own rather than a marketplace face, that is the shortest path that
 *   exists — and the rest of this system does not care which it is.
 *
 * THE ONE THING THAT IS CHECKED FIRST
 *   VRM 1.0 files increasingly ship the ARKit set *as well*, as custom
 *   expressions named `browInnerUp` and so on. When they do, they are used
 *   directly and none of the reduction below happens. So a good VRM gets the
 *   full-fidelity path and a plain one gets the stylised path, decided by what
 *   the file actually contains.
 */

import * as THREE from 'three';
import { VRMLoaderPlugin, VRMUtils } from '../vendor/three-vrm.module.min.js';
import { ARKIT_NAMES } from './arkit.js';

/**
 * ARKit shape -> which VRM expression it feeds, and how hard.
 *
 * Several ARKit shapes feed one VRM expression; the strongest contributor wins
 * rather than the sum, for the same reason the rig combines layers with max():
 * `browDownLeft` and `browDownRight` both meaning "angry" must not add up to a
 * VRM `angry` of 1.6.
 *
 * The weights are not arbitrary. `mouthSmileLeft` at full is a broad smile, so
 * it maps to `happy` 1:1; `cheekSquintLeft` at full is a smile's *side effect*,
 * so it only contributes 0.4 and cannot by itself make a VRM grin.
 */
const TO_VRM = {
  // ── yeux ────────────────────────────────────────────────────────────────
  eyeBlinkLeft: ['blinkLeft', 1.0],
  eyeBlinkRight: ['blinkRight', 1.0],
  eyeSquintLeft: ['blinkLeft', 0.35],
  eyeSquintRight: ['blinkRight', 0.35],
  eyeWideLeft: ['surprised', 0.55],
  eyeWideRight: ['surprised', 0.55],

  // ── sourcils : la seule source d'emotion "haute" dans un VRM ───────────
  browInnerUp: ['sad', 0.75],
  browDownLeft: ['angry', 0.8],
  browDownRight: ['angry', 0.8],
  browOuterUpLeft: ['surprised', 0.45],
  browOuterUpRight: ['surprised', 0.45],

  // ── bouche : emotion ────────────────────────────────────────────────────
  mouthSmileLeft: ['happy', 1.0],
  mouthSmileRight: ['happy', 1.0],
  cheekSquintLeft: ['happy', 0.4],
  cheekSquintRight: ['happy', 0.4],
  mouthFrownLeft: ['sad', 0.8],
  mouthFrownRight: ['sad', 0.8],
  mouthPressLeft: ['relaxed', 0.3],
  mouthPressRight: ['relaxed', 0.3],
  noseSneerLeft: ['angry', 0.4],
  noseSneerRight: ['angry', 0.4],

  // ── bouche : parole. Les cinq visemes VRM, depuis les formes ARKit que
  //    lipsync.js pilote reellement.
  jawOpen: ['aa', 1.0],
  mouthFunnel: ['oh', 0.9],
  mouthPucker: ['ou', 1.0],
  mouthStretchLeft: ['ih', 0.7],
  mouthStretchRight: ['ih', 0.7],
  mouthClose: ['ee', 0.0],       // pas de viseme "ferme" : le repos suffit
};

/** ARKit eye-look shapes -> a gaze direction, for `VRMLookAt`. */
const LOOK = {
  eyeLookInLeft: [1, 0], eyeLookOutLeft: [-1, 0],
  eyeLookInRight: [-1, 0], eyeLookOutRight: [1, 0],
  eyeLookUpLeft: [0, 1], eyeLookUpRight: [0, 1],
  eyeLookDownLeft: [0, -1], eyeLookDownRight: [0, -1],
};

/** How far the eyes travel at full weight. Metres, in front of the head. */
const GAZE_REACH = 0.45;

export class VrmBody {
  constructor(gltf, manifest) {
    this.vrm = gltf.userData.vrm;
    this.scene = this.vrm.scene;
    this.manifest = manifest;
    this.clips = Object.create(null);
    this.embedded = (gltf.animations || []).map((clip) => clip.name);
    this.mixer = new THREE.AnimationMixer(this.scene);

    // Sans cela le modele fait face a la camera de dos : VRM 0.x regarde vers
    // -Z par convention, three.js vers +Z.
    VRMUtils.rotateVRM0(this.vrm);

    const model = manifest.model || {};
    this.scene.scale.setScalar(Number(model.scale) || 1);
    const p = model.position || [0, 0, 0];
    this.scene.position.set(p[0] || 0, p[1] || 0, p[2] || 0);

    this.expressions = this.vrm.expressionManager;
    this.available = new Set(
      this.expressions ? this.expressions.expressions.map((e) => e.expressionName) : [],
    );

    // Le chemin haute-fidelite : ce VRM porte-t-il DEJA les noms ARKit ?
    this.native = ARKIT_NAMES.filter((name) => this.available.has(name));
    this.direct = this.native.length >= 20;

    this.pending = Object.create(null);   // accumule une image, applique en fin
    this.gaze = { x: 0, y: 0 };

    this._mapBones();
    this.detectedParts = new Set(['head', 'torso', 'arms', 'legs']);   // un VRM est humanoide par definition
  }

  get object3D() { return this.scene; }

  /**
   * Same signature as every other body. The weights are collected rather than
   * applied, because several ARKit shapes feed one VRM expression and the
   * answer is only known once the whole frame has been written.
   */
  setMorph(name, weight) {
    this.pending[name] = weight;
  }

  /** Called once per frame by main.js, after the rig has written everything. */
  update(delta) {
    if (this.direct) {
      for (const name of this.native) {
        this.expressions.setValue(name, this.pending[name] || 0);
      }
    } else {
      const resolved = Object.create(null);
      for (const name in TO_VRM) {
        const [target, scale] = TO_VRM[name];
        const value = (this.pending[name] || 0) * scale;
        if (value > (resolved[target] || 0)) resolved[target] = value;
      }
      for (const target in resolved) {
        if (this.available.has(target)) {
          this.expressions.setValue(target, Math.min(1, resolved[target]));
        }
      }
    }

    this._applyGaze();
    this.vrm.update(delta);
  }

  _applyGaze() {
    const lookAt = this.vrm.lookAt;
    if (!lookAt) return;

    let x = 0;
    let y = 0;
    for (const name in LOOK) {
      const weight = this.pending[name] || 0;
      if (weight <= 0.001) continue;
      x += LOOK[name][0] * weight;
      y += LOOK[name][1] * weight;
    }
    // Les deux yeux ecrivent la meme direction : on moyenne plutot que de
    // sommer, sinon un regard a gauche vaut 2.
    this.gaze.x = x / 2;
    this.gaze.y = y / 2;

    if (!this._target) {
      this._target = new THREE.Object3D();
      this.scene.add(this._target);
      lookAt.target = this._target;
    }
    const head = this.nodes.head;
    if (head) {
      head.getWorldPosition(this._target.position);
      this._target.position.z += 1.0;
      this._target.position.x -= this.gaze.x * GAZE_REACH;
      this._target.position.y += this.gaze.y * GAZE_REACH;
      this.scene.worldToLocal(this._target.position);
    }
  }

  addClip(gesture, clip) { this.clips[gesture] = clip; }

  _mapBones() {
    // VRM normalise le squelette : on demande les os par leur role, ce qui est
    // le seul cas de cette base de code ou les noms ne posent aucun probleme.
    const human = this.vrm.humanoid;
    const get = (name) => (human ? human.getNormalizedBoneNode(name) : null);
    this.nodes = {
      head: get('head'),
      neck: get('neck'),
      spine: get('chest') || get('spine'),
      root: get('hips') || this.scene,
      eyeLeft: get('leftEye'),
      eyeRight: get('rightEye'),
      armLeftUpper: get('leftUpperArm'),
      armRightUpper: get('rightUpperArm'),
      armLeftLower: get('leftLowerArm'),
      armRightLower: get('rightLowerArm'),
    };
  }

  /** Les memes questions que GltfBody.capabilities, les memes reponses. */
  capabilities() {
    return {
      expression: this.available.size > 0,
      // Un VRM a toujours un VRMLookAt : le regard est natif et normalise, ce
      // qui est l'un des rares endroits ou VRM est plus simple que glTF.
      gaze: !!this.vrm.lookAt,
      gazeBy: this.vrm.lookAt ? 'VRMLookAt' : 'aucun',
      lipsync: ['aa', 'ih', 'ou', 'ee', 'oh'].some((v) => this.available.has(v))
        || this.direct,
      gesture: true,
      posture: true,
    };
  }

  report() {
    return {
      capabilities: this.capabilities(),
      morphsFound: this.direct ? this.native.length : Object.keys(TO_VRM).length,
      morphsMissing: this.direct
        ? ARKIT_NAMES.filter((n) => !this.available.has(n))
        : ARKIT_NAMES.filter((n) => !(n in TO_VRM)),
      bones: Object.keys(this.nodes).filter((k) => this.nodes[k]),
      parts: [...this.detectedParts],
      clips: Object.keys(this.clips),
      vrm: this.direct ? 'ARKit natif' : `${this.available.size} expressions VRM`,
    };
  }
}

/** Register the VRM plugin on a GLTFLoader. Harmless on a plain .glb. */
export function enableVrm(loader) {
  loader.register((parser) => new VRMLoaderPlugin(parser));
  return loader;
}

/** Is this parsed glTF actually a VRM? */
export function isVrm(gltf) {
  return !!(gltf.userData && gltf.userData.vrm);
}
