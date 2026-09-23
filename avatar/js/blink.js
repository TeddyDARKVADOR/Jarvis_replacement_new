/**
 * blink.js — les paupieres, et POURQUOI elles se ferment.
 *
 * POURQUOI UN CONTROLEUR A PART
 *   Le clignement vivait dans `rig.js`, sur un minuteur : un intervalle tire
 *   uniformement entre 0.4 et 1.6 fois la moyenne, une duree fixe, et une seule
 *   cause exterieure (un grand deplacement du regard). Mesure sur 10 minutes :
 *   coefficient de variation 0.45, et jamais un intervalle au-dela de 1.75 fois
 *   la moyenne — donc jamais un regard qui tient.
 *
 *   Chez un humain, deux choses different. L'intervalle est asymetrique :
 *   beaucoup de clignements rapproches, quelques longues pauses (les
 *   distributions mesurees sont etirees vers la droite). Et une bonne partie
 *   des clignements ont une CAUSE : un changement de regard, la fin d'une
 *   phrase (on cligne aux ponctuations, en parlant comme en ecoutant — Nakano
 *   & Kitazawa 2010), la fin d'une surprise, pendant laquelle on ne cligne
 *   presque pas.
 *
 * CE QU'IL FAIT
 *   Un rythme spontane, log-normal autour de la moyenne de l'etat de presence.
 *   Des DEMANDES causees (`request`), acceptees si elles ne font pas papillonner
 *   la paupiere. Une INHIBITION (`inhibit`) — les yeux grands ouverts d'une
 *   surprise — a la fin de laquelle un clignement est probable. Chaque
 *   clignement dit sa cause, et les compteurs servent aux controles : un
 *   clignement « a la fin de la phrase » qu'on ne peut pas compter est un
 *   clignement qu'on croit avoir.
 *
 * CE QU'IL NE FAIT PAS
 *   Il ne decide pas d'une paupiere tombante (tristesse, fatigue) : c'est
 *   l'expression, dans `rig.js`, qui en prend le max. Et il ne cligne jamais
 *   sur une horloge : aucune cause n'est jamais acceptee a coup sur.
 */

/** Deux clignements plus proches que ca papillonnent — sauf un double voulu. */
export const MIN_GAP_S = 0.35;
/** Dispersion (log) de l'intervalle spontane. 0.5 -> CV ~0.53. */
const SIGMA = 0.5;
/** Un intervalle reste dans [LO, HI] fois la moyenne : ni rafale, ni statue. */
const LO = 0.3;
const HI = 3.2;

/** Probabilite par defaut de chaque cause. Aucune n'est certaine. */
export const CAUSES = {
  spontaneous: 1,
  double: 1,
  gaze: 0.6,             // decide deja par gaze.js, qui tire lui-meme
  utterance_end: 0.55,   // il finit sa phrase
  listener_pause: 0.35,  // l'utilisateur marque une pause
  after_surprise: 0.6,   // les yeux grands ouverts se relachent
  slow_gesture: 1,       // le geste `blink_slow` : rassurer, se resigner — voulu
};

export class BlinkController {
  /** @param {import('./rng.js').Rng} rng */
  constructor(rng) {
    this.rng = rng;
    this.phase = -1;          // <0 : pas de clignement en cours
    this.speed = 7;           // 1 / duree
    this.slow = false;
    this.pendingDouble = -1;  // >0 : un second clignement est programme
    this.nextIn = this.rng.range(1.5, 5.5);
    this.sinceLast = 10;
    this.inhibitedFor = 0;
    this.releaseCause = null; // la cause a proposer quand l'inhibition finit

    /** Compteurs : total, par cause, derniers intervalles. */
    this.count = 0;
    this.byCause = Object.create(null);
    this.intervals = [];
    this.lastCause = null;
  }

  /**
   * Une situation demande un clignement. Accepte seulement si la paupiere ne
   * vient pas de se fermer, si rien ne l'inhibe, et au tirage.
   *
   * @returns {boolean} un clignement a commence
   */
  request(cause, probability = CAUSES[cause] !== undefined ? CAUSES[cause] : 0.5) {
    if (this.phase >= 0 || this.pendingDouble > 0) return false;
    if (this.inhibitedFor > 0 || this.sinceLast < MIN_GAP_S) return false;
    if (this.rng.next() >= probability) return false;
    this._start(cause);
    return true;
  }

  /**
   * Pas de clignement pendant `seconds`. Un clignement du a ce moment attend ;
   * a la fin, `after` est propose (s'il est donne).
   */
  inhibit(seconds, after = null) {
    this.inhibitedFor = Math.max(this.inhibitedFor, seconds);
    this.releaseCause = after;
  }

  /**
   * Une image.
   * @param {number} dt
   * @param {object} b       les parametres de l'etat de presence (states.js)
   * @param {boolean} closed les yeux sont fermes par surete : rien a faire
   */
  update(dt, b, closed) {
    this.sinceLast += dt;
    if (closed) { this.phase = -1; this.pendingDouble = -1; return; }

    if (this.inhibitedFor > 0) {
      this.inhibitedFor -= dt;
      if (this.inhibitedFor <= 0) {
        this.inhibitedFor = 0;
        const cause = this.releaseCause;
        this.releaseCause = null;
        if (cause) this.request(cause);
      }
    }

    if (this.phase >= 0) {
      this.phase += dt * this.speed;
      if (this.phase >= 1) this.phase = -1;
      return;
    }
    if (this.pendingDouble > 0) {
      this.pendingDouble -= dt;
      if (this.pendingDouble <= 0) { this.pendingDouble = -1; this._start('double'); }
      return;
    }
    this.nextIn -= dt;
    if (this.nextIn <= 0 && this.inhibitedFor <= 0) this._start('spontaneous', b);
  }

  /** Le spontane est-il bientot du ? — ce que le regard consulte. */
  dueSoon(b) {
    return this.nextIn < this.meanInterval(b) * 0.85;
  }

  meanInterval(b) {
    const perMin = this.slow ? 30 : (b && b.blinkPerMin !== undefined ? b.blinkPerMin : 17);
    return 60 / Math.max(1, perMin);
  }

  /**
   * Un clignement LENT et voulu — le geste `blink_slow` (rassurer, se
   * resigner). Il remplace le prochain spontane au lieu de s'y ajouter, et
   * passe outre la garde anti-papillonnement : c'est un geste, pas un reflexe.
   * Refuse seulement pendant un clignement deja en cours.
   */
  slowOnce() {
    if (this.phase >= 0) return false;
    this.pendingDouble = -1;
    this._start('slow_gesture');
    this.speed = 2.2;          // ~0.45 s, au lieu de ~0.14
    return true;
  }

  /** 0..1 : la paupiere de ce clignement. */
  get lid() {
    if (this.phase < 0) return 0;
    const p = this.phase;
    // Le clignement VOULU se tient un instant ferme : c'est ce qui le
    // distingue d'un reflexe, et ce qui le fait lire comme « tout va bien ».
    if (this.lastCause === 'slow_gesture') {
      return p < 0.3 ? p / 0.3 : p < 0.45 ? 1 : 1 - (p - 0.45) / 0.55;
    }
    // Fermeture rapide, ouverture plus lente — c'est ainsi qu'une paupiere
    // bouge, et sin() donnerait le contraire.
    return p < 0.35 ? p / 0.35 : 1 - (p - 0.35) / 0.65;
  }

  _start(cause, b) {
    if (this.count > 0) {
      this.intervals.push(this.sinceLast);
      if (this.intervals.length > 400) this.intervals.shift();
    }
    this.count += 1;
    this.byCause[cause] = (this.byCause[cause] || 0) + 1;
    this.lastCause = cause;
    this.sinceLast = 0;
    this.phase = 0;
    // ~120 a 170 ms en temps normal : jamais deux paupieres identiques.
    this.speed = this.slow ? 2.2 : 7.0 * this.rng.range(0.85, 1.2);

    const mean = this.meanInterval(b || this._b);
    // Log-normal de moyenne `mean` : beaucoup de courts, quelques longs.
    const z = gaussian(this.rng);
    const factor = Math.exp(SIGMA * z - SIGMA * SIGMA / 2);
    this.nextIn = mean * Math.max(LO, Math.min(HI, factor));

    if (cause === 'spontaneous' && !this.slow && b && this.rng.next() < (b.doubleBlink || 0)) {
      this.pendingDouble = this.rng.range(0.12, 0.22) + 1 / this.speed;
    }
  }

  /** Retenu pour les demandes causees, qui n'ont pas l'etat sous la main. */
  remember(b) { this._b = b; }
}

/** Une gaussienne centree reduite, tiree de la graine (Box-Muller). */
function gaussian(rng) {
  const u = Math.max(1e-9, rng.next());
  const v = rng.next();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
