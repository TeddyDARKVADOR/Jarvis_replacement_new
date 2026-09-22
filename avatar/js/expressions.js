/**
 * expressions.js — the twelve faces, in JavaScript.
 *
 * GENERATED FROM `presence/vocabulary.py`. Do not edit by hand: regenerate, or
 * edit the Python and regenerate. `presence/selftest.py` parses this file and
 * fails on any difference, so a hand edit here is caught rather than silently
 * lived with.
 *
 * WHY IT IS DUPLICATED AT ALL
 *   The panel never needs it — the director resolves an expression into
 *   weights in Python and sends the weights. But `lab.html` has to be usable
 *   with no Python running at all: the whole point of the lab is to answer
 *   "does this model smile" before wiring it to anything. A lab that needed
 *   the server would be useless at exactly the moment it is needed.
 *
 *   The same table also makes an offline renderer possible later — a phone that
 *   has lost the link can still put a face on, from a word.
 */
/** How fast each family of shapes comes in as intensity rises. */
const CURVE = {
  brow: 0.65,
  eye: 0.75,
  cheek: 0.9,
  nose: 1.0,
  mouth: 1.3,
  jaw: 1.45,
  tongue: 1.6
};

function family(shape) {
  for (const prefix in CURVE) if (shape.startsWith(prefix)) return prefix;
  return 'mouth';
}
/** Each face authored at full intensity. */
const SHAPES = {
  neutral: {},
  happy: {
    mouthSmileLeft: 0.85,
    mouthSmileRight: 0.85,
    cheekSquintLeft: 0.55,
    cheekSquintRight: 0.55,
    eyeSquintLeft: 0.42,
    eyeSquintRight: 0.42,
    mouthDimpleLeft: 0.3,
    mouthDimpleRight: 0.3,
    browInnerUp: 0.15
  },
  sad: {
    browInnerUp: 0.8,
    browDownLeft: 0.25,
    browDownRight: 0.25,
    mouthFrownLeft: 0.6,
    mouthFrownRight: 0.6,
    mouthShrugLower: 0.35,
    eyeLookDownLeft: 0.3,
    eyeLookDownRight: 0.3,
    eyeBlinkLeft: 0.18,
    eyeBlinkRight: 0.18
  },
  angry: {
    browDownLeft: 0.85,
    browDownRight: 0.85,
    eyeSquintLeft: 0.55,
    eyeSquintRight: 0.55,
    noseSneerLeft: 0.4,
    noseSneerRight: 0.4,
    mouthPressLeft: 0.5,
    mouthPressRight: 0.5,
    jawForward: 0.25
  },
  surprised: {
    browInnerUp: 0.9,
    browOuterUpLeft: 0.85,
    browOuterUpRight: 0.85,
    eyeWideLeft: 0.8,
    eyeWideRight: 0.8,
    jawOpen: 0.45,
    mouthFunnel: 0.2
  },
  concerned: {
    browInnerUp: 0.7,
    browDownLeft: 0.35,
    browDownRight: 0.35,
    eyeWideLeft: 0.3,
    eyeWideRight: 0.3,
    mouthFrownLeft: 0.28,
    mouthFrownRight: 0.28,
    mouthPressLeft: 0.35,
    mouthPressRight: 0.35
  },
  thinking: {
    browDownLeft: 0.45,
    browOuterUpRight: 0.55,
    browInnerUp: 0.25,
    eyeSquintLeft: 0.3,
    eyeSquintRight: 0.2,
    eyeLookUpLeft: 0.35,
    eyeLookUpRight: 0.35,
    mouthLeft: 0.3,
    mouthPucker: 0.22,
    mouthRollLower: 0.2
  },
  amused: {
    mouthSmileLeft: 0.55,
    mouthSmileRight: 0.28,
    mouthDimpleLeft: 0.4,
    browOuterUpLeft: 0.45,
    eyeSquintLeft: 0.3,
    eyeSquintRight: 0.2,
    cheekSquintLeft: 0.35,
    cheekSquintRight: 0.15
  },
  serious: {
    browDownLeft: 0.4,
    browDownRight: 0.4,
    eyeWideLeft: 0.15,
    eyeWideRight: 0.15,
    mouthPressLeft: 0.45,
    mouthPressRight: 0.45,
    mouthClose: 0.3,
    jawForward: 0.12
  },
  confused: {
    browDownLeft: 0.55,
    browOuterUpRight: 0.7,
    browInnerUp: 0.3,
    eyeSquintLeft: 0.4,
    eyeWideRight: 0.25,
    mouthLeft: 0.4,
    mouthPucker: 0.3,
    mouthShrugUpper: 0.25
  },
  proud: {
    mouthSmileLeft: 0.4,
    mouthSmileRight: 0.4,
    mouthPressLeft: 0.25,
    mouthPressRight: 0.25,
    eyeSquintLeft: 0.35,
    eyeSquintRight: 0.35,
    browOuterUpLeft: 0.2,
    browOuterUpRight: 0.2,
    jawForward: 0.2,
    cheekSquintLeft: 0.3,
    cheekSquintRight: 0.3
  },
  tired: {
    eyeBlinkLeft: 0.45,
    eyeBlinkRight: 0.45,
    browInnerUp: 0.35,
    browDownLeft: 0.2,
    browDownRight: 0.2,
    mouthFrownLeft: 0.2,
    mouthFrownRight: 0.2,
    jawOpen: 0.1,
    eyeLookDownLeft: 0.25,
    eyeLookDownRight: 0.25
  }
};
/** The eyeLook* coefficients, per gaze. */
const GAZE = {
  user: {},
  screen: {
    eyeLookInLeft: 0.55,
    eyeLookOutRight: 0.55,
    eyeLookUpLeft: 0.1,
    eyeLookUpRight: 0.1
  },
  away: {
    eyeLookOutLeft: 0.6,
    eyeLookInRight: 0.6,
    eyeLookUpLeft: 0.3,
    eyeLookUpRight: 0.3
  },
  down: {
    eyeLookDownLeft: 0.65,
    eyeLookDownRight: 0.65,
    eyeBlinkLeft: 0.15,
    eyeBlinkRight: 0.15
  },
  around: {
    eyeLookOutLeft: 0.25,
    eyeLookInRight: 0.25
  },
  closed: {
    eyeBlinkLeft: 1.0,
    eyeBlinkRight: 1.0
  }
};

export const EXPRESSIONS = Object.keys(SHAPES);
export const GAZES = Object.keys(GAZE);

function scaled(shapes, intensity) {
  const out = {};
  if (intensity <= 0) return out;
  for (const shape in shapes) {
    const weight = shapes[shape] * Math.pow(intensity, CURVE[family(shape)]);
    if (weight >= 0.005) out[shape] = Math.min(1, weight);
  }
  return out;
}

/**
 * One expression and one gaze, resolved to ARKit weights.
 *
 * Combined with max(), never by adding — two shapes that both raise a brow
 * must not sum past 1.0 and clip. Identical to `presence.vocabulary.face`.
 */
export function face(expression, intensity, gaze = 'user') {
  const i = Math.max(0, Math.min(1, Number(intensity) || 0));
  const out = scaled(SHAPES[expression] || {}, i);
  const eyes = GAZE[gaze] || {};
  for (const shape in eyes) out[shape] = Math.max(out[shape] || 0, eyes[shape]);
  return out;
}
