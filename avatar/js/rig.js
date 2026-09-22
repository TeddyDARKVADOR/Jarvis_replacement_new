/**
 * rig.js — the 52 coefficients, and the order in which everything may touch them.
 *
 * WHAT THIS OWNS
 *   The last word on every ARKit shape. Seven layers arrive; one value per
 *   shape leaves, once per frame, through `apply(name, weight)`. Nothing here
 *   decides a face — the director chose it. This decides who wins when two
 *   things want the same shape, and that is the part that was missing.
 *
 * THE PRIORITY, HIGHEST FIRST
 *
 *   safety       Gaze.CLOSED shuts the lids whatever anything says. A JARVIS
 *                asleep with one eye open is worse than any expression bug.
 *   explicit     `setOverride` — a lab slider, a test pinning a shape. It is
 *                what a human asked for by hand, so it beats the engine.
 *   speech       while he talks, the JAW and LIPS belong to the lip-sync. The
 *                expression's articulators are faded, its CORNERS are kept:
 *                a smile survives a sentence, a surprised open jaw does not
 *                stop him closing his mouth on an M.
 *   intentional  the accent of the intent (`accents.js`) — a brow flash.
 *   emotional    the expression, as `performance.js` times it.
 *   idle         micro-expressions. Scaled down when the face is already
 *                saying something strongly, removed from any shape the
 *                expression is using, and from the mouth while he speaks —
 *                a micro-expression must never overwrite a decision.
 *
 *   Eyes and lids sit beside this list rather than in it: the gaze controller
 *   (`gaze.js`) owns the eight `eyeLook*` shapes outright, and the lids are the
 *   max of the expression, the blink and the lid that follows a downward gaze.
 *   A blink therefore cannot touch the mouth, and nothing but safety and an
 *   explicit override can cancel a blink.
 *
 * WHY max() AND NOT A SUM, WITHIN A PRIORITY
 *   Two layers that both raise a brow must not add up past 1.0 and clip, and a
 *   brow flash on top of a smile must not erase the smile. max() gives each
 *   layer its say without either eating the other.
 *
 * WHY BLINKING AND SACCADES LIVE HERE AND NOT IN PYTHON
 *   They are 60 fps phenomena with no decision content. Sending a blink over a
 *   websocket would spend 200 ms of round trip to close an eyelid, and a face
 *   that blinks only when the server speaks is obviously being puppeted.
 *
 * THE BOUNDARY
 *   Everything that leaves goes through `clean()`: a NaN, an Infinity, a -0.3
 *   or a 7 written into a morph target is a mesh exploding on screen, and the
 *   cheapest place to make that impossible is the one line every value
 *   crosses. And a value that did not change is not written again — 52 writes
 *   per frame, most of them identical, were the largest avoidable cost here.
 */

import { FacialPerformance } from './performance.js';
import { GazeController, EYE_SHAPES, isEyeShape, xyToEyes } from './gaze.js';
import { Rng } from './rng.js';

/** Eyelid shapes: driven by the blink generator, never smoothed like the rest. */
const LIDS = ['eyeBlinkLeft', 'eyeBlinkRight'];

/** The shapes speech owns while he talks: the jaw and what shapes the lips. */
export const ARTICULATORS = new Set([
  'jawOpen', 'jawForward', 'jawLeft', 'jawRight',
  'mouthClose', 'mouthFunnel', 'mouthPucker', 'mouthLeft', 'mouthRight',
  'mouthRollLower', 'mouthRollUpper', 'mouthShrugLower', 'mouthShrugUpper',
  'mouthPressLeft', 'mouthPressRight',
  'mouthLowerDownLeft', 'mouthLowerDownRight', 'mouthUpperUpLeft', 'mouthUpperUpRight',
  'mouthStretchLeft', 'mouthStretchRight', 'tongueOut',
]);

/** The mouth shapes that carry emotion rather than sound: they stay. */
export const EMOTIVE_MOUTH = new Set([
  'mouthSmileLeft', 'mouthSmileRight', 'mouthFrownLeft', 'mouthFrownRight',
  'mouthDimpleLeft', 'mouthDimpleRight', 'cheekSquintLeft', 'cheekSquintRight',
  'cheekPuff',
]);

/** How much of the expression's articulators speech takes, at full speech. */
const SPEECH_TAKES = 0.85;
/** How much of a smile's corners speech takes. A little: talking flattens a
 *  grin, it does not erase it. */
const SPEECH_TAKES_EMOTIVE = 0.2;

/** Under this change, a write is skipped. Far below one pixel on any mesh. */
const WRITE_EPSILON = 1e-4;

/** A value a morph target can take, whatever arrived. */
export function clean(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n >= 1 ? 1 : n;
}

export class Rig {
  /**
   * @param {(name: string, weight: number) => void} apply
   *        Writes one resolved coefficient to whatever body is loaded.
   * @param {object} [options]
   * @param {Rng}    [options.rng]
   * @param {(name: string, weight: number) => void} [options.applyViseme]
   *        Writes a NATIVE viseme (Oculus name) when the body has them. Absent
   *        = the ARKit approximation is written instead.
   */
  constructor(apply, options = {}) {
    this.apply = apply;
    this.applyViseme = typeof options.applyViseme === 'function' ? options.applyViseme : null;
    const rng = options.rng || new Rng();
    this.rng = rng.fork ? rng.fork('rig') : rng;

    this.performance = new FacialPerformance();
    this.gazeCtl = new GazeController(this.rng.fork('gaze'));

    // Les couches, telles qu'elles arrivent. Aucune n'est copiee par image.
    this.expression = this.performance.live;
    this.viseme = Object.create(null);
    this.nativeViseme = Object.create(null);
    this.micro = Object.create(null);
    this.accent = Object.create(null);
    this.override = null;
    this.speaking = 0;
    this.behaviour = null;
    this.vor = { rx: 0, ry: 0 };

    // Ce qui est calcule, et ce qui est ecrit.
    this.current = Object.create(null);   // la sortie de l'image, avant ecriture
    this.written = Object.create(null);   // ce que le modele a reellement recu
    this.writtenViseme = Object.create(null);
    this._eyes = Object.create(null);
    this.writes = 0;                      // compteur, pour les metriques

    this.gaze = 'user';
    this.gazeSource = 'reflex';
    this.closed = false;
    this.microGain = 1;

    // ── blink ────────────────────────────────────────────────────────────
    // Humans blink every 2–10 s, not on a timer. A periodic blink is uncanny in
    // a way people notice without being able to name.
    this.nextBlink = this.rng.range(1.5, 5.5);
    this.blinkPhase = -1;     // <0 : pas de clignement en cours
    this.blinkSpeed = 1;
    this.slowBlink = false;
    this.pendingDouble = -1;  // >0 : un second clignement est programme
    this.blinks = 0;          // compteur, pour les controles

    this.t = 0;
  }

  /** Les micro-saccades, lues par `avatar/checks/idle_motion.py`. */
  get saccade() { return this.gazeCtl.saccade; }

  /**
   * A `Performance`'s blendshapes, straight off the wire.
   *
   * The eight `eyeLook*` go to the gaze controller; everything else to the
   * performance layer. `expression`, `holdS`, `gazeSource` and `gazeHead` are
   * optional, and their absence gives the old behaviour: everything together,
   * nothing released, gaze treated as derived.
   */
  setExpression(blendshapes, gaze, expression, holdS, gazeSource, gazeHead) {
    const face = Object.create(null);
    const eyes = Object.create(null);
    for (const name in (blendshapes || {})) {
      const v = clean(blendshapes[name]);
      if (v <= 0) continue;
      if (isEyeShape(name)) eyes[name] = v;
      else face[name] = v;
    }
    this.performance.setTarget(face, expression, holdS);
    if (gaze) {
      this.gaze = gaze;
      this.closed = gaze === 'closed';
    }
    this.gazeSource = gazeSource || 'reflex';
    this.gazeCtl.setTarget(this.gaze, eyes, this.gazeSource, gazeHead);
  }

  /** The mouth layer, from lipsync.js. `native` = Oculus weights, if any. */
  setViseme(shapes, native) {
    this.viseme = shapes || Object.create(null);
    this.nativeViseme = native || Object.create(null);
  }

  /** 0..1: is he in the middle of a sentence. Decides who owns the mouth. */
  setSpeaking(level) {
    const n = Number(level);
    this.speaking = Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
  }

  /** Micro-expressions from the idle layer (idle.js). */
  setMicro(shapes) {
    this.micro = shapes || Object.create(null);
  }

  /** The accent of the current intent (accents.js). */
  setAccent(shapes) {
    this.accent = shapes || Object.create(null);
  }

  /**
   * Explicit values, beating everything but safety. `null` hands back.
   * A shape set here is written as given — the lab's sliders use this, and a
   * slider that the engine could quietly overrule would be a slider that lies.
   */
  setOverride(shapes) {
    if (!shapes) { this.override = null; return; }
    const clean_ = Object.create(null);
    for (const name in shapes) clean_[name] = clean(shapes[name]);
    this.override = Object.keys(clean_).length ? clean_ : null;
  }

  /** Slow, heavy blinks — TIRED and SLEEPING ask for these. */
  setSlowBlink(on) {
    this.slowBlink = !!on;
  }

  /** The presence state's background behaviour (states.js). */
  setBehaviour(behaviour) {
    this.behaviour = behaviour;
  }

  /** The head rotation the eyes must compensate for, from gestures.js. */
  setVor(rx, ry) {
    this.vor.rx = Number.isFinite(rx) ? rx : 0;
    this.vor.ry = Number.isFinite(ry) ? ry : 0;
  }

  update(dt) {
    this.t += dt;
    const b = this.behaviour || {};

    this.expression = this.performance.update(dt);
    this.gazeCtl.update(dt, this.vor, b);
    this._blink(dt, b);

    // Le repos se tait devant un visage qui dit deja quelque chose.
    const strength = this.performance.strength();
    const wantGain = (b.microGain !== undefined ? b.microGain : 1)
                   * Math.max(0.15, Math.min(1, 1 - 1.2 * strength));
    this.microGain += (wantGain - this.microGain) * (1 - Math.exp(-dt / 0.3));

    const out = this.current;
    for (const name in out) out[name] = 0;
    const s = this.speaking;

    // emotional
    for (const name in this.expression) {
      let v = this.expression[name];
      if (ARTICULATORS.has(name)) v *= 1 - SPEECH_TAKES * s;
      else if (EMOTIVE_MOUTH.has(name)) v *= 1 - SPEECH_TAKES_EMOTIVE * s;
      out[name] = v;
    }
    // idle, under the emotional layer and never in its place
    for (const name in this.micro) {
      if ((this.expression[name] || 0) > 0.05) continue;
      if (s > 0.05 && (ARTICULATORS.has(name) || EMOTIVE_MOUTH.has(name))) continue;
      const v = this.micro[name] * this.microGain;
      if (v > (out[name] || 0)) out[name] = v;
    }
    // intentional
    for (const name in this.accent) {
      const v = this.accent[name];
      if (v > (out[name] || 0)) out[name] = v;
    }
    // speech — the ARKit approximation, unless the body draws visemes itself
    if (!this.applyViseme) {
      for (const name in this.viseme) {
        const v = this.viseme[name];
        if (v > (out[name] || 0)) out[name] = v;
      }
    }

    // lids: expression, blink, and the lid that follows a downward gaze
    const lid = Math.max(this._lidWeight(), this.gazeCtl.lid);
    for (const name of LIDS) {
      if (lid > (out[name] || 0)) out[name] = lid;
    }

    // eyes: the gaze controller, outright
    xyToEyes(this.gazeCtl.out.x, this.gazeCtl.out.y, this._eyes);
    for (const name of EYE_SHAPES) out[name] = this._eyes[name];

    // explicit
    if (this.override) {
      for (const name in this.override) out[name] = this.override[name];
    }
    // safety
    if (this.closed) {
      for (const name of LIDS) out[name] = 1;
    }

    this._write(out);
  }

  _write(out) {
    const written = this.written;
    for (const name in out) {
      const v = clean(out[name]);
      out[name] = v;
      const had = written[name];
      if (had === undefined ? v > 0 : Math.abs(v - had) > WRITE_EPSILON || (v === 0 && had !== 0)) {
        this.apply(name, v);
        written[name] = v;
        this.writes += 1;
      }
    }
    if (this.applyViseme) {
      const w = this.writtenViseme;
      for (const name in this.nativeViseme) {
        const v = clean(this.nativeViseme[name]);
        if (w[name] === undefined || Math.abs(v - w[name]) > WRITE_EPSILON) {
          this.applyViseme(name, v);
          w[name] = v;
          this.writes += 1;
        }
      }
      for (const name in w) {
        if (!(name in this.nativeViseme) && w[name] !== 0) {
          this.applyViseme(name, 0);
          w[name] = 0;
          this.writes += 1;
        }
      }
    }
  }

  /** Force the next frame to write everything again (a new body, a freeze). */
  invalidate() {
    this.written = Object.create(null);
    this.writtenViseme = Object.create(null);
  }

  /**
   * Why is this shape at this value? Every layer's say, and the winner.
   *
   * Recomputed from the layers as they stand, so it answers for the frame
   * that was just written — which is the question the lab and the replay ask.
   */
  explain(name) {
    const s = this.speaking;
    const layers = {
      expression: this.expression[name] || 0,
      accent: this.accent[name] || 0,
      micro: (this.micro[name] || 0) * this.microGain,
      viseme: this.applyViseme ? 0 : (this.viseme[name] || 0),
      lid: LIDS.includes(name) ? Math.max(this._lidWeight(), this.gazeCtl.lid) : 0,
      gaze: isEyeShape(name) ? (this._eyes[name] || 0) : 0,
      override: this.override && name in this.override ? this.override[name] : null,
      safety: this.closed && LIDS.includes(name) ? 1 : null,
    };
    if (ARTICULATORS.has(name)) layers.expression *= 1 - SPEECH_TAKES * s;
    else if (EMOTIVE_MOUTH.has(name)) layers.expression *= 1 - SPEECH_TAKES_EMOTIVE * s;
    const final = this.written[name] || 0;
    let winner = 'none';
    if (layers.safety !== null) winner = 'safety';
    else if (layers.override !== null) winner = 'explicit';
    else if (isEyeShape(name)) winner = 'gaze';
    else {
      let best = -1;
      for (const key of ['expression', 'accent', 'micro', 'viseme', 'lid']) {
        if (layers[key] > best + 1e-6) { best = layers[key]; winner = best > 0 ? key : 'none'; }
      }
    }
    return { name, final, winner, speaking: s, layers };
  }

  // ── life ──────────────────────────────────────────────────────────────────

  _blink(dt, b) {
    if (this.closed) { this.blinkPhase = -1; this.gazeCtl.wantsBlink = false; return; }

    if (this.blinkPhase >= 0) {
      this.blinkPhase += dt * this.blinkSpeed;
      if (this.blinkPhase >= 1) this.blinkPhase = -1;
      this.gazeCtl.wantsBlink = false;
      return;
    }
    if (this.pendingDouble > 0) {
      this.pendingDouble -= dt;
      if (this.pendingDouble <= 0) { this.pendingDouble = -1; this._startBlink(b, true); }
      return;
    }
    // Un grand deplacement du regard entraine souvent un clignement — mais pas
    // deux clignements coup sur coup.
    if (this.gazeCtl.wantsBlink) {
      this.gazeCtl.wantsBlink = false;
      if (this.nextBlink < this._meanInterval(b) * 0.85) { this._startBlink(b, false); return; }
    }
    this.nextBlink -= dt;
    if (this.nextBlink <= 0) this._startBlink(b, false);
  }

  _meanInterval(b) {
    const perMin = this.slowBlink ? 30 : (b.blinkPerMin !== undefined ? b.blinkPerMin : 17);
    return 60 / Math.max(1, perMin);
  }

  _startBlink(b, isSecond) {
    this.blinkPhase = 0;
    this.blinks += 1;
    this.blinkSpeed = this.slowBlink ? 2.2 : 7.0;   // 1/duree du clignement
    const mean = this._meanInterval(b);
    // Irregulier : entre 0.4 et 1.6 fois la moyenne, jamais sur une horloge.
    this.nextBlink = mean * this.rng.range(0.4, 1.6);
    if (!isSecond && !this.slowBlink && this.rng.next() < (b.doubleBlink || 0)) {
      this.pendingDouble = this.rng.range(0.12, 0.22) + 1 / this.blinkSpeed;
    }
  }

  _lidWeight() {
    if (this.closed) return 1;
    if (this.blinkPhase < 0) return 0;
    // Fermeture rapide, ouverture plus lente — c'est ainsi qu'une paupiere
    // bouge, et sin() donnerait le contraire.
    const p = this.blinkPhase;
    return p < 0.35 ? p / 0.35 : 1 - (p - 0.35) / 0.65;
  }
}
