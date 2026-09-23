/**
 * voice.js — lire une voix dans un seul nombre : son niveau.
 *
 * CE QU'ON A, ET CE QU'ON N'A PAS
 *   Deux niveaux arrivent dans le moteur : celui de la voix de JARVIS (25 Hz,
 *   `speak()`) et celui du micro de l'utilisateur (15 Hz, `listen()`). Aucun
 *   phoneme, aucune hauteur, aucun mot. Ce qu'un niveau porte honnetement :
 *   quand une syllabe commence, quand elle culmine, si elle est plus forte que
 *   ses voisines (un appui), quand une pause commence et combien de temps on a
 *   parle avant. C'est exactement ce dont les signaux de conversation ont
 *   besoin, et rien de plus n'est pretendu.
 *
 * POURQUOI UN PLANCHER DE BRUIT
 *   Le micro entend la piece. Un seuil fixe prendrait un ventilateur pour une
 *   phrase. Le plancher suit le minimum recent, lentement vers le haut et vite
 *   vers le bas : un bruit stable disparait, une voix ne disparait pas.
 */

/** Un silence plus long que ca est une pause, pas l'espace entre deux mots. */
export const PAUSE_S = 0.25;

export class VoiceEnvelope {
  /**
   * @param {object} [o]
   * @param {number} [o.threshold]   au-dessus du plancher : c'est de la voix
   * @param {number} [o.rise]        montee minimale depuis le creux : une syllabe
   * @param {boolean} [o.floor]      suivre un plancher de bruit (le micro)
   */
  constructor({ threshold = 0.045, rise = 0.1, floor = false } = {}) {
    this.threshold = threshold;
    this.riseMin = rise;
    this.trackFloor = floor;

    this.level = 0;          // le dernier niveau recu
    this.smoothed = 0;
    this.noise = 0;          // le plancher
    this.valley = 0;         // le creux depuis le dernier pic
    this.peak = 0;           // le sommet de la syllabe en cours
    this.rising = false;
    this.meanPeak = 0.3;     // la moyenne glissante des sommets : l'echelle d'un appui
    this.voiced = false;
    this.voicedFor = 0;      // depuis quand on parle (s), pauses courtes comprises
    this.silentFor = 10;     // depuis quand on se tait (s)
    this.spokeFor = 0;       // la duree de parole avant la pause en cours
    this.loudAgo = 10;       // depuis quand le niveau etait franchement au-dessus

    /** Les evenements de l'image — lus puis oublies par l'appelant. */
    this.onset = false;
    this.peaked = false;
    this.ratio = 1;          // sommet / moyenne, a l'image `peaked`
    this.pauseStarted = false;
  }

  set(level) {
    const n = Number(level);
    this.level = Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
  }

  update(dt) {
    this.onset = false;
    this.peaked = false;
    this.pauseStarted = false;

    const tau = this.level > this.smoothed ? 0.03 : 0.08;
    this.smoothed += (this.level - this.smoothed) * (1 - Math.exp(-dt / tau));
    const x = this.smoothed;

    if (this.trackFloor) {
      // Vite vers le bas, tres lent vers le haut : 90 % d'une montee de bruit
      // en ~20 s, alors qu'une phrase dure quelques secondes.
      const k = x < this.noise ? 1 - Math.exp(-dt / 0.3) : 1 - Math.exp(-dt / 9);
      this.noise += (x - this.noise) * k;
    }
    const above = x - (this.trackFloor ? this.noise : 0);

    const voicedNow = above > this.threshold;
    this.loudAgo = above > this.threshold * 2.5 ? 0 : this.loudAgo + dt;
    if (voicedNow) {
      this.silentFor = 0;
      this.voicedFor += dt;
    } else {
      this.silentFor += dt;
      // Une pause de plus de 250 ms est une pause ; en dessous, c'est l'espace
      // entre deux mots, qui ne coupe pas l'enonce.
      if (this.silentFor >= PAUSE_S && this.voicedFor > 0) {
        this.spokeFor = this.voicedFor;
        this.voicedFor = 0;
        // Une pause est une CHUTE : la voix etait franchement la il y a un
        // instant. Un plancher de bruit qui rattrape un bruit stable fait
        // passer le niveau sous le seuil tout doucement — ce n'est pas une
        // pause, et le prendre pour une valait un hochement au ventilateur.
        this.pauseStarted = this.loudAgo <= PAUSE_S + 0.35;
      }
    }
    this.voiced = voicedNow;

    // Syllabes : un creux, une montee franche, un sommet.
    if (!this.rising) {
      this.valley = Math.min(this.valley, above);
      if (voicedNow && above > this.valley + this.riseMin) {
        this.rising = true;
        this.onset = true;
        this.peak = above;
      }
    } else if (above >= this.peak) {
      this.peak = above;
    } else if (above < this.peak - 0.04) {
      this.rising = false;
      this.peaked = true;
      this.ratio = this.peak / Math.max(0.05, this.meanPeak);
      this.meanPeak += (this.peak - this.meanPeak) * 0.18;
      this.valley = above;
    }
    if (!voicedNow && !this.rising) this.valley = Math.min(this.valley, Math.max(0, above));
  }
}
