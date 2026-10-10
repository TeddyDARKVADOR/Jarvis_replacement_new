/**
 * sound.js — interface bleeps, synthesised, no audio files.
 *
 * Short, quiet and only on events that matter: the wake word, a sent command,
 * an answer arriving, a confirmation, an error, the boot. Never on hover —
 * a HUD that chirps under the mouse is a HUD people mute.
 */
let ctx = null;
let enabled = true;
let volume = .35;

function ac() {
  if (!ctx) {
    try { ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch { ctx = null; }
  }
  if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}

function tone(freq, start, dur, { type = 'sine', gain = .2, to = null } = {}) {
  const a = ac(); if (!a) return;
  const t0 = a.currentTime + start;
  const o = a.createOscillator();
  const gn = a.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t0);
  if (to) o.frequency.exponentialRampToValueAtTime(to, t0 + dur);
  gn.gain.setValueAtTime(0, t0);
  gn.gain.linearRampToValueAtTime(gain * volume, t0 + .012);
  gn.gain.exponentialRampToValueAtTime(.0001, t0 + dur);
  o.connect(gn).connect(a.destination);
  o.start(t0); o.stop(t0 + dur + .05);
}

const SOUNDS = {
  boot()    { tone(180, 0, .9, { type: 'sawtooth', gain: .05, to: 900 }); tone(1320, .55, .5, { gain: .12 }); },
  wake()    { tone(660, 0, .12, { gain: .18 }); tone(990, .09, .22, { gain: .16 }); },
  send()    { tone(1200, 0, .06, { type: 'square', gain: .05 }); },
  reply()   { tone(880, 0, .18, { gain: .08 }); tone(1320, .07, .25, { gain: .06 }); },
  confirm() { tone(740, 0, .16, { type: 'triangle', gain: .2 }); tone(740, .22, .16, { type: 'triangle', gain: .2 }); },
  error()   { tone(160, 0, .35, { type: 'sawtooth', gain: .08, to: 90 }); },
  click()   { tone(2000, 0, .025, { type: 'square', gain: .03 }); },
  open()    { tone(520, 0, .1, { gain: .1, to: 780 }); },
  close()   { tone(780, 0, .1, { gain: .08, to: 420 }); },
};

export const sound = {
  play(name) { if (enabled && SOUNDS[name]) { try { SOUNDS[name](); } catch { /* audio is decoration */ } } },
  set(on, vol) { enabled = !!on; if (typeof vol === 'number') volume = vol; },
  get enabled() { return enabled; },
};
