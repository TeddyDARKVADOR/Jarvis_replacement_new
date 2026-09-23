/**
 * states.js — ce que fait un visage selon ce qu'il est en train de faire.
 *
 * LA QUESTION QUE CE FICHIER TRANCHE
 *   Quelqu'un qui ecoute ne cligne pas des yeux comme quelqu'un qui parle, ne
 *   tient pas la tete pareil, ne detourne pas le regard aussi souvent. Rien de
 *   tout ca n'est une DECISION — personne ne choisit de cligner plus en
 *   parlant — et c'est pourtant ce qui distingue un visage qui ecoute d'un
 *   visage qui attend.
 *
 *   Le LLM ne gere donc pas ces micro-etats, et surtout pas image par image. Il
 *   ne sait meme pas qu'ils existent. L'etat machine arrive deja dans chaque
 *   Performance (`state`), et ce fichier en tire le comportement de fond.
 *
 * LES ETATS
 *   Les mots de `main.py` (LISTENING, THINKING, ...) sont nombreux et parlent
 *   du programme. Ceux-ci parlent du visage, et il y en a huit :
 *
 *       idle          disponible, rien ne se passe
 *       listening     l'utilisateur parle — regard sur lui, tete legerement
 *                     inclinee, peu de clignements
 *       thinking      il cherche — regard qui s'echappe, sourcils actifs
 *       speaking      il parle — clignements plus frequents, coups d'oeil
 *                     lateraux, la bouche appartient au lip-sync
 *       reacting      quelque chose vient d'arriver (reveil, erreur)
 *       transitioning entre deux des precedents, pendant le fondu
 *       unavailable   endormi, hors ligne — le visage se retire
 *       loading       connexion en cours
 *
 * D'OU VIENNENT LES FREQUENCES
 *   Clignements : ~17 par minute au repos, ~26 en conversation, beaucoup moins
 *   quand l'attention visuelle est soutenue (Bentivoglio et al., 1997). Coups
 *   d'oeil lateraux en parlant, pour chercher ses mots, et regard stable en
 *   ecoutant (Kendon, 1967). Inclinaison de tete d'ecoute : signal d'interet.
 *
 * POURQUOI UN FONDU ET PAS UN INTERRUPTEUR
 *   Passer d'ecouter a parler ne change pas la frequence de clignement a la
 *   milliseconde pres. Les parametres sont interpoles pendant TRANSITION_S, et
 *   l'etat rapporte vaut `transitioning` pendant ce temps — le labo le montre.
 */

const DEG = Math.PI / 180;

/** Mot d'etat machine -> etat du visage. Tout mot inconnu vaut `idle`. */
export const STATE_OF = {
  ACTIVE: 'idle',
  LISTENING: 'listening',
  CONFIRM: 'listening',      // il attend un oui ou un non : il ecoute
  THINKING: 'thinking',
  SPEAKING: 'speaking',
  WAKING: 'reacting',
  ERROR: 'reacting',
  SLEEPING: 'unavailable',
  OFFLINE: 'unavailable',
  CONNECTING: 'loading',
  RECONNECTING: 'loading',
};

/**
 * Le comportement de fond de chaque etat.
 *
 *   blinkPerMin    frequence moyenne de clignement
 *   doubleBlink    probabilite qu'un clignement soit double
 *   saccadeAmp     amplitude des micro-saccades, en poids d'oeil
 *   saccadeEvery   intervalle entre deux, en secondes [min, max]
 *   glanceEvery    intervalle entre deux coups d'oeil, ou null = jamais
 *   glanceAmp      leur amplitude
 *   microRate      frequence des micro-expressions (1 = celle du repos)
 *   microGain      leur amplitude (1 = celle du repos)
 *   headRx/headRz  une inclinaison de tete tenue, en radians
 */
export const BEHAVIOURS = {
  idle:        { blinkPerMin: 17, doubleBlink: 0.10, saccadeAmp: 0.05, saccadeEvery: [0.6, 2.2],
                 glanceEvery: [6, 12], glanceAmp: 0.22, microRate: 1.0, microGain: 1.0,
                 headRx: 0, headRz: 0 },
  listening:   { blinkPerMin: 13, doubleBlink: 0.06, saccadeAmp: 0.035, saccadeEvery: [0.8, 2.4],
                 glanceEvery: [10, 18], glanceAmp: 0.15, microRate: 0.8, microGain: 0.9,
                 headRx: -1.0 * DEG, headRz: 2.5 * DEG },
  thinking:    { blinkPerMin: 21, doubleBlink: 0.12, saccadeAmp: 0.08, saccadeEvery: [0.4, 1.2],
                 glanceEvery: null, glanceAmp: 0, microRate: 1.3, microGain: 1.0,
                 headRx: -1.5 * DEG, headRz: -3.0 * DEG },
  speaking:    { blinkPerMin: 25, doubleBlink: 0.15, saccadeAmp: 0.05, saccadeEvery: [0.7, 2.0],
                 glanceEvery: [3, 7], glanceAmp: 0.28, microRate: 0.6, microGain: 0.7,
                 headRx: 0, headRz: 0 },
  reacting:    { blinkPerMin: 10, doubleBlink: 0.0, saccadeAmp: 0.03, saccadeEvery: [0.8, 2.0],
                 glanceEvery: null, glanceAmp: 0, microRate: 0.3, microGain: 0.4,
                 headRx: 0, headRz: 0 },
  unavailable: { blinkPerMin: 6, doubleBlink: 0.0, saccadeAmp: 0.0, saccadeEvery: [2, 4],
                 glanceEvery: null, glanceAmp: 0, microRate: 0.2, microGain: 0.4,
                 headRx: 0, headRz: 0 },
  loading:     { blinkPerMin: 17, doubleBlink: 0.08, saccadeAmp: 0.08, saccadeEvery: [0.3, 0.9],
                 glanceEvery: [2, 4], glanceAmp: 0.3, microRate: 0.5, microGain: 0.6,
                 headRx: 0, headRz: 0 },
};

/** Duree du fondu entre deux etats. */
export const TRANSITION_S = 0.4;

const NUMERIC = ['blinkPerMin', 'doubleBlink', 'saccadeAmp', 'glanceAmp',
                 'microRate', 'microGain', 'headRx', 'headRz'];

export class PresenceStates {
  constructor() {
    this.word = '';
    this.name = 'idle';
    this.previous = 'idle';
    this.blend = 1;              // 0 = tout l'ancien etat, 1 = tout le nouveau
    this.current = Object.assign({}, BEHAVIOURS.idle);
  }

  /** Le mot de la Performance. Rien ne se passe si l'etat ne change pas. */
  set(word) {
    const upper = String(word || '').toUpperCase();
    const next = STATE_OF[upper] || 'idle';
    this.word = upper;
    if (next === this.name) return;
    this.previous = this.name;
    this.name = next;
    this.blend = 0;
  }

  /** Ce que le visage vit maintenant : l'etat, ou `transitioning`. */
  get reported() { return this.blend < 1 ? 'transitioning' : this.name; }

  /** Une image. Rend les parametres, fondus. Aucun objet cree par image. */
  update(dt) {
    if (this.blend < 1) this.blend = Math.min(1, this.blend + dt / TRANSITION_S);
    const a = BEHAVIOURS[this.previous] || BEHAVIOURS.idle;
    const b = BEHAVIOURS[this.name] || BEHAVIOURS.idle;
    const k = this.blend * this.blend * (3 - 2 * this.blend);
    const out = this.current;
    for (const key of NUMERIC) out[key] = a[key] + (b[key] - a[key]) * k;
    // Les intervalles ne se fondent pas : on prend ceux de l'etat vers lequel
    // on va des la moitie du fondu.
    const lead = k < 0.5 ? a : b;
    out.saccadeEvery = lead.saccadeEvery;
    out.glanceEvery = lead.glanceEvery;
    return out;
  }
}
