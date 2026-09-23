/**
 * gaze.js — ou vont les yeux, et comment ils y vont.
 *
 * CE QUE CE FICHIER REMPLACE
 *   Avant lui, le regard etait trois choses independantes qui ne se parlaient
 *   pas : huit coefficients `eyeLook*` noyes dans l'expression (donc soumis a
 *   SON rythme — un regard qui attend que la bouche de `thinking` ait fini de
 *   se composer), une rotation de tete lissee a part dans `gestures.js`, et
 *   des saccades ajoutees apres coup dans `rig.js`. Les yeux et la tete
 *   partaient ensemble, a la meme vitesse, et arrivaient ensemble : ce que
 *   fait une camera motorisee, pas un visage.
 *
 * CE QU'IL FAIT
 *   Un regard humain vers une cible laterale se fait en trois temps :
 *
 *       1. les yeux partent seuls, vite, et vont PRESQUE jusqu'a la cible
 *       2. la tete suit, avec ~80 ms de retard, lentement
 *       3. pendant qu'elle arrive, les yeux reviennent vers le centre de
 *          l'orbite : la direction totale ne bouge plus, seule la
 *          repartition oeil/tete change
 *
 *   Le temps 3 est le reflexe vestibulo-oculaire, et il sert aussi hors des
 *   changements de regard : quand la tete derive au repos ou hoche, les yeux
 *   compensent, et JARVIS garde le contact visuel au lieu de balayer la piece
 *   a chaque respiration.
 *
 * CE QUI N'EST JAMAIS DECIDE ICI
 *   La CIBLE. Elle vient de la Performance — donc de JARVIS, de son intention,
 *   de son etat ou du reflexe, dans cet ordre, deja tranche en Python. Ce
 *   fichier ajoute la vie par-dessus (saccades, coups d'oeil) et respecte une
 *   regle : un regard DECIDE (`gaze_source` explicit, intent ou safety) n'est
 *   jamais deplace par un comportement de fond. Les micro-saccades de fixation
 *   restent, parce qu'un oeil parfaitement immobile est un oeil de verre.
 *
 * LES UNITES
 *   x, y en « poids ARKit » : x > 0 vers la droite de l'ecran, y > 0 vers le
 *   haut. Verifie sur le modele installe : `eyeLookInLeft + eyeLookOutRight`
 *   et une rotation de tete +Y vont bien du meme cote.
 *   La tete en radians, comme `gestures.js` : +ry vers la droite de l'ecran,
 *   +rx vers le bas.
 */

import { springStep, omegaFor } from './performance.js';

/** Les huit formes du regard, dans l'ordre ou le rig les ecrit. */
export const EYE_SHAPES = [
  'eyeLookInLeft', 'eyeLookOutLeft', 'eyeLookUpLeft', 'eyeLookDownLeft',
  'eyeLookInRight', 'eyeLookOutRight', 'eyeLookUpRight', 'eyeLookDownRight',
];
const EYE_SET = new Set(EYE_SHAPES);
export const isEyeShape = (name) => EYE_SET.has(name);

/**
 * Course d'un oeil a poids 1, en radians. ~26 degres : la valeur par defaut de
 * `body_gltf.js` pour les yeux a os, et l'ordre de grandeur des formes ARKit.
 * Sert a convertir une rotation de tete en poids d'oeil equivalent.
 */
export const EYE_REACH_RAD = 0.45;
const K = 1 / EYE_REACH_RAD;

/** Les yeux : 90 % du chemin en 0.10 s. Une vraie saccade est plus rapide ;
 *  a la taille du panneau, plus rapide se lit comme un saut. */
const EYE_T90 = 0.10;
/** La tete : ~80 ms de latence, puis 90 % en 0.45 s. */
const HEAD_LATENCY = 0.08;
const HEAD_T90 = 0.45;
/** Part de la derive de tete que les yeux compensent. 1 = fixation parfaite,
 *  qui se lit comme un regard colle ; 0.75 garde la vie. */
const VOR_GAIN = 0.75;
/** Part d'un mouvement de tete fait EN REGARDANT que les yeux compensent.
 *
 *  Le reflexe vestibulo-oculaire humain a un gain proche de 1 ; 0.75 etait un
 *  choix pour la derive lente du repos, et il s'appliquait aussi a
 *  `investigate` + `gaze: user` — tete detournee de 17°, regard qui manquait
 *  l'utilisateur de plus de 4°, la largeur du cone ou l'on se sent regarde.
 *  Hocher en te regardant, c'est te regarder. */
const LOCKED_GAIN = 0.96;
/** La paupiere superieure suit l'oeil vers le bas. Rapport pris sur `Gaze.DOWN`
 *  de `presence/vocabulary.py` : 0.15 de paupiere pour 0.65 de regard. */
const LID_FOLLOW = 0.23;

/** Les regards DECIDES : aucun comportement de fond ne les deplace. */
const DECIDED = new Set(['explicit', 'intent', 'safety']);

/** Les huit poids du regard -> une direction. */
export function eyesToXY(shapes) {
  const w = (n) => {
    const v = shapes ? Number(shapes[n]) : 0;
    return Number.isFinite(v) ? v : 0;
  };
  return {
    x: (w('eyeLookInLeft') - w('eyeLookOutLeft') + w('eyeLookOutRight') - w('eyeLookInRight')) / 2,
    y: (w('eyeLookUpLeft') + w('eyeLookUpRight') - w('eyeLookDownLeft') - w('eyeLookDownRight')) / 2,
  };
}

/** Une direction -> les huit poids, ecrits dans `out`. */
export function xyToEyes(x, y, out) {
  const cx = Math.max(-1, Math.min(1, x));
  const cy = Math.max(-1, Math.min(1, y));
  out.eyeLookInLeft = cx > 0 ? cx : 0;
  out.eyeLookOutRight = cx > 0 ? cx : 0;
  out.eyeLookOutLeft = cx < 0 ? -cx : 0;
  out.eyeLookInRight = cx < 0 ? -cx : 0;
  out.eyeLookUpLeft = cy > 0 ? cy : 0;
  out.eyeLookUpRight = cy > 0 ? cy : 0;
  out.eyeLookDownLeft = cy < 0 ? -cy : 0;
  out.eyeLookDownRight = cy < 0 ? -cy : 0;
  return out;
}

export class GazeController {
  /** @param {import('./rng.js').Rng} rng */
  constructor(rng) {
    this.rng = rng;
    this.name = 'user';
    this.source = 'reflex';
    this.closed = false;

    this.target = { x: 0, y: 0 };           // ou les yeux finissent
    this.headTarget = { rx: 0, ry: 0 };     // la part de la tete, en radians
    this.headDelay = 0;

    this.eye = { x: { x: 0, v: 0 }, y: { x: 0, v: 0 } };
    this.head = { rx: { x: 0, v: 0 }, ry: { x: 0, v: 0 } };

    this.saccade = { x: 0, y: 0, tx: 0, ty: 0, next: 0 };
    this.glance = { x: 0, y: 0, tx: 0, ty: 0, until: 0, next: 4 };
    this.t = 0;

    /** Sorties de l'image : lues par `rig.js` et `gestures.js`. */
    this.out = { x: 0, y: 0 };
    this.headOut = { rx: 0, ry: 0 };
    this.lid = 0;
    /** Un grand changement de regard s'accompagne souvent d'un clignement. */
    this.wantsBlink = false;
    /** Pour le labo et les controles : quand a commence le dernier changement. */
    this.shiftAt = -1;
    /** Une echappee de debut de phrase est en cours (voir `avert`). */
    this.averting = false;
    this.averts = 0;
    /** Echappees reprises par un regard decide avant la fin — pour les controles. */
    this.avertsCancelled = 0;
  }

  /**
   * Une nouvelle cible.
   *
   * @param {string} name      le mot (`user`, `screen`, ...)
   * @param {object} shapes    les `eyeLook*` de la Performance — la direction
   *                           du regard ET celle que l'expression y ajoute
   *                           (la tristesse baisse les yeux)
   * @param {string} source    qui l'a choisi — voir DECIDED
   * @param {{rx:number, ry:number}} head  la part de la tete pour ce regard
   */
  setTarget(name, shapes, source, head) {
    const next = eyesToXY(shapes);
    const moved = Math.hypot(next.x - this.target.x, next.y - this.target.y);
    const headMoved = Math.abs((head ? head.ry : 0) - this.headTarget.ry)
                    + Math.abs((head ? head.rx : 0) - this.headTarget.rx);

    this.name = name || 'user';
    this.source = source || 'reflex';
    this.closed = this.name === 'closed';
    this.target = next;
    this.headTarget = { rx: head ? head.rx : 0, ry: head ? head.ry : 0 };

    if (moved > 0.05 || headMoved > 0.02) {
      this.headDelay = HEAD_LATENCY;
      this.shiftAt = this.t;
      // Clignement « evoque » : la plupart des grands deplacements du regard
      // s'accompagnent d'un clignement. Tire, pas systematique.
      if (moved + headMoved * K > 0.35 && this.rng.next() < 0.6) this.wantsBlink = true;
    }
    if (DECIDED.has(this.source)) {
      // Un regard decide annule le coup d'oeil en cours : il reprend la main
      // tout de suite, pas a la fin de la distraction.
      if (this.averting && this.t < this.glance.until) this.avertsCancelled += 1;
      this.glance.tx = this.glance.ty = 0;
      this.glance.until = 0;
      this.averting = false;
    }
  }

  get decided() { return DECIDED.has(this.source); }

  /**
   * Le regard s'echappe un instant — le debut d'un enonce (conversation.js).
   * C'est un coup d'oeil avec une CAUSE, pas un de plus : il remplace le
   * prochain coup d'oeil au hasard au lieu de s'y ajouter. Refuse sur un regard
   * decide, et pendant un balayage — sauf `allowIntent` : un regard que la
   * TABLE d'une intention a pose (pas JARVIS lui-meme), quand l'intention est
   * de developper. Voir `engine.js`.
   * @returns {boolean} le regard est parti
   */
  avert(x, y, duration, allowIntent = false) {
    const held = this.source === 'intent' ? !allowIntent : this.decided;
    if (held || this.closed || this.name === 'around') return false;
    const g = this.glance;
    g.tx = x;
    g.ty = y;
    g.until = this.t + duration;
    g.next = Math.max(g.next, 3);
    this.averting = true;
    this.averts += 1;
    return true;
  }

  /** La phrase finit : le regard revient, s'il etait parti. */
  endAversion() {
    const g = this.glance;
    if (this.t < g.until) g.until = this.t;
  }

  /**
   * Une image.
   *
   * @param {number} dt
   * @param {{rx:number, ry:number}} vor  la rotation de tete que les yeux
   *        doivent compenser — la derive du repos et les hochements, lus dans
   *        `gestures.js` a l'image precedente
   * @param {object} behaviour  les parametres de l'etat de presence, voir
   *        `states.js`
   */
  update(dt, vor, behaviour) {
    this.t += dt;
    const b = behaviour || {};

    // ── la tete, en retard sur les yeux ─────────────────────────────────
    if (this.headDelay > 0) this.headDelay = Math.max(0, this.headDelay - dt);
    const wOmega = omegaFor(HEAD_T90);
    const headGoalRx = this.headDelay > 0 ? this.head.rx.x : this.headTarget.rx;
    const headGoalRy = this.headDelay > 0 ? this.head.ry.x : this.headTarget.ry;
    springStep(this.head.rx, headGoalRx, wOmega, dt);
    springStep(this.head.ry, headGoalRy, wOmega, dt);
    this.headOut.rx = this.head.rx.x;
    this.headOut.ry = this.head.ry.x;

    if (this.closed) {
      // Yeux fermes : rien a viser, rien a compenser.
      springStep(this.eye.x, 0, omegaFor(0.3), dt);
      springStep(this.eye.y, 0, omegaFor(0.3), dt);
      this.saccade.x = this.saccade.y = 0;
      this.out.x = this.eye.x.x;
      this.out.y = this.eye.y.x;
      this.lid = 0;
      return;
    }

    this._glance(dt, b);
    this._saccade(dt, b);

    // ── les yeux : vers la cible, PLUS ce que la tete n'a pas encore fait ──
    // C'est ce terme qui fait partir les yeux les premiers, et revenir au
    // centre pendant que la tete arrive.
    const deficitRy = this.headTarget.ry - this.head.ry.x;
    const deficitRx = this.headTarget.rx - this.head.rx.x;
    const goalX = this.target.x + this.glance.x + deficitRy * K;
    const goalY = this.target.y + this.glance.y - deficitRx * K;
    const eOmega = omegaFor(EYE_T90);
    springStep(this.eye.x, goalX, eOmega, dt);
    springStep(this.eye.y, goalY, eOmega, dt);

    // ── le reflexe vestibulo-oculaire : la tete bouge, le regard tient ────
    const vRy = vor ? vor.ry || 0 : 0;
    const vRx = vor ? vor.rx || 0 : 0;
    const lRy = vor ? vor.lockRy || 0 : 0;
    const lRx = vor ? vor.lockRx || 0 : 0;
    const x = this.eye.x.x - (vRy * VOR_GAIN + lRy * LOCKED_GAIN) * K + this.saccade.x;
    const y = this.eye.y.x + (vRx * VOR_GAIN + lRx * LOCKED_GAIN) * K + this.saccade.y;

    this.out.x = clampFinite(x, -1, 1);
    this.out.y = clampFinite(y, -1, 1);
    this.lid = Math.max(0, -this.out.y) * LID_FOLLOW;
  }

  /**
   * Les coups d'oeil : de brefs ecarts qui reviennent. Jamais sur un regard
   * decide, jamais pendant un balayage (`around` est deja un mouvement).
   */
  _glance(dt, b) {
    const g = this.glance;
    const allowed = !this.decided && this.name !== 'around' && b.glanceEvery;
    if (this.averting && this.t < g.until) {
      // Une echappee de debut de phrase, acceptee par `avert()` : elle tient
      // sa duree. Sans cette branche, un regard pose par l'intention la
      // remettait a zero des l'image suivante — mesure : sous `explain`, 12
      // echappees commandees, 0 arrivee aux yeux.
    } else if (!allowed) {
      this.averting = false;
      g.tx = g.ty = 0;
    } else if (this.t >= g.until) {
      this.averting = false;
      g.tx = g.ty = 0;
      g.next -= dt;
      if (g.next <= 0) {
        const amp = b.glanceAmp || 0.25;
        // Surtout lateral, un peu vers le haut : on detourne les yeux pour
        // chercher un mot, rarement pour regarder ses pieds.
        g.tx = (this.rng.next() < 0.5 ? -1 : 1) * amp * this.rng.range(0.6, 1);
        g.ty = amp * this.rng.range(-0.2, 0.6);
        g.until = this.t + this.rng.range(0.4, 0.9);
        const [lo, hi] = b.glanceEvery;
        g.next = this.rng.range(lo, hi);
      }
    }
    const k = 1 - Math.exp(-dt / 0.06);
    g.x += (g.tx - g.x) * k;
    g.y += (g.ty - g.y) * k;
  }

  /** Micro-saccades de fixation. Toujours la, meme sur un regard decide. */
  _saccade(dt, b) {
    const s = this.saccade;
    s.next -= dt;
    if (s.next <= 0) {
      // Plus large quand le regard « cherche » : balayer, c'est bouger les
      // yeux. Regarder quelqu'un, c'est les garder presque fixes.
      const wide = this.name === 'around' || this.name === 'away';
      const amp = wide ? 0.22 : (b.saccadeAmp !== undefined ? b.saccadeAmp : 0.05);
      s.tx = this.rng.spread(amp);
      s.ty = this.rng.spread(amp * 0.6);
      const [lo, hi] = wide ? [0.3, 0.8] : (b.saccadeEvery || [0.6, 2.2]);
      s.next = this.rng.range(lo, hi);
    }
    const k = 1 - Math.exp(-dt / 0.045);   // quasi instantanees
    s.x += (s.tx - s.x) * k;
    s.y += (s.ty - s.y) * k;
  }
}

function clampFinite(v, lo, hi) {
  if (!Number.isFinite(v)) return 0;
  return v < lo ? lo : v > hi ? hi : v;
}
