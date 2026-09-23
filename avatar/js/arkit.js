/**
 * arkit.js — the 52 names, in one place on the JavaScript side.
 *
 * WHY ITS OWN MODULE
 *   Three files need them — the glTF loader to match morph targets, the VRM
 *   body to decide whether a VRM carries the ARKit set natively, and the lab to
 *   build one slider per shape. Keeping them in the loader meant the VRM body
 *   importing the loader that imports the VRM body, which ES modules tolerate
 *   and nobody should have to reason about.
 *
 * WHY THEY ARE DUPLICATED FROM PYTHON AT ALL
 *   `presence/vocabulary.py` holds the same list. It is duplicated rather than
 *   sent over the wire because this module has to work in a browser with no
 *   Python anywhere near it — that is the whole premise of `lab.html`.
 *
 *   A duplicate nobody checks is a duplicate that drifts, so it is checked:
 *   `presence/selftest.py` parses this file and fails if a single name or the
 *   order differs from `vocabulary.ARKIT_52`.
 */

export const ARKIT_NAMES = [
  'eyeBlinkLeft', 'eyeLookDownLeft', 'eyeLookInLeft', 'eyeLookOutLeft',
  'eyeLookUpLeft', 'eyeSquintLeft', 'eyeWideLeft',
  'eyeBlinkRight', 'eyeLookDownRight', 'eyeLookInRight', 'eyeLookOutRight',
  'eyeLookUpRight', 'eyeSquintRight', 'eyeWideRight',
  'jawForward', 'jawLeft', 'jawRight', 'jawOpen',
  'mouthClose', 'mouthFunnel', 'mouthPucker', 'mouthLeft', 'mouthRight',
  'mouthSmileLeft', 'mouthSmileRight', 'mouthFrownLeft', 'mouthFrownRight',
  'mouthDimpleLeft', 'mouthDimpleRight', 'mouthStretchLeft', 'mouthStretchRight',
  'mouthRollLower', 'mouthRollUpper', 'mouthShrugLower', 'mouthShrugUpper',
  'mouthPressLeft', 'mouthPressRight', 'mouthLowerDownLeft', 'mouthLowerDownRight',
  'mouthUpperUpLeft', 'mouthUpperUpRight',
  'browDownLeft', 'browDownRight', 'browInnerUp', 'browOuterUpLeft',
  'browOuterUpRight',
  'cheekPuff', 'cheekSquintLeft', 'cheekSquintRight',
  'noseSneerLeft', 'noseSneerRight',
  'tongueOut',
];
