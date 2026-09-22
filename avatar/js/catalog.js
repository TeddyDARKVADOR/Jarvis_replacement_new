/**
 * catalog.js — ce que CE corps peut faire, en JavaScript.
 *
 * Miroir de `presence/model.py` (le vocabulaire des gestes, leurs exigences,
 * la chaine de repli) et de `presence/catalog.py` (le tri contre le corps
 * charge). `presence/selftest.py` lit ce fichier et echoue a la moindre
 * difference.
 *
 * POURQUOI IL EXISTE
 *   Le labo tranchait seul ce qui est jouable, avec une regle a lui : « le
 *   geste demande ou `idle` ». Le panneau, lui, suit `FALLBACK_CHAIN`. Resultat
 *   mesurable : `greet` en mode visage, le labo annoncait `✗ wave injouable →
 *   idle` pendant que le panneau hochait la tete. Un labo qui montre un autre
 *   comportement que le produit est pire qu'absent — on le croit.
 *
 *   Le moteur s'en sert aussi : `gestures.js` refuse de jouer sous la nuque en
 *   mode visage, et doit le DIRE plutot que d'annoncer un geste qu'il remet a
 *   zero a l'image suivante.
 */

/** Tous les gestes que JARVIS a le droit de vouloir. Miroir de `Gesture`. */
export const GESTURES = [
  'idle', 'look_at_user', 'look_away', 'look_around', 'nod', 'shake_head',
  'tilt_head', 'lean_in', 'lean_back', 'blink_slow', 'sigh',
  'shrug', 'turn', 'bow', 'stretch',
  'wave', 'point', 'present', 'explain', 'think', 'thumbs_up', 'cross_arms',
  'facepalm', 'salute', 'type', 'count_off',
  'stand', 'sit', 'walk', 'step_aside',
];

/** Ce qu'un corps joue sans aucun clip. Miroir de `PROCEDURAL_GESTURES`. */
export const PROCEDURAL_GESTURES = new Set([
  'idle', 'look_at_user', 'look_away', 'look_around', 'nod', 'shake_head',
  'tilt_head', 'blink_slow', 'lean_in', 'lean_back', 'sigh', 'shrug',
  'turn', 'bow', 'stretch', 'think',
]);

/** Ce que chaque geste demande au rig. Miroir de `GESTURE_REQUIRES`. */
export const GESTURE_NEEDS = {
  idle: 'head', look_at_user: 'head', look_away: 'head', look_around: 'head',
  nod: 'head', shake_head: 'head', tilt_head: 'head', blink_slow: 'head',
  lean_in: 'torso', lean_back: 'torso', sigh: 'torso', shrug: 'torso',
  turn: 'torso', bow: 'torso', stretch: 'torso',
  wave: 'arms', point: 'arms', present: 'arms', explain: 'arms', think: 'arms',
  thumbs_up: 'arms', cross_arms: 'arms', facepalm: 'arms', salute: 'arms',
  type: 'arms', count_off: 'arms',
  stand: 'legs', sit: 'legs', walk: 'legs', step_aside: 'legs',
};

/** Ou un geste se rabat. Miroir de `FALLBACK_CHAIN`. */
export const FALLBACK_CHAIN = {
  facepalm: ['shake_head', 'look_away'],
  thumbs_up: ['nod'],
  salute: ['nod', 'bow'],
  count_off: ['explain', 'present'],
  explain: ['present', 'nod'],
  present: ['point', 'look_at_user'],
  point: ['turn', 'look_away'],
  think: ['tilt_head', 'look_away'],
  cross_arms: ['lean_back'],
  type: ['look_away'],
  shrug: ['tilt_head'],
  bow: ['nod'],
  stretch: ['lean_back'],
  sigh: ['lean_back', 'blink_slow'],
  wave: ['nod'],
  walk: ['turn'],
  step_aside: ['turn'],
  stand: ['lean_back'],
  sit: ['lean_back'],
  turn: ['look_away'],
  look_around: ['look_away'],
  lean_in: ['look_at_user'],
  lean_back: ['idle'],
  blink_slow: ['idle'],
  tilt_head: ['look_at_user'],
  shake_head: ['look_at_user'],
  nod: ['look_at_user'],
  look_away: ['idle'],
  look_at_user: ['idle'],
  idle: [],
};

/** Ce que chaque `rig.motion` accepte de piloter. Miroir de `_MOTION_PARTS`. */
export const MOTION_PARTS = {
  full: ['head', 'torso', 'arms', 'legs'],
  face: ['head'],
};

/** Le mot du manifeste, lu comme Python le lit : inconnu ou absent = full. */
export function motionOf(manifest) {
  const raw = String(((manifest && manifest.rig) || {}).motion || 'full').trim().toLowerCase();
  return MOTION_PARTS[raw] ? raw : 'full';
}

/**
 * L'inventaire d'un corps charge, comme `presence.catalog.Catalogue`.
 *
 * @param {Iterable<string>} detected  les membres que le modele A
 * @param {string} motion              ce qu'on accepte d'en bouger
 * @param {Iterable<string>} clips     les clips installes
 */
export class Catalogue {
  constructor(detected, motion = 'full', clips = []) {
    const has = new Set(detected && [...detected].length ? detected : ['head', 'torso']);
    this.motion = MOTION_PARTS[motion] ? motion : 'full';
    const driven = new Set([...has].filter((p) => MOTION_PARTS[this.motion].includes(p)));
    driven.add('head');   // la tete n'est jamais retirable : toute chaine y termine
    this.parts = driven;
    this.frozen = new Set([...has].filter((p) => !driven.has(p)));
    this.installed = new Set(clips || []);
  }

  can(gesture) {
    if (!this.parts.has(GESTURE_NEEDS[gesture] || 'head')) return false;
    return this.installed.has(gesture) || PROCEDURAL_GESTURES.has(gesture);
  }

  resolve(gesture) {
    if (this.can(gesture)) return gesture;
    for (const candidate of FALLBACK_CHAIN[gesture] || []) {
      if (this.can(candidate)) return candidate;
    }
    return 'idle';
  }

  get vocabulary() { return GESTURES.filter((g) => this.can(g)); }
}
