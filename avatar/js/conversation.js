/**
 * conversation.js — ce qu'un visage fait parce qu'on se parle.
 *
 * LA QUESTION DE DEPART
 *   Pendant qu'il parle, JARVIS avait la tete du repos : une derive lente, la
 *   meme qu'en silence. Pendant qu'il ecoute, une inclinaison tenue et rien
 *   d'autre. Rien ne distinguait un visage qui ECOUTE d'un visage qui ATTEND,
 *   ni un visage qui parle d'une bouche qui bouge sur une tete immobile.
 *
 *   Ce que font les humains a ces moments-la est bien documente, et tient en
 *   quatre signaux, tous lies a un evenement de la voix — aucun n'est tire
 *   d'une horloge :
 *
 *     il commence a parler   le regard s'echappe un instant, puis revient
 *                            (Kendon 1967 : le locuteur detourne les yeux en
 *                            debut d'enonce, les ramene vers la fin)
 *     il parle               la tete marque les syllabes appuyees d'un petit
 *                            mouvement vers le bas, parfois les sourcils aussi
 *                            (Munhall et al. 2004 ; Cave et al. 1996)
 *     il finit sa phrase     un clignement, souvent (Nakano & Kitazawa 2010)
 *     l'utilisateur marque   un petit hochement « je suis », parfois, apres un
 *     une pause              moment de parole (Ward & Tsukahara 2000 : les
 *                            relances suivent les pauses)
 *
 * CE QUE JARVIS EN SAIT
 *   Rien, et c'est voulu. Il dit `explain` ; il ne dit pas « hoche sur la
 *   troisieme syllabe ». Ce fichier lit deux niveaux de voix (le sien, et le
 *   micro de l'utilisateur) et produit une tete, trois sourcils et des
 *   demandes au regard et aux paupieres. Le contexte vient de la Performance :
 *   l'immobilite (derivee de l'affect), `explain` qui amplifie, un regard
 *   decide que rien ne deplace.
 *
 * LA LIMITE HONNETE
 *   Un niveau n'a ni hauteur ni sens. Un « appui » est ici une syllabe plus
 *   forte que ses voisines — la correlation est reelle, pas parfaite. Et une
 *   pause de l'utilisateur n'est pas une fin d'idee : le hochement reste
 *   petit (≤ 3.5°) pour dire « je suis », jamais « je suis d'accord ».
 */

import { VoiceEnvelope } from './voice.js';
import { springStep, omegaFor } from './performance.js';

const DEG = Math.PI / 180;

/** Les appuis de tete de la parole. */
export const BEAT = {
  amp: 2.2 * DEG,        // un appui franc ; la plupart sont plus petits
  attack: 0.12,          // 90 % en 0.12 s : le sommet tombe sur la syllabe
  release: 0.30,
  hold: 0.13,            // tenu jusqu'au sommet de la syllabe, a peu pres
  refractory: 0.45,      // deux appuis par seconde au plus
  onset: 0.8,            // toutes les syllabes n'appellent pas la tete
  emphasis: 1.12,        // sommet / moyenne a partir duquel c'est un appui
};

/** Les sourcils qui accompagnent les appuis les plus forts. */
export const BROW = {
  ratio: 1.45, probability: 0.4, refractory: 2.5, duration: 0.42,
  shapes: { browInnerUp: 0.10, browOuterUpLeft: 0.08, browOuterUpRight: 0.08 },
};

/** Le regard qui s'echappe en debut d'enonce. */
export const AVERT = {
  probability: 0.5, afterSilence: 0.8, duration: [0.6, 1.3], amp: [0.20, 0.32],
};

/** Le hochement d'ecoute. */
export const BACKCHANNEL = {
  minSpeech: 1.0, probability: 0.4, refractory: [2.5, 4.5],
  amp: 3.0 * DEG, attack: 0.14, release: 0.35, hold: 0.12,
};

/** Une impulsion de tete : un ressort vers `goal`, tenu, puis rendu. */
class HeadPulse {
  constructor() {
    this.s = { x: 0, v: 0 };
    this.goal = 0;
    this.left = 0;         // temps de tenue restant
    this.attack = 0.12;
    this.release = 0.3;
    this.yaw = 0;          // part laterale, en fraction du tangage
    this.roll = 0;
  }

  fire(amplitude, spec, rng) {
    this.goal = amplitude;
    this.left = spec.hold;
    this.attack = spec.attack;
    this.release = spec.release;
    // Jamais deux appuis identiques : un peu de lacet et de roulis, au hasard
    // du cote. Petit : un appui est d'abord vertical.
    this.yaw = (rng.next() < 0.5 ? -1 : 1) * rng.range(0.1, 0.35);
    this.roll = (rng.next() < 0.5 ? -1 : 1) * rng.range(0.0, 0.2);
  }

  /** Le sommet de la syllabe est connu : ajuster pendant la montee. */
  reshape(amplitude) {
    if (this.left > 0) this.goal = amplitude;
  }

  get rising() { return this.left > 0; }

  update(dt) {
    if (this.left > 0) {
      this.left -= dt;
      if (this.left <= 0) this.goal = 0;
    }
    const goingUp = Math.abs(this.goal) >= Math.abs(this.s.x);
    springStep(this.s, this.goal, omegaFor(goingUp ? this.attack : this.release), dt);
    if (this.goal === 0 && Math.abs(this.s.x) < 1e-5 && Math.abs(this.s.v) < 1e-4) {
      this.s.x = 0;
      this.s.v = 0;
    }
    return this.s.x;
  }
}

export class Conversation {
  /** @param {import('./rng.js').Rng} rng */
  constructor(rng) {
    this.rng = rng;
    this.self = new VoiceEnvelope({ threshold: 0.045, rise: 0.1 });
    this.user = new VoiceEnvelope({ threshold: 0.05, rise: 0.08, floor: true });
    this.beat = new HeadPulse();
    this.nod = new HeadPulse();
    this.t = 0;

    // Le contexte, pose par le moteur a chaque Performance.
    this.gain = 1;
    this.boost = 1;
    this.listening = false;

    // Les frontieres d'enonce, lues sur `speaking` (le lip-sync, qui tient a
    // travers les espaces entre les mots).
    this.inUtterance = false;
    this.quietFor = 10;
    this.utterFor = 0;

    this.lastBeatAt = -10;
    this.lastBrowAt = -10;
    this.browT = -1;
    this.backchannelAfter = 0;

    /** Sorties de l'image, reutilisees. */
    this.head = { rx: 0, ry: 0, rz: 0 };
    this.shapes = Object.create(null);
    /** Evenements de l'image : le moteur les transmet au regard et aux paupieres. */
    this.events = { utteranceStart: false, utteranceEnd: false, avert: null, userPause: false };
    /** Compteurs, pour les controles et le labo. */
    this.stats = { beats: 0, emphasised: 0, brows: 0, averts: 0, utterances: 0, backchannels: 0 };
    this._avert = { x: 0, y: 0, duration: 0 };
  }

  setSelfLevel(level) { this.self.set(level); }

  setUserLevel(level) { this.user.set(level); }

  /**
   * Ce que la Performance en cours dit du contexte.
   * @param {object} c
   * @param {number} c.stillness  0 agite -> 1 statue ; un JARVIS calme marque moins
   * @param {boolean} c.explaining  `explain` : les appuis sont la moitie du message
   * @param {boolean} c.listening   l'etat de presence est l'ecoute
   */
  setContext({ stillness, explaining, listening }) {
    const s = Number.isFinite(stillness) ? stillness : 0.7;
    this.gain = Math.max(0.4, Math.min(1.3, 0.45 + (1 - s) * 1.1));
    this.boost = explaining ? 1.6 : 1;
    this.listening = !!listening;
  }

  /**
   * Une image.
   * @param {number} dt
   * @param {number} speaking  le « il parle » du lip-sync, 0..1
   * @param {boolean} gazeDecided  un regard decide ne s'echappe pas
   */
  update(dt, speaking, gazeDecided) {
    this.t += dt;
    const ev = this.events;
    ev.utteranceStart = false;
    ev.utteranceEnd = false;
    ev.avert = null;
    ev.userPause = false;

    this.self.update(dt);
    this.user.update(dt);

    // ── les frontieres de l'enonce ──────────────────────────────────────
    if (!this.inUtterance && speaking > 0.5) {
      this.inUtterance = true;
      this.utterFor = 0;
      if (this.quietFor >= AVERT.afterSilence) {
        ev.utteranceStart = true;
        this.stats.utterances += 1;
        if (!gazeDecided && this.rng.next() < AVERT.probability) {
          const a = this._avert;
          a.x = (this.rng.next() < 0.5 ? -1 : 1) * this.rng.range(AVERT.amp[0], AVERT.amp[1]);
          a.y = this.rng.range(0.05, 0.22);   // plutot vers le haut : on cherche ses mots
          a.duration = this.rng.range(AVERT.duration[0], AVERT.duration[1]);
          ev.avert = a;
          this.stats.averts += 1;
        }
      }
    }
    if (this.inUtterance) {
      this.utterFor += dt;
      if (speaking < 0.3) {
        this.inUtterance = false;
        this.quietFor = 0;
        if (this.utterFor >= 1.0) ev.utteranceEnd = true;
      }
    } else {
      this.quietFor += dt;
    }

    // ── les appuis de la parole ─────────────────────────────────────────
    const g = this.gain * this.boost;
    // La periode refractaire ne compte qu'a partir d'un VRAI appui : une
    // syllabe ordinaire qui la consommait bloquait l'appui qui la suivait (une
    // syllabe dure ~0.22 s, la periode 0.45 s). Mesure : un appui visible en
    // six secondes de parole.
    if (this.self.onset && speaking > 0.3 && !this.beat.rising
        && this.t - this.lastBeatAt >= BEAT.refractory && this.rng.next() < BEAT.onset) {
      // A l'attaque on ne sait pas encore si la syllabe sera appuyee : on part
      // petit, et le sommet decide.
      this.beat.fire(BEAT.amp * 0.3 * g, BEAT, this.rng);
      this.stats.beats += 1;
    }
    if (this.self.peaked && this.beat.rising) {
      const r = this.self.ratio;
      if (r >= BEAT.emphasis) {
        const k = Math.max(0.45, Math.min(1, (r - 1) / 0.45));
        this.beat.reshape(BEAT.amp * k * g * this.rng.range(0.85, 1.15));
        this.lastBeatAt = this.t;
        this.stats.emphasised += 1;
      } else {
        // Une syllabe ordinaire : la tete bouge a peine, ce qui suffit a ce
        // qu'elle ne soit jamais tout a fait immobile quand il parle.
        this.beat.reshape(BEAT.amp * 0.15 * g);
      }
      if (r >= BROW.ratio && this.t - this.lastBrowAt >= BROW.refractory
          && this.rng.next() < BROW.probability * Math.min(1, this.boost)) {
        this.browT = 0;
        this.lastBrowAt = this.t;
        this.stats.brows += 1;
      }
    }

    // ── l'ecoute : un hochement aux pauses de l'utilisateur ─────────────
    if (this.listening && speaking < 0.1 && this.user.pauseStarted
        && this.user.spokeFor >= BACKCHANNEL.minSpeech) {
      ev.userPause = true;
      if (this.t >= this.backchannelAfter && this.rng.next() < BACKCHANNEL.probability) {
        this.nod.fire(BACKCHANNEL.amp * this.rng.range(0.75, 1.15), BACKCHANNEL, this.rng);
        this.backchannelAfter = this.t + this.rng.range(...BACKCHANNEL.refractory);
        this.stats.backchannels += 1;
      }
    }

    // ── la sortie ───────────────────────────────────────────────────────
    const b = this.beat.update(dt);
    const n = this.nod.update(dt);
    this.head.rx = b + n;
    this.head.ry = b * this.beat.yaw + n * this.nod.yaw * 0.5;
    this.head.rz = b * this.beat.roll + n * this.nod.roll * 0.5;

    for (const key in this.shapes) delete this.shapes[key];
    if (this.browT >= 0) {
      this.browT += dt;
      const p = this.browT / BROW.duration;
      if (p >= 1) {
        this.browT = -1;
      } else {
        const k = Math.sin(p * Math.PI);
        for (const shape in BROW.shapes) this.shapes[shape] = BROW.shapes[shape] * k;
      }
    }
  }
}
