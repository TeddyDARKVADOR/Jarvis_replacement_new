/**
 * director.js — `presence/director.py`, en JavaScript, pour le labo.
 *
 * POURQUOI UN MIROIR DU CERVEAU COTE NAVIGATEUR
 *   Le labo doit repondre a « que ferait le panneau de CETTE directive » sans
 *   qu'aucun Python ne tourne — sous `jarvis://`, dans un navigateur, sur un
 *   modele qu'on vient de glisser. Il le faisait avec une derivation a lui,
 *   qui divergeait deja du directeur sur trois points mesurables :
 *
 *     * le repli    le labo jouait `idle` quand un geste manquait, le panneau
 *                   suit `FALLBACK_CHAIN` (greet : labo idle, panneau nod)
 *     * la posture  un visage force prenait la posture DERIVEE ; le directeur
 *                   prend celle du visage (`_POSTURE_OF`)
 *     * la colere   le labo affichait angry 0.9 ; le directeur plafonne a 0.45
 *
 *   Trois fois « le labo montre un comportement que le produit n'a pas ».
 *   Ce fichier refait `resolve()` et `parse()` ligne pour ligne, et
 *   `avatar/checks/director_parity.py` les confronte au Python sur plusieurs
 *   centaines de directives : meme JSON, champ par champ.
 *
 * CE QUE CE FICHIER NE FAIT PAS
 *   Il ne tourne jamais dans le panneau. Le panneau recoit des Performances
 *   deja resolues par Python ; la seule chose qui doit y etre juste est le
 *   moteur. Ce miroir sert a DECIDER comme Python, pour qu'un moteur identique
 *   joue une decision identique.
 */

import {
  BASELINE, DECAY_HALF_LIFE_S, normaliseAffect, expressionFor, fadingExpression, intensityFor,
  gazeFor, postureFor, tempoFor, stillnessFor, gazeHoldFor, affectForIntent,
  gestureForIntent, gazeForIntent, accentForIntent, INTENTS, SOCIAL_MODES,
} from './affect.js';
import { face, EXPRESSIONS, GAZES } from './expressions.js';
import { Catalogue, GESTURES } from './catalog.js';

/** Miroir de `INTENT_TTL_S`. */
export const INTENT_TTL_S = 25.0;

/** Miroir de `_USER_FLOOR` : l'utilisateur a la parole. */
const USER_FLOOR = new Set(['LISTENING', 'CONFIRM']);
/** Miroir de `ANGRY_CEILING`. */
export const ANGRY_CEILING = 0.45;

/** Miroir de `_DECIDED_GAZE` (presence/director.py). */
export const DECIDED_GAZE = new Set(['explicit', 'intent', 'safety']);

export const POSTURES = ['attentive', 'relaxed', 'focused', 'formal', 'dormant'];

/** Miroir de `_REFLEX` : (expression, intensite, geste, regard, posture, hold_s). */
export const REFLEX = {
  LISTENING:  ['neutral', 0.15, 'lean_in', 'user', 'attentive', 0.0],
  THINKING:   ['thinking', 0.62, 'think', 'away', 'focused', 0.0],
  SPEAKING:   ['neutral', 0.22, 'explain', 'user', 'attentive', 0.0],
  SLEEPING:   ['tired', 0.55, 'idle', 'closed', 'dormant', 0.0],
  ACTIVE:     ['neutral', 0.10, 'idle', 'user', 'relaxed', 0.0],
  WAKING:     ['surprised', 0.35, 'look_at_user', 'user', 'attentive', 1.2],
  ERROR:      ['concerned', 0.65, 'shake_head', 'user', 'formal', 2.0],
  OFFLINE:    ['tired', 0.40, 'idle', 'down', 'dormant', 0.0],
  CONNECTING: ['neutral', 0.20, 'look_around', 'around', 'relaxed', 0.0],
  CONFIRM:    ['serious', 0.70, 'look_at_user', 'user', 'formal', 0.0],
};
const DEFAULT_REFLEX = ['neutral', 0.10, 'idle', 'user', 'relaxed', 0.0];

/** Miroir de `_REFLEX_AFFECT`. Les axes absents valent l'affect par defaut. */
export const REFLEX_AFFECT = {
  LISTENING:  { valence: 0.10, arousal: 0.30, attention: 0.95, confidence: 0.75 },
  THINKING:   { valence: 0.00, arousal: 0.40, attention: 0.35, confidence: 0.45 },
  SPEAKING:   { valence: 0.15, arousal: 0.45, attention: 0.85, confidence: 0.80 },
  SLEEPING:   { valence: -0.10, arousal: 0.03, attention: 0.05, confidence: 0.60 },
  ACTIVE:     {},
  WAKING:     { valence: 0.20, arousal: 0.75, attention: 0.90, confidence: 0.55 },
  ERROR:      { valence: -0.50, arousal: 0.65, attention: 0.90, confidence: 0.40, urgency: 0.60 },
  OFFLINE:    { valence: -0.25, arousal: 0.08, attention: 0.20, confidence: 0.50 },
  CONNECTING: { valence: 0.00, arousal: 0.35, attention: 0.40, confidence: 0.45 },
  CONFIRM:    { valence: -0.05, arousal: 0.50, attention: 0.98, confidence: 0.90,
                urgency: 0.55, social_mode: 'formal' },
};

/** Miroir de `_POSTURE_OF`. */
export const POSTURE_OF = {
  serious: 'formal', concerned: 'focused', thinking: 'focused', angry: 'formal',
  tired: 'dormant', proud: 'attentive', happy: 'attentive', amused: 'relaxed',
  sad: 'relaxed', surprised: 'attentive', confused: 'attentive', neutral: 'relaxed',
};

const clamp = (v, lo = 0, hi = 1) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : lo;
};

/** `Affect.decayed` : vers la base, l'urgence deux fois plus vite. */
export function decayed(affect, seconds) {
  if (seconds <= 0) return affect;
  const k = 0.5 ** (seconds / DECAY_HALF_LIFE_S);
  const toward = (key) => BASELINE[key] + (affect[key] - BASELINE[key]) * k;
  return normaliseAffect({
    valence: toward('valence'),
    arousal: toward('arousal'),
    attention: toward('attention'),
    confidence: toward('confidence'),
    urgency: affect.urgency * k * k,
    social_mode: affect.social_mode,
  });
}

// ── lire une directive : miroir de `_coerce` ─────────────────────────────────

function isNumber(v) { return typeof v === 'number' && Number.isFinite(v); }

function number(raw, keys, fallback) {
  for (const key of keys) if (key in raw && isNumber(raw[key])) return raw[key];
  return fallback;
}

function wordOf(raw, key, vocabulary) {
  const value = raw[key];
  if (value === undefined || value === null) return null;
  const w = String(value).trim().toLowerCase();
  return vocabulary.includes(w) ? w : null;
}

function affectFrom(raw) {
  const inner = raw.emotion && typeof raw.emotion === 'object' ? raw.emotion : {};
  const pick = (axis) => {
    const a = number(inner, [axis], null);
    return a !== null ? a : number(raw, [axis], null);
  };
  const valence = pick('valence');
  const arousal = pick('arousal');
  const attention = number(raw, ['attention'], null);
  const confidence = number(raw, ['confidence'], null);
  const urgency = number(raw, ['urgency'], null);
  const modeRaw = raw.socialMode !== undefined ? raw.socialMode : raw.social_mode;
  let mode = null;
  if (modeRaw !== undefined && modeRaw !== null) {
    const m = String(modeRaw).trim().toLowerCase();
    mode = SOCIAL_MODES.includes(m) ? m : null;
  }
  if ([valence, arousal, attention, confidence, urgency].every((v) => v === null) && mode === null) {
    return null;
  }
  return normaliseAffect({
    valence: valence === null ? undefined : valence,
    arousal: arousal === null ? undefined : arousal,
    attention: attention === null ? undefined : attention,
    confidence: confidence === null ? undefined : confidence,
    urgency: urgency === null ? undefined : urgency,
    social_mode: mode || 'professional',
  });
}

function affectAxes(raw) {
  const inner = raw.emotion && typeof raw.emotion === 'object' ? raw.emotion : {};
  const names = [];
  for (const axis of ['valence', 'arousal']) {
    if (number(inner, [axis], null) !== null || number(raw, [axis], null) !== null) names.push(axis);
  }
  for (const axis of ['attention', 'confidence', 'urgency']) {
    if (number(raw, [axis], null) !== null) names.push(axis);
  }
  const mode = raw.socialMode !== undefined ? raw.socialMode : raw.social_mode;
  if (mode !== undefined && mode !== null) names.push('social_mode');
  return names;
}

/**
 * Une directive, telle que `presence.parse` la lirait. `null` si ce n'en est
 * pas une. Accepte l'objet ou son texte JSON — les deux formes du fil.
 */
export function parseDirective(input) {
  let raw = input;
  if (typeof raw === 'string') {
    try { raw = JSON.parse(raw); } catch { return null; }
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;

  let expression = wordOf(raw, 'expression', EXPRESSIONS);
  let affect = affectFrom(raw);

  let gesture = wordOf(raw, 'gesture', GESTURES);
  if (gesture === null) gesture = wordOf(raw, 'head', GESTURES);
  const gestureGiven = gesture !== null;

  const explicitGaze = wordOf(raw, 'gaze', GAZES);
  let gazeFrom = explicitGaze !== null ? 'explicit' : '';
  let gaze = explicitGaze;

  const intent = wordOf(raw, 'intent', Object.keys(INTENTS));
  if (intent !== null) {
    const base = Object.assign({}, affectForIntent(intent),
                               { social_mode: affect ? affect.social_mode : 'professional' });
    if (affect) for (const axis of affectAxes(raw)) base[axis] = affect[axis];
    affect = normaliseAffect(base);
    if (!gestureGiven) gesture = gestureForIntent(intent);
    if (explicitGaze === null) {
      const preferred = gazeForIntent(intent);
      if (preferred) { gaze = preferred; gazeFrom = 'intent'; }
    }
  }
  if (gesture === null) gesture = 'idle';

  if (expression === null && typeof raw.emotion === 'string') {
    const e = raw.emotion.trim().toLowerCase();
    expression = EXPRESSIONS.includes(e) ? e : null;
  }

  const posture = wordOf(raw, 'posture', POSTURES);
  // Un regard ou une posture seuls SONT une directive — voir `_coerce`.
  if (expression === null && gesture === 'idle' && affect === null && intent === null
      && explicitGaze === null && posture === null) return null;

  const givenIntensity = number(raw, ['intensity', 'emotionIntensity', 'emotion_intensity'], null);
  return {
    expression: expression || 'neutral',
    intensity: clamp(givenIntensity === null ? 0.5 : givenIntensity),
    expression_given: expression !== null,
    intensity_given: givenIntensity !== null,
    gesture,
    gaze,
    posture,
    reason: String(raw.reason !== undefined ? raw.reason : '').slice(0, 200),
    affect,
    intent,
    gaze_from: gazeFrom,
  };
}

// ── resoudre : miroir de `Director.resolve` ──────────────────────────────────

const r3 = (v) => Math.round(v * 1000) / 1000;
const r2 = (v) => Math.round(v * 100) / 100;

export class Director {
  /** @param {Catalogue} catalogue  le corps contre lequel on resout */
  constructor(catalogue) {
    this.cat = catalogue || new Catalogue(['head', 'torso'], 'full', []);
    this.intent = null;
    this.intentAt = 0;
    this.intentSeq = 0;
    this.affect = null;
    this.affectAt = 0;
    // Une intention appartient a son tour de parole — voir `_turn` en Python.
    this.turn = 0;
    this.intentTurn = 0;
    this.lastWord = '';
  }

  setIntent(directive, now = 0) {
    this.intent = directive;
    this.intentAt = now;
    this.intentSeq += 1;
    this.intentTurn = this.turn;
    if (directive && directive.affect) { this.affect = directive.affect; this.affectAt = now; }
  }

  setAffect(affect, now = 0) {
    this.affect = normaliseAffect(affect);
    this.affectAt = now;
  }

  clearIntent() { this.intent = null; this.intentAt = 0; }

  affectNow(now) {
    return this.affect ? decayed(this.affect, now - this.affectAt) : null;
  }

  /**
   * Reflexe, puis affect, puis intention — et la Performance en JSON, comme
   * `Performance.as_json()` l'ecrirait. Avec, en plus, la TRACE de la decision
   * (`_trace`) : ce que le labo affiche, et que le fil ne porte pas.
   */
  resolve(stateWord, { speechLevel = 0, now = 0 } = {}) {
    const word = String(stateWord || '').toUpperCase();
    if (word === 'THINKING' && USER_FLOOR.has(this.lastWord)) this.turn += 1;
    this.lastWord = word;
    let [expression, intensity, gesture, gaze, posture, hold] = REFLEX[word] || DEFAULT_REFLEX;
    let affect = normaliseAffect(REFLEX_AFFECT[word] || {});
    let reason = `reflex:${word}`;
    let derived = false;
    let gazeSource = 'reflex';
    let gestureId = `reflex:${word}`;
    const trace = [`ETAT ${word || '-'}`];

    const live = this.affectNow(now);
    if (live) {
      affect = live;
      // Un etat qui retombe garde son visage, puis le neutre. Voir
      // `presence.affect.fading_expression`.
      expression = fadingExpression(this.affect, affect);
      intensity = intensityFor(affect);
      gaze = gazeFor(affect);
      posture = postureFor(affect);
      reason = `affect:v${fmtSigned(affect.valence)} a${affect.arousal.toFixed(2)} `
        + `att${affect.attention.toFixed(2)} conf${affect.confidence.toFixed(2)} `
        + `urg${affect.urgency.toFixed(2)}`;
      derived = true;
      gazeSource = 'affect';
      trace.push(`AFFECT ${expression} ${intensity.toFixed(2)}`);
    }

    const intent = this.intent;
    let chosen = null;
    const active = intent !== null && (now - this.intentAt) <= INTENT_TTL_S
      && this.intentTurn === this.turn;
    if (active) {
      chosen = intent.intent || null;
      if (!(derived && intent.affect)) {
        // Un visage que la directive ne nomme pas n'est qu'un defaut : il ne
        // remplace pas celui de l'etat. Miroir de `resolve()` en Python.
        const named = intent.expression_given || intent.expression !== 'neutral';
        if (named) {
          expression = intent.expression;
          intensity = clamp(intent.intensity);
          posture = intent.posture || POSTURE_OF[expression] || posture;
        } else if (intent.posture) {
          posture = intent.posture;
        }
        reason = intent.reason || `intent:${expression}`;
      } else {
        if (intent.reason) reason = intent.reason;
        // Ce que JARVIS a NOMME corrige la base posee par l'intention.
        if (intent.expression_given) {
          expression = intent.expression;
          if (intent.intensity_given) intensity = clamp(intent.intensity);
          posture = POSTURE_OF[expression] || posture;
        }
        if (intent.posture) posture = intent.posture;
      }
      gesture = intent.gesture;
      if (intent.gaze) {
        gaze = intent.gaze;
        gazeSource = intent.gaze_from || 'explicit';
      }
      gestureId = `intent#${this.intentSeq}`;
      trace.push(`INTENT ${chosen || '(visage nomme)'}`);
    }
    if (derived || active) hold = 0;

    if (word === 'SLEEPING') {
      gaze = 'closed';
      posture = 'dormant';
      gazeSource = 'safety';
    }
    if (expression === 'angry') intensity = Math.min(intensity, ANGRY_CEILING);

    const requested = gesture;
    gesture = this.cat.resolve(gesture);
    trace.push(`GAZE ${gaze} (${gazeSource})`);
    trace.push(`MODEL CAPABILITY ${this.cat.motion}`);
    trace.push(`BODY ACTION ${requested}`);
    if (requested !== gesture) trace.push(`FALLBACK ${gesture}`);
    trace.push(`FACIAL TARGET ${expression} ${clamp(intensity).toFixed(2)}`);

    const accent = accentForIntent(chosen);
    if (accent) trace.push(`ACCENT ${accent}`);

    const blendshapes = {};
    const shapes = face(expression, intensity, gaze, DECIDED_GAZE.has(gazeSource));
    for (const k in shapes) blendshapes[k] = r3(shapes[k]);

    const payload = {
      expression,
      intensity: r3(clamp(intensity)),
      gesture,
      gaze,
      posture,
      speech_level: r3(clamp(speechLevel)),
      hold_s: r2(Math.max(0, hold)),
      tempo: r3(tempoFor(affect)),
      stillness: r3(stillnessFor(affect)),
      gaze_hold_s: r2(gazeHoldFor(affect)),
      blendshapes,
      affect: {
        valence: r3(affect.valence), arousal: r3(affect.arousal),
        attention: r3(affect.attention), confidence: r3(affect.confidence),
        urgency: r3(affect.urgency), social_mode: affect.social_mode,
      },
    };
    if (requested !== gesture) payload.requested_gesture = requested;
    if (chosen) payload.intent = chosen;
    payload.state = word;
    payload.gaze_source = gazeSource;
    payload.gesture_id = gestureId;
    if (accent) payload.accent = accent;
    if (reason) payload.reason = reason.slice(0, 200);
    Object.defineProperty(payload, '_trace', { value: trace, enumerable: false });
    return payload;
  }
}

function fmtSigned(v) {
  return (v >= 0 ? '+' : '') + v.toFixed(2);
}
