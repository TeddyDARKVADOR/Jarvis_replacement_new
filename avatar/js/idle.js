/**
 * idle.js — ce que le personnage fait quand il ne fait rien.
 *
 * POURQUOI C'EST LA PIECE LA PLUS IMPORTANTE DU RENDU
 *   JARVIS passe 95 % de son temps entre deux decisions. Un corps qui ne bouge
 *   qu'aux ordres est fige 95 % du temps, et les utilisateurs rapportent ca
 *   comme « il a plante » — pas comme « il est calme ». Tout le travail sur les
 *   expressions ne se voit que si l'intervalle entre elles est vivant.
 *
 * POURQUOI CE N'EST PAS CINQUANTE ANIMATIONS DE REPOS
 *   Une banque de clips « idle_1 … idle_50 » se remarque au bout de dix minutes,
 *   parce qu'elle boucle. Et elle ne sait rien de l'etat : le meme idle joue
 *   qu'il soit inquiet ou amuse.
 *
 *   Ici le repos est une SOMME DE COUCHES CONTINUES, chacune parametree par
 *   l'affect. Il ne boucle jamais — les frequences sont incommensurables — et
 *   il ressemble a l'etat intérieur sans qu'aucune ligne ne dise « si inquiet
 *   alors bouger comme ceci ».
 *
 *       respiration      toujours, ralentie par l'immobilite
 *       report du poids  lent, alterne, seulement si le modele a des jambes
 *       micro-mouvements derive de la tete et du buste, bruit lisse
 *       derive du regard le regard REVIENT, il ne se verrouille pas
 *       micro-expressions breves, minuscules, plus frequentes si active
 *
 * LES DEUX NOMBRES QUI PILOTENT TOUT
 *   `stillness`  0 agite → 1 statue. Amplitude de tout ce qui suit.
 *   `tempo`      vitesse. Un JARVIS presse respire et derive plus vite.
 *
 *   Ils viennent de `presence/affect.py` et arrivent dans chaque Performance.
 *   Rien ici ne decide : tout ici derive.
 *
 * CE QUE CE FICHIER NE FAIT PAS
 *   Il ne clignote pas — `rig.js` possede les paupieres, avec sa propre
 *   horloge, parce qu'un clignement lisse n'est plus un clignement. Il ne
 *   deplace pas non plus le personnage : marcher est un geste, pas un repos.
 */

import { Rng } from './rng.js';

const TAU = Math.PI * 2;
const DEG = Math.PI / 180;

/**
 * Bruit lisse, sans table ni dependance.
 *
 * Trois sinus de frequences incommensurables : la somme ne se repete pas a une
 * echelle humaine, ce qui est toute la propriete recherchee. Un `Math.random()`
 * par image donnerait un tremblement, pas une derive.
 */
function drift(t, seed) {
  return (
    Math.sin(t * 0.31 + seed) * 0.55 +
    Math.sin(t * 0.73 + seed * 2.3) * 0.30 +
    Math.sin(t * 1.17 + seed * 4.1) * 0.15
  );
}

/**
 * Micro-expressions : de quoi une pensee a l'air quand personne ne parle.
 *
 * Amplitudes volontairement minuscules — au-dela de ~0.12 ce n'est plus une
 * micro-expression, c'est une grimace, et le visage se met a raconter quelque
 * chose que JARVIS n'a pas decide.
 */
const MICRO = [
  { shapes: { browInnerUp: 0.10, browOuterUpLeft: 0.06 }, hold: 0.55 },
  { shapes: { browDownLeft: 0.08, browDownRight: 0.07, eyeSquintLeft: 0.06 }, hold: 0.7 },
  { shapes: { mouthSmileLeft: 0.07, cheekSquintLeft: 0.05 }, hold: 0.9 },
  { shapes: { mouthPressLeft: 0.08, mouthPressRight: 0.08 }, hold: 0.6 },
  { shapes: { eyeSquintLeft: 0.09, eyeSquintRight: 0.09 }, hold: 0.45 },
  { shapes: { mouthLeft: 0.07, mouthPucker: 0.05 }, hold: 0.8 },
  { shapes: { browOuterUpRight: 0.09 }, hold: 0.5 },
  { shapes: { noseSneerLeft: 0.05, noseSneerRight: 0.04 }, hold: 0.35 },
];

export class Idle {
  /**
   * @param {object} nodes  les os, tels que `body.nodes` les expose. Ce qui
   *                        manque est simplement ignore : un corps sans jambes
   *                        ne reporte pas son poids, et c'est tout.
   * @param {Rng} [rng]     le hasard, avec sa graine — voir rng.js. Sans lui
   *                        une seance ne peut pas etre rejouee.
   */
  constructor(nodes = {}, rng) {
    this.hasLegs = !!(nodes.armLeftUpper || nodes.root);
    this.rng = rng || new Rng();
    this.t = 0;
    this.seed = this.rng.next() * 100;
    // Frequence des micro-expressions voulue par l'etat de presence : on en
    // fait moins en parlant, davantage en cherchant. Voir states.js.
    this.microRate = 1;

    // Ce que l'affect impose. Valeurs de repos en attendant la premiere
    // Performance : un corps ne doit jamais demarrer parfaitement immobile.
    this.stillness = 0.7;
    this.tempo = 1.0;
    this.gazeHold = 4.0;

    // ── derive du regard ─────────────────────────────────────────────────
    // Le regard REVIENT vers son ancre au lieu de s'y verrouiller. Un regard
    // parfaitement fixe est ce que fait une camera de surveillance.
    this.gazeDrift = { x: 0, y: 0, tx: 0, ty: 0, next: 0 };

    // ── micro-expressions ────────────────────────────────────────────────
    this.micro = null;
    this.microAge = 0;
    this.nextMicro = 2 + this.rng.next() * 4;

    this.offsets = {
      headRx: 0, headRy: 0, headRz: 0,
      spineRx: 0, spineRy: 0,
      rootY: 0, rootZ: 0, rootRy: 0,
    };
    this.shapes = Object.create(null);
  }

  /** Called on every Performance. Only the continuous parameters matter here. */
  setAffect({ stillness, tempo, gazeHold }) {
    if (typeof stillness === 'number') this.stillness = Math.max(0, Math.min(1, stillness));
    if (typeof tempo === 'number') this.tempo = Math.max(0.3, Math.min(2.5, tempo));
    if (typeof gazeHold === 'number') this.gazeHold = Math.max(0.4, gazeHold);
  }

  /**
   * One frame. Fills `offsets` (bones) and `shapes` (micro-expressions).
   *
   * Rien n'est ecrit directement : `gestures.js` additionne les offsets a sa
   * propre somme, et `rig.js` combine les formes au maximum. C'est ce qui
   * permet au repos de continuer PENDANT un geste — un clip qui fige la
   * respiration se lit comme un blocage, ce que les utilisateurs signalent.
   */
  update(dt) {
    const speed = this.tempo;
    this.t += dt * speed;

    // 0 immobile -> 1 agite. C'est le seul facteur d'amplitude du fichier.
    const move = 1 - this.stillness;

    const out = this.offsets;
    for (const key in out) out[key] = 0;

    this._breathe(out, move);
    this._micromotion(out, move);
    this._weightShift(out, move);
    this._gaze(dt, out, move);
    this._microExpression(dt, move);
  }

  /**
   * Respiration. Ne s'arrete jamais, y compris pendant un clip.
   *
   * L'immobilite la ralentit ET la creuse : quelqu'un de tres calme respire
   * lentement et amplement, quelqu'un d'active vite et court. Lier les deux au
   * meme nombre est ce qui fait qu'on lit l'etat sans y penser.
   */
  _breathe(out, move) {
    const rate = 0.16 + move * 0.22;          // Hz
    const depth = 0.006 + this.stillness * 0.006;
    const phase = this.t * TAU * rate;

    out.rootY += Math.sin(phase) * depth;
    out.spineRx += Math.sin(phase) * 0.9 * DEG;
    // Contre-mouvement leger de la tete : une tete parfaitement solidaire du
    // buste est une tete vissee.
    out.headRx -= Math.sin(phase) * 0.45 * DEG;
  }

  /** Derive lente de la tete et du buste. Ce qui distingue un vivant d'un mannequin. */
  _micromotion(out, move) {
    const amount = 0.35 + move * 0.65;
    out.headRy += drift(this.t, this.seed) * 1.5 * DEG * amount;
    out.headRx += drift(this.t, this.seed + 11) * 1.0 * DEG * amount;
    out.headRz += drift(this.t, this.seed + 23) * 0.8 * DEG * amount;
    out.spineRy += drift(this.t * 0.6, this.seed + 37) * 0.7 * DEG * amount;
  }

  /**
   * Report du poids d'une jambe sur l'autre. Tres lent, tres faible.
   *
   * C'est ce qui manque le plus a un humanoide debout : sans lui il est *posé*
   * sur le sol au lieu de s'y tenir. Sur une tete seule, `hasLegs` est faux et
   * rien ne se passe.
   */
  _weightShift(out, move) {
    if (!this.hasLegs) return;
    const phase = this.t * TAU * 0.055;       // ~18 s par cycle
    const amount = 0.5 + move * 0.5;
    out.rootRy += Math.sin(phase) * 1.4 * DEG * amount;
    out.spineRy += Math.sin(phase + 0.4) * -0.9 * DEG * amount;
    out.rootY += Math.abs(Math.sin(phase)) * -0.004 * amount;
  }

  /**
   * Derive du regard, par la tete.
   *
   * `gazeHold` vient de l'attention : une attention haute ne veut pas dire un
   * regard fixe, elle veut dire un regard qui revient vite. La cible derive,
   * puis une nouvelle est tiree — l'amplitude, elle, tombe quand l'attention
   * monte, ce que porte deja `gazeHold`.
   *
   * Les YEUX ne sont pas ici : `rig.js` possede les saccades, a sa propre
   * echelle de temps. Ceci est le mouvement de tete qui les accompagne.
   */
  _gaze(dt, out, move) {
    const g = this.gazeDrift;
    g.next -= dt;
    if (g.next <= 0) {
      const reach = (0.4 + move * 0.6);
      g.tx = this.rng.spread(2.2 * DEG * reach);
      g.ty = this.rng.spread(1.4 * DEG * reach);
      g.next = this.gazeHold * (0.6 + this.rng.next() * 0.8);
    }
    // Retour lent vers la cible : un saut de tete serait une saccade, et les
    // saccades appartiennent aux yeux.
    const k = 1 - Math.exp(-dt / 0.9);
    g.x += (g.tx - g.x) * k;
    g.y += (g.ty - g.y) * k;
    out.headRy += g.x;
    out.headRx += g.y;
  }

  /**
   * Une micro-expression breve, de temps en temps.
   *
   * Plus frequentes quand l'etat est active : c'est ce qui donne l'impression
   * qu'il se passe quelque chose derriere le visage. En dessous de ~0.12
   * d'amplitude elles ne se voient pas consciemment, ce qui est exactement le
   * but — une micro-expression qu'on remarque est une grimace.
   */
  _microExpression(dt, move) {
    if (this.micro) {
      this.microAge += dt;
      const p = this.microAge / this.micro.hold;
      if (p >= 1) {
        this.micro = null;
        this.shapes = Object.create(null);
      } else {
        // Monte et redescend : une micro-expression qui se coupe net se voit.
        const envelope = Math.sin(p * Math.PI) * (0.45 + move * 0.55);
        // Reutilise : une micro-expression dure une demi-seconde, et allouer
        // un objet par image pour elle etait l'allocation la plus frequente
        // du repos.
        const shapes = this.shapes;
        for (const name in this.micro.shapes) {
          shapes[name] = this.micro.shapes[name] * envelope;
        }
      }
      return;
    }

    this.nextMicro -= dt * this.microRate;
    if (this.nextMicro <= 0) {
      this.micro = this.rng.pick(MICRO);
      this.microAge = 0;
      this.shapes = Object.create(null);
      // Immobile : rarement. Active : souvent.
      this.nextMicro = (2.2 + this.rng.next() * 5.0) * (0.4 + this.stillness * 1.6);
    }
  }
}
