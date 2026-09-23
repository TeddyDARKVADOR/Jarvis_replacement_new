/**
 * body_null.js — un corps sans rendu, qui retient tout ce qu'on lui ecrit.
 *
 * POURQUOI UN CORPS QUI NE S'AFFICHE PAS
 *   Le moteur ne connait du corps qu'une interface : `setMorph`, `nodes`,
 *   `update`, `capabilities()`. Ce fichier la remplit sans three.js, sans
 *   GPU, sans navigateur. Deux usages, et les deux comptent :
 *
 *   * les tests — `avatar/checks/engine_test.mjs` fait tourner le VRAI moteur
 *     sous Node et lit ici ce qui a ete ecrit. Mesurer la sortie, pas les
 *     tables : c'est la regle tiree de `rootRy`.
 *   * le rejeu — un enregistrement se rejoue en arriere-plan, sans toucher au
 *     visage affiche.
 *
 * Il peut imiter n'importe quel modele : les membres qu'il a, ses visemes, ses
 * yeux. C'est ce qui permet de tester un modele qui n'existe pas encore — le
 * visage masculin — avant qu'il soit installe.
 */

function node() {
  return { rotation: { x: 0, y: 0, z: 0 }, position: { x: 0, y: 0, z: 0 } };
}

export class NullBody {
  /**
   * @param {object} [spec]
   * @param {string[]} [spec.parts]    membres presents
   * @param {string} [spec.visemes]    'oculus' | 'vrm' | 'arkit' | 'none'
   * @param {boolean} [spec.head]      un os de tete existe
   * @param {object} [spec.manifest]  le manifeste, pour `rig.motion`
   */
  constructor(spec = {}) {
    const parts = spec.parts || ['head', 'torso'];
    this.manifest = spec.manifest || {};
    this.detectedParts = new Set(parts);
    this.visemeSet = spec.visemes || 'arkit';

    this.nodes = {};
    if (spec.head !== false) { this.nodes.head = node(); this.nodes.neck = node(); }
    if (parts.includes('torso')) { this.nodes.spine = node(); this.nodes.root = node(); }
    if (parts.includes('arms')) {
      for (const key of ['armLeftUpper', 'armRightUpper', 'armLeftLower', 'armRightLower']) {
        this.nodes[key] = node();
      }
    }

    this.clips = Object.create(null);
    this.morphs = Object.create(null);
    this.visemes = Object.create(null);
    this.writes = 0;
  }

  get object3D() { return null; }

  setMorph(name, weight) {
    this.morphs[name] = weight;
    this.writes += 1;
  }

  setViseme(name, weight) {
    this.visemes[name] = weight;
    this.writes += 1;
  }

  update() {}

  capabilities() {
    return {
      expression: true,
      gaze: true,
      gazeBy: 'formes',
      lipsync: this.visemeSet !== 'none',
      visemes: this.visemeSet,
      head: !!this.nodes.head,
      gesture: !!this.nodes.head,
      posture: this.detectedParts.has('torso'),
    };
  }
}
