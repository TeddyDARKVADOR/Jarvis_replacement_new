/**
 * perf.js — the governor. Same HUD on every PC; the cost adapts, not the design.
 *
 * Nothing is ever removed. Every element stays on screen at every tier; what
 * changes is how expensively each one is drawn:
 *
 *   px3d      resolution of the 3D core (a fraction of the screen's pixels)
 *   detail    subdivision of the plasma sphere (40 is visually = 28 at 1080p)
 *   shell     how many of the 2 400 shell particles are drawn
 *   glow      'blur' = canvas shadowBlur (beautiful, very expensive);
 *             'cheap' = the same halo drawn as a wide faint stroke underneath
 *   px2d      resolution of the 2D layers (rings, scope, background)
 *   bgFps     the background's own frame rate (it moves slowly)
 *   fps       frame cap for the whole page
 *   backdrop  the panels' backdrop blur + the scanlines' blend mode — both
 *             force the compositor to redo full-screen work on every frame
 *   push      how often Python sends levels (Hz)
 *
 * HOW THE TIER IS CHOSEN
 *   1. A first guess from the hardware: a software WebGL renderer (no GPU, or a
 *      GPU Chromium blocklisted) starts at the bottom; few cores or little
 *      memory start low; an integrated GPU starts in the middle; a 4K screen
 *      starts one tier lower. The tier learned last time wins over the guess.
 *   2. Then it measures. Every 1.5 s the real frame rate is compared with the
 *      tier's target: two bad windows in a row → one tier down. Twelve good
 *      seconds → one tier up, at most to the highest tier that has not already
 *      failed this session, so it never oscillates.
 *   Windows where the page was hidden, paused or stalled (a window drag, a
 *   minimise) are not counted — they say nothing about this PC.
 */

export const TIERS = [
  { name: 'ULTRA',     fps: 60, px3d: 2,    px2d: 2,   detail: 40, shell: 2400, glow: 'blur',  bgFps: 30, backdrop: true,  push: 30 },
  { name: 'ÉLEVÉ',     fps: 60, px3d: 1.25, px2d: 1.5, detail: 40, shell: 2400, glow: 'blur',  bgFps: 30, backdrop: false, push: 30 },
  { name: 'ÉQUILIBRÉ', fps: 60, px3d: 1,    px2d: 1,   detail: 28, shell: 2000, glow: 'cheap', bgFps: 24, backdrop: false, push: 30 },
  { name: 'ÉCO',       fps: 45, px3d: .8,   px2d: 1,   detail: 20, shell: 1600, glow: 'cheap', bgFps: 20, backdrop: false, push: 24 },
  { name: 'MINIMAL',   fps: 30, px3d: .62,  px2d: .85, detail: 14, shell: 1200, glow: 'cheap', bgFps: 15, backdrop: false, push: 20 },
];

const WINDOW_MS = 1500;
const BAD_RATIO = .82;      // below 82 % of the target is a bad window
const GOOD_RATIO = .95;
const UP_AFTER_MS = 12000;
const UP_COOLDOWN_MS = 20000;

/** What the hardware says before a single frame is measured. */
export function guessTier() {
  let tier = 1;
  let gpu = '';
  let software = false;
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) { software = true; }
    else {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      gpu = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER) || '');
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
  } catch { software = true; }
  if (/swiftshader|llvmpipe|software|basic render|microsoft basic/i.test(gpu)) software = true;

  const cores = navigator.hardwareConcurrency || 4;
  const mem = navigator.deviceMemory || 8;
  const pixels = screen.width * screen.height * (devicePixelRatio || 1) ** 2;

  if (software) tier = 4;
  else {
    if (/intel|uhd|iris|hd graphics|vega \d|radeon\(tm\) graphics|adreno|mali/i.test(gpu)) tier = 2;
    if (cores <= 4 || mem <= 4) tier = Math.max(tier, 3);
    if (pixels > 5e6) tier = Math.min(4, tier + 1);   // ~ beyond 2560×1440 physical
  }
  return { tier, gpu: gpu || (software ? 'rendu logiciel' : 'inconnu'), software };
}

export function createGovernor({ initial, locked = null, onChange }) {
  const g = {
    tier: locked ?? initial,
    locked,                 // null = automatic, otherwise a forced tier
    ceiling: 0,             // the best tier allowed to be tried this session
    fps: 0,
    get Q() { return TIERS[g.tier]; },
  };
  let frames = 0, windowStart = performance.now(), lastFrame = windowStart;
  let stalled = false, bad = 0, goodMs = 0, upCooldownUntil = 0, lastUpAt = 0;

  function set(tier, why) {
    tier = Math.max(0, Math.min(TIERS.length - 1, tier));
    if (tier === g.tier) return;
    g.tier = tier;
    onChange && onChange(TIERS[tier], tier, why);
  }

  /** Called once per DRAWN frame. */
  g.frame = (now) => {
    if (now - lastFrame > 300) stalled = true;   // drag, minimise, GC pause…
    lastFrame = now;
    frames += 1;
    const span = now - windowStart;
    if (span < WINDOW_MS) return;

    g.fps = (frames * 1000) / span;
    const valid = !stalled && !document.hidden;
    frames = 0; windowStart = now; stalled = false;
    if (!valid || g.locked !== null) return;

    const target = TIERS[g.tier].fps;
    if (g.fps < target * BAD_RATIO) {
      goodMs = 0;
      bad += 1;
      if (bad >= 2 && g.tier < TIERS.length - 1) {
        // stepping down right after a step up means that tier is too much here
        if (now - lastUpAt < 10000) g.ceiling = Math.max(g.ceiling, g.tier);
        bad = 0;
        upCooldownUntil = now + UP_COOLDOWN_MS;
        set(g.tier + 1, `${g.fps.toFixed(0)} FPS < ${target}`);
      }
    } else {
      bad = 0;
      if (g.fps >= target * GOOD_RATIO) goodMs += span; else goodMs = 0;
      if (goodMs >= UP_AFTER_MS && now > upCooldownUntil && g.tier - 1 >= g.ceiling) {
        goodMs = 0;
        lastUpAt = now;
        set(g.tier - 1, `${g.fps.toFixed(0)} FPS stables`);
      }
    }
  };

  /** Jump straight to a tier (the one learned last time). */
  g.force = (tier, why) => set(tier, why);

  /** A tier picked by hand, or null to hand control back to the measurements. */
  g.lock = (tier) => {
    g.locked = tier;
    bad = 0; goodMs = 0; g.ceiling = 0;
    if (tier !== null) set(tier, 'manuel');
  };

  /** Frame cap: true when this rAF should be skipped.
   *
   *  An accumulator, not "time since last draw": on a 60 Hz display a 45 FPS
   *  cap measured the naive way skips every other frame and lands on 30 —
   *  and the governor would then read its own cap as a slow PC. Carrying the
   *  remainder draws three frames in four, i.e. a real 45. */
  let acc = 0, prev = null;
  g.skip = (now) => {
    const fps = TIERS[g.tier].fps;
    if (fps >= 58 || prev === null) { prev = now; acc = 0; return false; }
    acc += now - prev;
    prev = now;
    const interval = 1000 / fps;
    if (acc < interval - 1) return true;
    acc = Math.min(acc - interval, interval);
    return false;
  };

  return g;
}
