/**
 * affect.js — la derivation du comportement, en JavaScript.
 *
 * TABLES GENEREES depuis `presence/affect.py`. Ne pas les editer a la main :
 * regenerer, ou editer le Python et regenerer. `presence/selftest.py` analyse
 * ce fichier et echoue a la moindre difference.
 *
 * POURQUOI CE FICHIER EXISTE
 *   Le panneau n'en a pas besoin : le directeur derive en Python et envoie le
 *   resultat. Mais `lab.html` doit pouvoir deriver un comportement sans qu'aucun
 *   Python ne tourne — c'est toute sa raison d'etre. Un labo qui exigeait le
 *   serveur serait inutile exactement quand on en a besoin.
 *
 *   Il rend aussi possible un rendu hors-ligne plus tard : un telephone qui a
 *   perdu le lien peut encore se comporter, a partir d'un etat.
 *
 * CE QUE LE TEST VERIFIE, ET CE QU'IL NE VERIFIE PAS
 *   Il compare les TABLES — ancres, poids, plafonds, seuils — parce qu'il peut
 *   les lire. Il ne peut pas executer ce JavaScript.
 *
 *   Rien n'est arrondi : l'arrondi est une affaire de presentation, et le
 *   mettre dans le calcul rendait les deux implementations impossibles a
 *   comparer a l'identique. Voir presence/affect.py.
 *
 *   L'equivalence des FONCTIONS a donc ete mesuree separement, sur une grille de
 *   plusieurs milliers de points, en faisant tourner les deux implementations
 *   cote a cote (`avatar/checks/affect_parity.py`). C'est une verification
 *   ponctuelle et non un garde-fou permanent : en toucher une sans toucher
 *   l'autre demande de la relancer.
 */


/** Ce que chaque registre autorise comme intensite maximale. */
export const CEILING = {
  formal: 0.5,
  professional: 0.68,
  casual: 0.88,
  intimate: 1,
};


/** Vers quoi l'affect retombe quand plus rien ne le nourrit. */
export const BASELINE = {
  valence: 0.05,
  arousal: 0.22,
  attention: 0.8,
  confidence: 0.72,
  urgency: 0,
};


/** Demi-vie de la decroissance, en secondes. */
export const DECAY_HALF_LIFE_S = 22;


/** Tous les seuils de derivation. Partages avec presence/affect.py. */
export const TUNING = {
  intensity_base: 0.12,
  intensity_arousal: 0.58,
  intensity_valence: 0.34,
  intensity_urgency_min: 0.75,
  gaze_urgency: 0.55,
  gaze_attention_high: 0.62,
  gaze_attention_mid: 0.35,
  gaze_confidence_mid: 0.5,
  gaze_arousal_low: 0.12,
  posture_urgency: 0.6,
  posture_arousal_dormant: 0.1,
  posture_attention_dormant: 0.35,
  posture_attention_high: 0.75,
  posture_arousal_high: 0.3,
  posture_confidence_mid: 0.55,
  posture_attention_mid: 0.55,
  tempo_base: 0.72,
  tempo_arousal: 0.45,
  tempo_urgency: 0.55,
  tempo_min: 0.55,
  tempo_max: 1.85,
  stillness_base: 0.25,
  stillness_calm: 0.45,
  stillness_confidence: 0.3,
  gaze_hold_base: 1.2,
  gaze_hold_attention: 4.5,
  gaze_hold_urgency: 0.8,
};


/**
 * Les douze ancres : (valence, arousal, confidence, urgency).
 *
 * Choisir un visage est une recherche du plus proche voisin pondere.
 */
export const ANCHORS = {
  neutral: [0, 0.2, 0.7, 0],
  amused: [0.5, 0.35, 0.82, 0],
  happy: [0.85, 0.7, 0.8, 0],
  proud: [0.6, 0.45, 0.97, 0],
  surprised: [0.1, 0.92, 0.35, 0.25],
  concerned: [-0.45, 0.62, 0.45, 0.65],
  serious: [-0.1, 0.48, 0.88, 0.75],
  thinking: [0, 0.38, 0.42, 0.05],
  confused: [-0.2, 0.45, 0.12, 0.05],
  sad: [-0.72, 0.22, 0.5, 0],
  tired: [-0.18, 0.04, 0.48, 0],
  angry: [-0.82, 0.88, 0.82, 0.55],
};


/** Ce que chaque axe pese dans la recherche. */
export const WEIGHTS = [1, 0.85, 0.55, 0.7];


export const SOCIAL_MODES = Object.keys(CEILING);

function clamp(value, low = 0, high = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(low, Math.min(high, n)) : low;
}

/**
 * Normalise un etat : bornes appliquees, defauts remplis.
 *
 * Meme role que `Affect.__post_init__` en Python — un etat hors bornes qui
 * circule produira une intensite hors bornes ailleurs, sans trace de son
 * origine.
 */
export function normaliseAffect(a = {}) {
  return {
    valence: clamp(a.valence === undefined ? BASELINE.valence : a.valence, -1, 1),
    arousal: clamp(a.arousal === undefined ? BASELINE.arousal : a.arousal),
    attention: clamp(a.attention === undefined ? BASELINE.attention : a.attention),
    confidence: clamp(a.confidence === undefined ? BASELINE.confidence : a.confidence),
    urgency: clamp(a.urgency === undefined ? BASELINE.urgency : a.urgency),
    social_mode: CEILING[a.social_mode] !== undefined ? a.social_mode : 'professional',
  };
}

/** Le visage le plus proche de cet etat. */
export function expressionFor(affect) {
  const a = normaliseAffect(affect);
  const point = [a.valence, a.arousal, a.confidence, a.urgency];
  let best = null;
  for (const name in ANCHORS) {
    const anchor = ANCHORS[name];
    let sum = 0;
    for (let i = 0; i < 4; i++) {
      const d = WEIGHTS[i] * (point[i] - anchor[i]);
      sum += d * d;
    }
    const distance = Math.sqrt(sum);
    if (best === null || distance < best[0]) best = [distance, name];
  }
  return best[1];
}

/** A quel point ce visage se voit. */
export function intensityFor(affect) {
  const a = normaliseAffect(affect);
  let raw = TUNING.intensity_base
    + a.arousal * TUNING.intensity_arousal
    + Math.abs(a.valence) * TUNING.intensity_valence;
  raw = Math.max(raw, a.urgency * TUNING.intensity_urgency_min);
  return Math.min(raw, CEILING[a.social_mode]);
}

/** Ou vont les yeux. L'urgence passe devant l'attention. */
export function gazeFor(affect) {
  const a = normaliseAffect(affect);
  if (a.urgency >= TUNING.gaze_urgency) return 'user';
  if (a.attention >= TUNING.gaze_attention_high) return 'user';
  if (a.attention >= TUNING.gaze_attention_mid) {
    return a.confidence < TUNING.gaze_confidence_mid ? 'away' : 'around';
  }
  if (a.arousal < TUNING.gaze_arousal_low) return 'down';
  return 'around';
}

/** Le canal lent. */
export function postureFor(affect) {
  const a = normaliseAffect(affect);
  const urgent = a.urgency >= TUNING.posture_urgency;
  if (a.social_mode === 'formal' || urgent) return urgent ? 'focused' : 'formal';
  if (a.arousal < TUNING.posture_arousal_dormant
      && a.attention < TUNING.posture_attention_dormant) return 'dormant';
  if (a.attention >= TUNING.posture_attention_high
      && a.arousal >= TUNING.posture_arousal_high) {
    return a.confidence < TUNING.posture_confidence_mid ? 'focused' : 'attentive';
  }
  if (a.attention >= TUNING.posture_attention_mid) return 'attentive';
  return 'relaxed';
}

/** Vitesse des gestes, 1.0 = nominal. */
export function tempoFor(affect) {
  const a = normaliseAffect(affect);
  const speed = TUNING.tempo_base
    + a.arousal * TUNING.tempo_arousal
    + a.urgency * TUNING.tempo_urgency;
  return clamp(speed, TUNING.tempo_min, TUNING.tempo_max);
}

/** Immobilite du repos, 0 agite, 1 statue. */
export function stillnessFor(affect) {
  const a = normaliseAffect(affect);
  const calm = 1 - a.arousal;
  return clamp(TUNING.stillness_base
    + calm * TUNING.stillness_calm
    + a.confidence * TUNING.stillness_confidence);
}

/** Combien de temps le regard tient avant de deriver, en secondes. */
export function gazeHoldFor(affect) {
  const a = normaliseAffect(affect);
  return TUNING.gaze_hold_base
    + a.attention * TUNING.gaze_hold_attention
    - a.urgency * TUNING.gaze_hold_urgency;
}

/**
 * Tout d'un coup : ce que cet etat implique.
 *
 * C'est la forme que le labo affiche — la decision DERIVEE, a cote de la
 * decision executee.
 */
export function deriveFrom(affect) {
  const a = normaliseAffect(affect);
  return {
    affect: a,
    expression: expressionFor(a),
    intensity: intensityFor(a),
    gaze: gazeFor(a),
    posture: postureFor(a),
    tempo: tempoFor(a),
    stillness: stillnessFor(a),
    gaze_hold_s: gazeHoldFor(a),
  };
}

/**
 * Les seize intentions : (valence, arousal, attention, confidence, urgency,
 * geste, regard).
 *
 * Genere depuis `presence/affect.py`. Une intention nomme la SITUATION ; l'etat
 * interieur, et tout ce qui en derive, se calculent. Le geste est toujours
 * celui d'un corps complet — `FALLBACK_CHAIN` le rabat sur ce que ce corps-ci
 * sait faire, et c'est ce qui permet au meme mot de produire un salut de la
 * main un jour et un hochement de tete aujourd'hui.
 *
 * Le regard vaut `null` quand il se derive de l'attention. Il n'est explicite
 * que la ou la derivation ne peut pas aller : `gazeFor()` ne rend jamais
 * `screen`, parce que l'attention ne sait pas qu'il existe un ecran.
 */
export const INTENTS = {
  greet         : [ 0.76, 0.64, 0.95, 0.82, 0.00, 'wave', 'user'],
  farewell      : [ 0.32, 0.24, 0.90, 0.86, 0.00, 'bow', 'user'],
  acknowledge   : [ 0.15, 0.30, 0.92, 0.88, 0.05, 'nod', 'user'],
  wait          : [ 0.10, 0.26, 0.96, 0.68, 0.00, 'lean_in', 'user'],
  investigate   : [ 0.00, 0.55, 0.30, 0.55, 0.15, 'turn', 'screen'],
  think         : [ 0.00, 0.36, 0.28, 0.40, 0.05, 'think', 'away'],
  explain       : [ 0.10, 0.42, 0.88, 0.84, 0.05, 'explain', 'user'],
  agree         : [ 0.45, 0.36, 0.92, 0.92, 0.00, 'nod', 'user'],
  disagree      : [-0.22, 0.48, 0.92, 0.86, 0.55, 'shake_head', 'user'],
  amuse         : [ 0.58, 0.38, 0.86, 0.86, 0.00, 'tilt_head', 'user'],
  confirm       : [-0.05, 0.52, 0.96, 0.90, 0.72, 'look_at_user', 'user'],
  warn          : [-0.48, 0.74, 0.95, 0.78, 0.88, 'lean_in', 'user'],
  reassure      : [ 0.38, 0.22, 0.94, 0.92, 0.00, 'blink_slow', 'user'],
  report_success: [ 0.75, 0.58, 0.88, 0.94, 0.00, 'thumbs_up', 'user'],
  report_failure: [-0.52, 0.58, 0.92, 0.38, 0.48, 'sigh', 'user'],
  apologise     : [-0.58, 0.32, 0.90, 0.30, 0.25, 'bow', 'down'],
};

/** L'etat interieur ou cette intention met JARVIS. `null` si le mot est inconnu. */
export function affectForIntent(name) {
  const row = INTENTS[name];
  if (!row) return null;
  return { valence: row[0], arousal: row[1], attention: row[2],
           confidence: row[3], urgency: row[4] };
}

/** Par quoi elle voudrait passer, sur un corps qui peut tout. */
export function gestureForIntent(name) {
  const row = INTENTS[name];
  return row ? row[5] : null;
}

/** Ou elle regarde, ou `null` pour laisser l'etat decider. */
export function gazeForIntent(name) {
  const row = INTENTS[name];
  return row ? row[6] : null;
}
