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

export class Accents {
  constructor() {
    this.active = null;       // { name, t, spec, tempo }
    /** Sorties de l'image, reutilisees. */
    this.shapes = Object.create(null);
    this.head = { rx: 0, ry: 0, rz: 0 };
    /** Le dernier accent joue, pour le labo et le journal. */
    this.last = null;
  }

  /** Jouer un accent. Un nom inconnu ne fait rien — et le dit : `false`. */
  play(name, tempo = 1) {
    const spec = name ? ACCENTS[name] : null;
    if (!spec) return false;
    this.active = { name, t: 0, spec, tempo: Math.max(0.5, Math.min(1.8, Number(tempo) || 1)) };
    this.last = name;
    return true;
  }

  /** Interrompre : une nouvelle decision sans accent coupe l'ancien. */
  stop() { this.active = null; }

  get name() { return this.active ? this.active.name : null; }

  update(dt) {
    for (const key in this.shapes) delete this.shapes[key];
    this.head.rx = this.head.ry = this.head.rz = 0;
    const a = this.active;
    if (!a) return;

    // Le flash de sourcils ne suit pas le tempo : c'est un signal, sa duree est
    // ce qui le rend lisible. Les autres, oui — un JARVIS presse explique vite.
    a.t += dt * (a.name === 'brow_flash' ? 1 : a.tempo);
    if (a.t >= a.spec.duration) { this.active = null; return; }

    const k = a.spec.f(a.t);
    const ks = a.spec.shapeF ? a.spec.shapeF(a.t) : k;
    for (const shape in a.spec.shapes) {
      const v = a.spec.shapes[shape] * ks;
      if (v > 0.002) this.shapes[shape] = v;
    }
    this.head.rx = a.spec.head.rx * k;
    this.head.ry = a.spec.head.ry * k;
    this.head.rz = a.spec.head.rz * k;
  }
}
