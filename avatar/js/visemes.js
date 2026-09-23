/**
 * visemes.js — the mouth shapes speech is made of.
 *
 * WHY THIS IS DUPLICATED FROM PYTHON, AND WHY THAT IS SAFE
 *   `presence/vocabulary.py` holds the same eight shapes. They are duplicated
 *   rather than sent over the wire because the mouth changes 4–7 times a second
 *   and the network carries one message per *decision* — shipping a viseme
 *   table down a websocket to redraw a lip would be putting 200 ms of latency
 *   in front of a syllable.
 *
 *   A duplicate that nobody checks is a duplicate that drifts, so it is
 *   checked: `presence/selftest.py` parses THIS FILE and fails if any shape,
 *   any name or any weight differs from the Python table. Same technique
 *   `context/selftest.py` uses to keep the Kotlin heartbeat and the Python TTL
 *   honest with each other.
 *
 *   Edit both, or edit neither. The test will say so either way.
 *
 * WHY EIGHT AND NOT FIFTEEN
 *   The Oculus/Preston-Blair set has fifteen. Eight is what an amplitude
 *   envelope can honestly distinguish (see lipsync.js), and eight shapes read
 *   as speech at the size this panel is actually looked at. The remaining seven
 *   become worth adding the day a real phoneme source is wired in — at which
 *   point they go here and in the Python table together.
 */

export const VISEMES = {
  sil: { mouthClose: 0.25 },

  AA: { jawOpen: 0.70, mouthLowerDownLeft: 0.35, mouthLowerDownRight: 0.35 },

  E: {
    jawOpen: 0.32,
    mouthStretchLeft: 0.45, mouthStretchRight: 0.45,
    mouthUpperUpLeft: 0.20, mouthUpperUpRight: 0.20,
  },

  I: {
    jawOpen: 0.18,
    mouthSmileLeft: 0.30, mouthSmileRight: 0.30,
    mouthStretchLeft: 0.25, mouthStretchRight: 0.25,
  },

  O: { jawOpen: 0.50, mouthFunnel: 0.60, mouthPucker: 0.30 },

  U: { jawOpen: 0.20, mouthPucker: 0.75, mouthFunnel: 0.35 },

  M: { mouthClose: 0.80, mouthPressLeft: 0.40, mouthPressRight: 0.40 },

  F: {
    mouthLowerDownLeft: 0.30, mouthLowerDownRight: 0.30,
    mouthRollLower: 0.45,
    mouthUpperUpLeft: 0.25, mouthUpperUpRight: 0.25,
  },
};
