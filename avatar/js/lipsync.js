/**
 * lipsync.js — a mouth that moves with the voice, and the honest limit of that.
 *
 * WHAT IT HAS TO WORK WITH
 *   One number: `speech_level`, 0..1, the loudness of JARVIS's own voice,
 *   measured in the desktop client's playback loop — so it is in time with what
 *   is being heard. No phonemes: Gemini Live returns audio and a transcript of
 *   it, and the transcript is not time-aligned with the sound.
 *
 * WHAT ONE NUMBER CAN AND CANNOT BUY
 *   It CAN buy a mouth that opens and closes on the right syllables, at the
 *   right moments, with the right energy. It CANNOT buy the right shapes: an
 *   envelope carries no phonemes. What this file does is pick a viseme per
 *   syllable from the envelope's own shape — loud and rising is an open vowel,
 *   quiet and falling is a closure — which is wrong in detail and right in
 *   rhythm, and rhythm is what the eye reads.
 *
 * COARTICULATION, AND WHY IT IS A CROSSFADE
 *   A real mouth never snaps from one shape to the next: the lips are already
 *   rounding for the O while the A is still sounding. So each of the eight
 *   visemes has its own weight, rising toward 1 for the current one and
 *   falling for the others, and the mouth is their weighted mix. Switching
 *   from AA to O moves every coefficient smoothly between the two shapes —
 *   never through zero, which is what made the old mouth pop.
 *
 * THE MOUTH IS NOT THE FACE
 *   This file produces a MOUTH layer and a `speaking` signal, nothing else.
 *   `rig.js` decides how that layer meets the expression: the jaw and the lips
 *   belong to speech while he talks, the corners of a smile stay with the
 *   emotion. That is why a smile survives a sentence.
 *
 * TWO WAYS TO DRAW A VISEME
 *   Most models only have ARKit: the viseme is then approximated from ARKit
 *   shapes (`visemes.js`). Some — Ready Player Me, and the model installed
 *   here — also carry the Oculus viseme set, sculpted by their author. When the
 *   body says so (`capabilities().visemes === 'oculus'`), those are driven
 *   directly and the approximation is not written. Same rhythm, better shapes.
 *
 * THE UPGRADE PATH, WHICH IS A NEW INPUT AND NOT A REWRITE
 *   `setViseme(name, weight)` takes a phoneme from outside — our eight names
 *   or the fifteen Oculus ones — and, while it keeps arriving, the envelope
 *   stops choosing. Wire Audio2Face, a phoneme aligner on the TTS, or an ARKit
 *   stream into that one method; nothing above it changes.
 */

import { VISEMES } from './visemes.js';
import { Rng } from './rng.js';

/** Under this, JARVIS is not speaking and the mouth closes. */
const SILENCE = 0.045;

/** Minimum time on one viseme. 110 ms ~= the fastest real syllable. */
const MIN_HOLD = 0.11;

/** How long an externally supplied viseme is trusted before the envelope
 *  takes over again. */
const EXTERNAL_TTL = 2.0;

/** Vowels the envelope may choose between, open to closed. */
const OPEN = ['AA', 'E', 'O', 'I', 'U'];

/** Our eight, in a fixed order — the crossfade weights are indexed on it. */
export const VISEME_NAMES = Object.keys(VISEMES);

/** Our eight -> the Oculus set, for bodies that carry it. */
export const TO_OCULUS = {
  sil: 'sil', AA: 'aa', E: 'E', I: 'I', O: 'O', U: 'U', M: 'PP', F: 'FF',
};

/** The Oculus fifteen -> the nearest of our eight, for bodies that do not. */
export const FROM_OCULUS = {
  sil: 'sil', PP: 'M', FF: 'F', TH: 'F', DD: 'E', kk: 'E', CH: 'I', SS: 'I',
  nn: 'E', RR: 'O', aa: 'AA', E: 'E', I: 'I', O: 'O', U: 'U',
};

/** Crossfade speed between visemes: 90 % of the way in ~70 ms. */
const FADE_TAU = 0.03;
/** How fast `speaking` rises, and how long it takes to let go. The release
 *  outlasts the gaps between words, so a sentence reads as one utterance. */
const SPEAK_UP = 0.05;
const SPEAK_DOWN = 0.28;

export class LipSync {
  /**
   * @param {object} [rig]  when given, the mouth layer is pushed into it every
   *                        frame (`setViseme`, `setSpeaking`). The engine
   *                        passes it; a test can read `shapes` instead.
   * @param {Rng} [rng]
   */
  constructor(rig, rng) {
    this.rig = rig || null;
    this.rng = rng || new Rng();

    this.level = 0;
    this.smoothed = 0;
    this.previous = 0;

    this.current = 'sil';
    this.held = 0;

    this.external = null;
    this.externalAge = 0;

    /** Crossfade weight of each of our eight. */
    this.mix = Object.create(null);
    for (const name of VISEME_NAMES) this.mix[name] = name === 'sil' ? 1 : 0;
    this.openness = 0;

    /** Outputs of the frame, reused. */
    this.shapes = Object.create(null);     // ARKit approximation
    this.oculus = Object.create(null);     // native viseme weights
    this.speaking = 0;                     // 0..1, "is this a sentence"
  }

  /** From the wire, once per `Performance`, and from `speak()` at 25 Hz. */
  setLevel(level) {
    this.level = clamp01(level);
  }

  /**
   * From a real phoneme source. Takes precedence for `EXTERNAL_TTL` seconds.
   * @param {string} name   one of our eight, or an Oculus name
   * @param {number} weight 0..1
   */
  setViseme(name, weight) {
    const key = String(name || '').replace(/^viseme_/i, '');
    const ours = VISEMES[key] ? key : FROM_OCULUS[key];
    if (!ours) return;
    this.external = { name: ours, weight: clamp01(weight) };
    this.externalAge = 0;
  }

  update(dt) {
    if (this.external) {
      this.externalAge += dt;
      if (this.externalAge > EXTERNAL_TTL) this.external = null;
    }

    let target;
    let openness;
    let voiced;
    if (this.external) {
      target = this.external.name;
      openness = this.external.weight;
      voiced = target !== 'sil' && openness > 0.05;
    } else {
      // Attack fast, release slow: a mouth reaches an open vowel almost
      // instantly and closes over the tail of the sound.
      const tau = this.level > this.smoothed ? 0.025 : 0.085;
      this.smoothed += (this.level - this.smoothed) * (1 - Math.exp(-dt / tau));
      this.held += dt;

      if (this.smoothed < SILENCE) {
        this.current = 'sil';
      } else if (this.held >= MIN_HOLD) {
        const rising = this.smoothed > this.previous + 0.012;
        this.current = this._choose(this.smoothed, rising);
        this.held = 0;
      }
      this.previous = this.smoothed;
      target = this.current;
      voiced = this.smoothed >= SILENCE;
      // The opening follows the envelope, not the viseme: a loud syllable
      // opens the mouth more than a quiet one on the same sound.
      openness = target === 'sil' ? 0.25 : 0.35 + Math.min(1, this.smoothed * 2.4) * 0.65;
    }

    // `speaking`: rises with the voice, outlasts the gaps between words.
    const sTau = voiced ? SPEAK_UP : SPEAK_DOWN;
    this.speaking += ((voiced ? 1 : 0) - this.speaking) * (1 - Math.exp(-dt / sTau));
    if (this.speaking < 1e-3) this.speaking = 0;

    // The crossfade.
    const k = 1 - Math.exp(-dt / FADE_TAU);
    let total = 0;
    for (const name of VISEME_NAMES) {
      const want = name === target ? 1 : 0;
      this.mix[name] += (want - this.mix[name]) * k;
      total += this.mix[name];
    }
    this.openness += (openness - this.openness) * k;

    // The ARKit mix, and the native one, from the same weights.
    for (const key in this.shapes) delete this.shapes[key];
    for (const key in this.oculus) delete this.oculus[key];
    const norm = total > 1e-6 ? 1 / total : 0;
    // In silence the mouth layer fades to nothing: the expression owns a mouth
    // that is not speaking. `sil` only shapes the pauses INSIDE speech.
    const presence = this.speaking;
    for (const name of VISEME_NAMES) {
      const w = this.mix[name] * norm * this.openness * presence;
      if (w <= 0.002) continue;
      this.oculus[TO_OCULUS[name]] = w;
      const shape = VISEMES[name];
      for (const s in shape) {
        const v = shape[s] * w;
        if (v > 0.002) this.shapes[s] = Math.min(1, (this.shapes[s] || 0) + v);
      }
    }

    if (this.rig) {
      this.rig.setViseme(this.shapes, this.oculus);
      this.rig.setSpeaking(this.speaking);
    }
  }

  /**
   * One syllable's shape, from the envelope alone.
   *
   * Loud and rising -> an open vowel. Quiet and falling -> a closure. Between
   * them, a weighted pick so consecutive syllables differ: always choosing the
   * same shape for the same loudness produces a mouth that pumps.
   */
  _choose(level, rising) {
    if (!rising && level < 0.12) return this.rng.next() < 0.5 ? 'M' : 'F';

    const reach = Math.min(OPEN.length - 1, Math.floor((1 - level) * OPEN.length));
    let pick = OPEN[Math.max(0, reach)];
    if (pick === this.current) {
      pick = OPEN[(OPEN.indexOf(pick) + 1 + Math.floor(this.rng.next() * 2)) % OPEN.length];
    }
    return pick;
  }
}

function clamp01(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
}
