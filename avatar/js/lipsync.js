/**
 * lipsync.js — a mouth that moves with the voice, and the honest limit of that.
 *
 * WHAT IT HAS TO WORK WITH
 *   One number: `speech_level`, 0..1, the loudness of JARVIS's own voice. The
 *   desktop client already measures it in the playback loop rather than on the
 *   socket — `client_desktop/state.py` explains why — so it is in time with
 *   what is being heard, which is the one property lip sync cannot do without.
 *
 * WHAT ONE NUMBER CAN AND CANNOT BUY
 *   It CAN buy a mouth that opens and closes on the right syllables, at the
 *   right moments, with the right energy. At the size this panel is actually
 *   read, that is indistinguishable from lip sync.
 *
 *   It CANNOT buy the right *shapes*: an envelope carries no phonemes, so "bat"
 *   and "boot" look identical. Pretending otherwise by picking visemes at
 *   random produces a mouth that chews. What this file does instead is pick a
 *   viseme per syllable from the envelope's own shape — loud and rising is an
 *   open vowel, quiet and falling is a closure — which is wrong in detail and
 *   right in rhythm, and rhythm is what the eye reads.
 *
 * THE UPGRADE PATH, WHICH IS A NEW INPUT AND NOT A REWRITE
 *   `setViseme(name, weight)` takes a phoneme from outside and, from that
 *   moment, the envelope stops choosing. Wire NVIDIA Audio2Face-3D, a phoneme
 *   aligner on the TTS, or an ARKit stream from a phone camera into that one
 *   method and this file becomes a smoother with no opinions. Nothing above it
 *   changes — which is the whole reason the seam is here on day one.
 *
 * WHY SYLLABLES AND NOT FRAMES
 *   Choosing a new viseme every frame gives a 60 Hz flutter. Real speech
 *   changes mouth shape 4–7 times a second, and the envelope's rising edges are
 *   a good enough syllable detector to hit that rate.
 */

import { VISEMES } from './visemes.js';

/** Under this, JARVIS is not speaking and the mouth closes. */
const SILENCE = 0.045;

/** Minimum time on one viseme. 110 ms ~= the fastest real syllable. */
const MIN_HOLD = 0.11;

/** How long an externally supplied viseme is trusted before the envelope
 *  takes over again. Two seconds: long enough to cover a dropped frame from
 *  Audio2Face, short enough that a crashed source unfreezes the mouth. */
const EXTERNAL_TTL = 2.0;

/** Vowels the envelope may choose between, open to closed. */
const OPEN = ['AA', 'E', 'O', 'I', 'U'];

export class LipSync {
  constructor(rig) {
    this.rig = rig;

    this.level = 0;
    this.smoothed = 0;
    this.previous = 0;

    this.current = 'sil';
    this.held = 0;

    this.external = null;
    this.externalAge = 0;
  }

  /** From the wire, once per `Performance`. */
  setLevel(level) {
    this.level = clamp01(level);
  }

  /**
   * From a real phoneme source. Takes precedence for `EXTERNAL_TTL` seconds.
   * @param {string} name  an entry in VISEMES
   * @param {number} weight 0..1
   */
  setViseme(name, weight) {
    if (!VISEMES[name]) return;
    this.external = { name, weight: clamp01(weight) };
    this.externalAge = 0;
  }

  update(dt) {
    if (this.external) {
      this.externalAge += dt;
      if (this.externalAge > EXTERNAL_TTL) this.external = null;
    }

    if (this.external) {
      this.rig.setViseme(scale(VISEMES[this.external.name], this.external.weight));
      return;
    }

    // Attack fast, release slow: a mouth reaches an open vowel almost
    // instantly and closes over the tail of the sound. Equal constants give a
    // mouth that lags the start of every word.
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

    // L'ouverture suit l'enveloppe, pas le viseme : c'est ce qui fait qu'une
    // syllabe forte ouvre plus la bouche qu'une syllabe faible sur le meme son.
    const openness = this.current === 'sil'
      ? 0.25
      : 0.35 + Math.min(1, this.smoothed * 2.4) * 0.65;

    this.rig.setViseme(scale(VISEMES[this.current], openness));
  }

  /**
   * One syllable's shape, from the envelope alone.
   *
   * Loud and rising -> an open vowel. Quiet and falling -> a closure. Between
   * them, a weighted pick so consecutive syllables differ: always choosing the
   * same shape for the same loudness produces a mouth that pumps.
   */
  _choose(level, rising) {
    if (!rising && level < 0.12) return Math.random() < 0.5 ? 'M' : 'F';

    // Plus c'est fort, plus on va vers le debut de OPEN (voyelles ouvertes).
    const reach = Math.min(OPEN.length - 1, Math.floor((1 - level) * OPEN.length));
    let pick = OPEN[Math.max(0, reach)];
    if (pick === this.current) {
      pick = OPEN[(OPEN.indexOf(pick) + 1 + Math.floor(Math.random() * 2)) % OPEN.length];
    }
    return pick;
  }
}

function scale(shape, weight) {
  const out = Object.create(null);
  for (const name in shape) {
    const w = shape[name] * weight;
    if (w > 0.004) out[name] = Math.min(1, w);
  }
  return out;
}

function clamp01(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
}
