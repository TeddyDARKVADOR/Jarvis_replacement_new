/**
 * rng.js — le hasard du visage, avec une graine.
 *
 * POURQUOI LE MOTEUR N'APPELLE PLUS Math.random()
 *   Un clignement, une saccade, une micro-expression, le choix d'une voyelle
 *   par le lip-sync : tout ce qui rend le visage vivant est tire au hasard, et
 *   c'est voulu. Mais un hasard qu'on ne peut pas rejouer rend une question
 *   impossible : « pourquoi le visage etait-il dans cet etat a 12:42:11 ? ».
 *   Pour y repondre il faut pouvoir refaire EXACTEMENT la meme seance — memes
 *   decisions, memes images, memes tirages.
 *
 *   Chaque sous-systeme recoit donc son propre generateur, derive d'une graine
 *   unique. En production la graine est tiree au demarrage (le visage reste
 *   imprevisible d'une session a l'autre) ; en rejeu ou en test elle est
 *   fixee, et deux executions ecrivent les memes coefficients a la meme image.
 *
 * POURQUOI UN GENERATEUR PAR SOUS-SYSTEME ET PAS UN SEUL
 *   Avec un seul flux, ajouter un tirage dans `idle.js` decalerait tous les
 *   clignements de `rig.js` : deux enregistrements d'avant et d'apres ne
 *   seraient plus comparables pour une raison sans rapport avec ce qu'on
 *   compare. `fork(nom)` donne a chacun sa sequence, stable tant que son propre
 *   code ne change pas.
 */

/** mulberry32 — 32 bits d'etat, periode 2^32, assez pour des paupieres. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Un nom -> 32 bits, pour deriver une sous-graine lisible. */
function hash(text) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

export class Rng {
  /** @param {number} [seed]  absent = tire au hasard, une fois. */
  constructor(seed) {
    this.seed = Number.isFinite(seed) ? (seed >>> 0) : ((Math.random() * 4294967296) >>> 0);
    this._next = mulberry32(this.seed);
  }

  /** [0, 1) */
  next() { return this._next(); }

  /** [lo, hi) */
  range(lo, hi) { return lo + (hi - lo) * this._next(); }

  /** [-a, a) */
  spread(a) { return (this._next() * 2 - 1) * a; }

  /** Un element au hasard. */
  pick(list) { return list[Math.floor(this._next() * list.length) % list.length]; }

  /** Un generateur independant, stable pour ce nom et cette graine. */
  fork(name) { return new Rng((this.seed ^ hash(String(name))) >>> 0); }
}
