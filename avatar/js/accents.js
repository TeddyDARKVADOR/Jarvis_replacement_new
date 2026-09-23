/**
 * accents.js — le bref geste du visage qui porte une intention.
 *
 * POURQUOI CETTE COUCHE
 *   En mode visage, le corps est tenu et chaque geste se rabat sur la tete.
 *   Mesure sur la sortie reelle, `greet` et `report_success` y jouaient alors
 *   exactement la meme chose — happy 0.68, regard sur l'utilisateur, `nod` —
 *   et `acknowledge` et `explain` aussi. Deux intentions indiscernables ne
 *   sont que deux noms.
 *
 *   Un humain dont le corps ne bouge pas les distingue quand meme, par un
 *   signal facial bref : le flash des sourcils du salut, le menton releve de la
 *   fierte, les appuis de tete de l'explication, la tete basse de l'excuse.
 *   `presence/model.py` (`Accent`) en donne les sources.
 *
 * CE QUE JARVIS EN SAIT
 *   Rien. Il dit `greet` ; `presence/affect.py` associe `brow_flash` au mot ;
 *   ce fichier le joue. Les noms ci-dessous sont verifies contre l'enum Python
 *   par `presence/selftest.py`.
 *
 * COMMENT IL SE COMBINE
 *   Les formes vont dans la couche « comportement intentionnel » de `rig.js`,
 *   au-dessus de l'expression et sous la parole : un flash de sourcils pendant
 *   qu'il parle ne touche pas la bouche. La tete va dans la somme de
 *   `gestures.js`, comme un geste, et le regard la compense — il salue sans
 *   quitter l'utilisateur des yeux.
 */

const DEG = Math.PI / 180;

/** Monte, tient, retombe — en courbe douce aux deux bouts. */
function envelope(t, rise, hold, fall) {
  if (t <= 0) return 0;
  if (t < rise) return smooth(t / rise);
  if (t < rise + hold) return 1;
  if (t < rise + hold + fall) return smooth(1 - (t - rise - hold) / fall);
  return 0;
}

function smooth(x) {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
}

/**
 * Chaque accent : sa duree, ses formes a pleine intensite, sa tete, et la
 * fonction du temps qui les dose.
 *
 * Les amplitudes restent sous celles des expressions : un accent colore un
 * visage, il n'en installe pas un nouveau.
 */
export const ACCENTS = {
  // Le salut : les deux sourcils montent et redescendent en un tiers de
  // seconde. Au-dela de 0.5 s ce n'est plus un salut, c'est de l'etonnement.
  brow_flash: {
    duration: 0.62,
    shapes: { browInnerUp: 0.45, browOuterUpLeft: 0.55, browOuterUpRight: 0.55,
              eyeWideLeft: 0.15, eyeWideRight: 0.15 },
    head: { rx: -1.5 * DEG, ry: 0, rz: 0 },
    f: (t) => envelope(t, 0.12, 0.18, 0.30),
  },

  // La fierte : le menton se releve, la bouche se retient. Un sourire large
  // ici donnerait de la joie, pas de la reussite.
  chin_up: {
    duration: 1.9,
    shapes: { mouthPressLeft: 0.20, mouthPressRight: 0.20, jawForward: 0.08 },
    head: { rx: -5.5 * DEG, ry: 0, rz: 0 },
    f: (t) => envelope(t, 0.35, 1.0, 0.5),
  },

  // L'explication : trois petits appuis de tete, ce qui marque le rythme
  // d'une phrase qui developpe. Les sourcils accompagnent a peine.
  beat: {
    duration: 2.1,
    shapes: { browInnerUp: 0.12, browOuterUpLeft: 0.08, browOuterUpRight: 0.08 },
    head: { rx: 3.2 * DEG, ry: 0, rz: 0 },
    f: (t) => {
      const on = envelope(t, 0.15, 1.6, 0.35);
      // Trois appuis, vers le bas seulement : une tete qui marque, pas qui
      // oscille.
      const pulse = Math.max(0, Math.sin(t * Math.PI * 2 / 0.62));
      return on * pulse;
    },
    // Les formes suivent l'enveloppe, pas chaque appui.
    shapeF: (t) => envelope(t, 0.15, 1.6, 0.35),
    // Ces trois appuis sont une horloge : sans voix, c'est tout ce qu'on a.
    // Quand il parle, ce sont ses syllabes qui marquent (conversation.js, que
    // `explain` amplifie) et cette horloge se tait — elle tombait a cote.
    yieldsToSpeech: true,
  },

  // L'excuse : la tete s'abaisse et reste basse, les sourcils montent au
  // centre. C'est ce que `bow`, rabattu sur un hochement, ne disait plus.
  head_down: {
    duration: 2.4,
    shapes: { browInnerUp: 0.18 },
    head: { rx: 8 * DEG, ry: 0, rz: 0 },
    f: (t) => envelope(t, 0.5, 1.3, 0.6),
  },
};

/**
 * Le temps qu'un accent interrompu met a s'effacer.
 *
 * POURQUOI UN EFFACEMENT ET PAS UN ARRET
 *   `stop()` existait, documente comme « une nouvelle decision sans accent
 *   coupe l'ancien » — et personne ne l'appelait. Mesure : `apologise` puis
 *   `warn` une demi-seconde plus tard, et la tete restait basse de 5.4 degres
 *   pendant l'avertissement. Mais le couper net n'aurait pas mieux valu : la
 *   couche accent n'est pas lissee par `rig.js`, et des sourcils a 0.55 qui
 *   tombent a zero en une image se voient comme un tic. Une decision nouvelle
 *   REDIRIGE le visage ; l'ancien geste se retire en un sixieme de seconde.
 */
export const INTERRUPT_FADE_S = 0.18;

export class Accents {
  constructor() {
    this.active = null;       // { name, t, spec, tempo }
    /** L'accent interrompu qui s'efface : { name, t, spec, tempo, fade }. */
    this.fading = null;
    /** Sorties de l'image, reutilisees. */
    this.shapes = Object.create(null);
    this.head = { rx: 0, ry: 0, rz: 0 };
    /** La tete de l'accent emmene-t-elle les yeux ? Seule l'excuse le fait. */
    this.keepsEyes = true;
    /** Le dernier accent joue, pour le labo et le journal. */
    this.last = null;
    /** Combien d'accents ont ete interrompus — pour les controles. */
    this.interrupted = 0;
    /** « Il parle », 0..1, pose par le moteur : voir `yieldsToSpeech`. */
    this.speaking = 0;
  }

  /**
   * Jouer un accent. Un nom inconnu ne fait rien — et le dit : `false`.
   * Un accent en cours n'est pas coupe : il s'efface sous le nouveau.
   */
  play(name, tempo = 1) {
    const spec = name ? ACCENTS[name] : null;
    if (!spec) return false;
    this.release();
    this.active = { name, t: 0, spec, tempo: Math.max(0.5, Math.min(1.8, Number(tempo) || 1)) };
    this.last = name;
    return true;
  }

  /** Une nouvelle decision arrive : l'accent en cours s'efface. */
  release() {
    if (!this.active) return;
    this.fading = Object.assign(this.active, { fade: 0 });
    this.active = null;
    this.interrupted += 1;
  }

  /** Quelque chose a ecrire cette image — actif ou en train de s'effacer. */
  get busy() { return this.active !== null || this.fading !== null; }

  get name() { return this.active ? this.active.name : null; }

  update(dt) {
    for (const key in this.shapes) delete this.shapes[key];
    this.head.rx = this.head.ry = this.head.rz = 0;
    this.keepsEyes = true;

    const a = this.active;
    if (a) {
      // Le flash de sourcils ne suit pas le tempo : c'est un signal, sa duree
      // est ce qui le rend lisible. Les autres, oui — un JARVIS presse explique
      // vite.
      a.t += dt * (a.name === 'brow_flash' ? 1 : a.tempo);
      if (a.t >= a.spec.duration) this.active = null;
      else this._add(a, 1);
    }

    const f = this.fading;
    if (f) {
      f.fade += dt;
      f.t += dt * (f.name === 'brow_flash' ? 1 : f.tempo);
      const left = 1 - f.fade / INTERRUPT_FADE_S;
      if (left <= 0 || f.t >= f.spec.duration) this.fading = null;
      else this._add(f, left * left * (3 - 2 * left));
    }
  }

  /** Ajoute la contribution d'un accent, dosee par `gain`. */
  _add(a, gain) {
    // Cede franchement : a « il parle » 0.8 et au-dela, l'horloge se tait.
    const head = a.spec.yieldsToSpeech ? Math.max(0, 1 - this.speaking / 0.8) : 1;
    const k = a.spec.f(a.t) * gain * head;
    const ks = (a.spec.shapeF ? a.spec.shapeF(a.t) : a.spec.f(a.t)) * gain;
    for (const shape in a.spec.shapes) {
      const v = a.spec.shapes[shape] * ks;
      if (v > 0.002 && v > (this.shapes[shape] || 0)) this.shapes[shape] = v;
    }
    this.head.rx += a.spec.head.rx * k;
    this.head.ry += a.spec.head.ry * k;
    this.head.rz += a.spec.head.rz * k;
    if (a.name === 'head_down' && k > 0.05) this.keepsEyes = false;
  }
}
