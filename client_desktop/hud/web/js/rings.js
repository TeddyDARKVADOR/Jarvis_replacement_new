/**
 * rings.js — the instrument around the core, in 2D canvas.
 *
 * The orb is the "who"; the rings are the "what": every ring reads one real
 * quantity, so the decoration is also a gauge.
 *
 *   graduated bezel     slow rotation = alive; speed = the state's `spin`
 *   radial spectrum     96 bars driven by the live level (mic or voice)
 *   MIC / VOX arcs      left gauge = your microphone, right gauge = his voice
 *   segment crown       12 segments that chase while connecting / thinking
 *   scan sweep          a radar wedge while the link is being established
 *   shockwave           the wake word was heard
 *   countdown arc       a confirmation is waiting, and how long it has left
 *
 * When WebGL is unavailable this file also draws a flat core, so the HUD
 * still has a centre.
 */
import { rgba } from './states.js';

const TAU = Math.PI * 2;

export function createRings(canvas, V) {
  const g = canvas.getContext('2d');
  let W = 0, H = 0, dpr = 1;
  const bars = new Float32Array(96);
  let rot = 0, crown = 0, sweep = 0;
  let pxCap = 2, cheap = false, glowN = 0;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, pxCap);
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
  }
  new ResizeObserver(resize).observe(canvas);
  resize();

  // Glow, two ways. 'blur' is canvas shadowBlur — a real gaussian per stroke,
  // gorgeous and the single most expensive thing on this page. 'cheap' draws
  // the same halo as a wider, faint stroke under the line: no blur pass, and
  // at this size the eye cannot tell them apart.
  function setGlow(n) {
    glowN = n;
    g.shadowBlur = cheap ? 0 : n;
  }
  function stroke(path) {
    if (cheap && glowN > 0) {
      const w = g.lineWidth;
      g.globalAlpha = .22;
      g.lineWidth = w + glowN * .9;
      path ? g.stroke(path) : g.stroke();
      g.globalAlpha = 1;
      g.lineWidth = w;
    }
    path ? g.stroke(path) : g.stroke();
  }

  // Strokes grouped by opacity. Drawing 96 bars as 96 strokes is 96 draw
  // calls (and 96 blur passes); grouping them by alpha, quantised to steps
  // the eye cannot separate, makes it a handful. Same pixels, far less work.
  const buckets = new Map();
  function bucket(alpha) {
    const k = Math.round(Math.min(1, Math.max(0, alpha)) * 20);
    let path = buckets.get(k);
    if (!path) { path = new Path2D(); buckets.set(k, path); }
    return path;
  }
  function flush(c, width) {
    g.lineWidth = width;
    for (const [k, path] of buckets) { g.strokeStyle = rgba(c, k / 20); stroke(path); }
    buckets.clear();
  }

  // The graduated bezel never changes shape — only its angle and colour. It
  // is drawn once into an offscreen canvas and then just rotated: 120 ticks
  // and 12 labels become one drawImage. Redrawn when the size changes or the
  // colour has moved by more than a few levels (state transitions).
  let bezel = null, bezelKey = '', bezelHalf = 0;
  function bezelFor(R, c, mini) {
    const key = `${R | 0}|${c.map((v) => v >> 3).join(',')}|${mini}|${dpr}`;
    if (key === bezelKey) return bezel;
    bezelKey = key;
    bezelHalf = Math.ceil(R * 1.08 + 16);
    bezel = bezel || document.createElement('canvas');
    bezel.width = bezel.height = Math.ceil(bezelHalf * 2 * dpr);
    const b = bezel.getContext('2d');
    b.setTransform(dpr, 0, 0, dpr, bezelHalf * dpr, bezelHalf * dpr);
    b.clearRect(-bezelHalf, -bezelHalf, bezelHalf * 2, bezelHalf * 2);
    b.lineCap = 'round';
    const ticks = mini ? 60 : 120;
    for (const long of [false, true]) {
      b.beginPath();
      for (let i = 0; i < ticks; i++) {
        if ((i % 10 === 0) !== long) continue;
        const a = (i / ticks) * TAU;
        const r1 = R * .985, r2 = R * (long ? .945 : .965);
        b.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        b.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
      }
      b.lineWidth = long ? 1.6 : .8;
      b.strokeStyle = rgba(c, long ? .55 : .22);
      b.stroke();
    }
    if (!mini) {
      b.font = `${Math.max(8, R * .026)}px Cascadia Mono, Consolas, monospace`;
      b.fillStyle = rgba(c, .45);
      b.textAlign = 'center'; b.textBaseline = 'middle';
      for (let i = 0; i < 12; i++) {
        b.save(); b.rotate((i / 12) * TAU); b.translate(0, -R * 1.025);
        b.fillText(String(i * 30).padStart(3, '0'), 0, 0); b.restore();
      }
    }
    return bezel;
  }

  function arc(cx, cy, r, a0, a1, width, style) {
    g.beginPath(); g.arc(cx, cy, r, a0, a1);
    g.lineWidth = width; g.strokeStyle = style; stroke();
  }

  function draw(t, dt) {
    if (!W || !H) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2;
    const R = Math.min(W, H) * .46;
    const c = V.color, P = V.physics, lvl = V.level;
    const mini = V.mini;
    rot += dt * (.05 + P.spin * .08);
    crown += dt * (P.spin * 1.4);
    sweep += dt * 2.2;

    g.lineCap = 'round';
    g.shadowColor = rgba(c, .8);

    // ── fallback flat core (no WebGL) ──
    if (V.no3d) {
      const r0 = R * (.24 + lvl * .06 + V.burst * .03);
      const grd = g.createRadialGradient(cx, cy, 0, cx, cy, r0 * 2.2);
      grd.addColorStop(0, 'rgba(255,255,255,.95)');
      grd.addColorStop(.18, rgba(c, .9));
      grd.addColorStop(.5, rgba(c, .25));
      grd.addColorStop(1, rgba(c, 0));
      g.fillStyle = grd;
      g.beginPath(); g.arc(cx, cy, r0 * 2.2, 0, TAU); g.fill();
    }

    // ── graduated bezel ──
    setGlow(0);
    const bz = bezelFor(R, c, mini);
    g.save();
    g.translate(cx, cy); g.rotate(rot);
    g.drawImage(bz, -bezelHalf, -bezelHalf, bezelHalf * 2, bezelHalf * 2);
    g.restore();

    // thin outer + inner reference circles
    arc(cx, cy, R * 1.06, 0, TAU, 1, rgba(c, .08));
    arc(cx, cy, R * .9, 0, TAU, .7, rgba(c, .12));

    // ── rotating dashed arcs ──
    setGlow(8);
    for (let k = 0; k < 3; k++) {
      const a = -rot * 2.2 + k * (TAU / 3);
      arc(cx, cy, R * .9, a, a + .55, 2.2, rgba(c, .75));
    }
    for (let k = 0; k < 4; k++) {
      const a = rot * 3.1 + k * (TAU / 4);
      arc(cx, cy, R * 1.06, a, a + .22, 1.6, rgba(c, .5));
    }

    // ── segment crown ──
    const chasing = ['CONNECTING', 'RECONNECTING', 'THINKING'].includes(V.state);
    const segs = 24;
    for (let i = 0; i < segs; i++) {
      const a0 = (i / segs) * TAU + .02, a1 = ((i + 1) / segs) * TAU - .02;
      let on = .12;
      if (chasing) {
        const head = (crown % segs + segs) % segs;
        const d = (head - i + segs) % segs;
        on = d < 6 ? .95 - d * .14 : .1;
      } else {
        on = .14 + lvl * .7 * (.5 + .5 * Math.sin(i * 1.7 + t * 3));
      }
      const path = bucket(on);
      path.moveTo(cx + Math.cos(a0) * R * .82, cy + Math.sin(a0) * R * .82);
      path.arc(cx, cy, R * .82, a0, a1);
    }
    flush(c, R * .022);

    // ── radial spectrum ──
    const n = bars.length;
    const base = R * .64;
    setGlow(6);
    for (let i = 0; i < n; i++) {
      // each bar = live level shaped by a slow travelling field, so it moves
      // like a spectrum but its height is the real loudness
      const field = .5 + .5 * Math.sin(i * .37 + t * 3.2) * Math.cos(i * .11 - t * 1.3);
      const target = lvl * (.25 + .75 * field) + .03 + V.burst * .4;
      bars[i] += (target - bars[i]) * Math.min(1, dt * 14);
      const a = (i / n) * TAU - Math.PI / 2;
      const len = R * .14 * bars[i];
      const r1 = base, r2 = base + Math.max(1.5, len);
      const path = bucket(.25 + bars[i] * .9);
      path.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      path.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
    }
    flush(c, Math.max(1.2, R * .008));

    // ── MIC / VOX gauges ──
    if (!mini) {
      const gr = R * .74, span = Math.PI * .55;
      const micC = [110, 231, 183];
      // left: mic, fills bottom → top
      arc(cx, cy, gr, Math.PI - span / 2, Math.PI + span / 2, 3, rgba(micC, .1));
      arc(cx, cy, gr, Math.PI + span / 2 - span * V.micShown, Math.PI + span / 2, 3, rgba(micC, .9));
      // right: voice
      arc(cx, cy, gr, -span / 2, span / 2, 3, rgba(c, .1));
      arc(cx, cy, gr, span / 2 - span * V.voiceShown, span / 2, 3, rgba(c, .9));
      setGlow(0);
      g.font = `${Math.max(9, R * .028)}px Bahnschrift, Segoe UI, sans-serif`;
      g.textAlign = 'center';
      g.fillStyle = rgba(micC, .7);
      g.fillText('MIC', cx - gr - R * .07, cy + 4);
      g.fillStyle = rgba(c, .7);
      g.fillText('VOX', cx + gr + R * .07, cy + 4);
    }

    // ── scan sweep while the link is being made ──
    if (chasing && V.state !== 'THINKING') {
      const a = sweep % TAU;
      const grd = g.createConicGradient ? g.createConicGradient(a - .9, cx, cy) : null;
      if (grd) {
        grd.addColorStop(0, rgba(c, 0));
        grd.addColorStop(.14, rgba(c, .16));
        grd.addColorStop(.145, rgba(c, 0));
        g.fillStyle = grd;
        g.beginPath(); g.arc(cx, cy, R * .98, 0, TAU); g.fill();
      }
    }

    // ── wake shockwave ──
    if (V.wakeAge !== null && V.wakeAge < 1.6) {
      for (let k = 0; k < 3; k++) {
        const p = (V.wakeAge - k * .18) / 1.2;
        if (p <= 0 || p >= 1) continue;
        const e = 1 - Math.pow(1 - p, 3);
        setGlow(20);
        arc(cx, cy, R * (.3 + e * .85), 0, TAU, 3 * (1 - p), rgba([110, 231, 183], (1 - p) * .9));
      }
    }

    // ── confirmation countdown ──
    if (V.confirm) {
      const amber = [251, 191, 107];
      const frac = V.confirm.total ? Math.max(0, V.confirm.remaining / V.confirm.total) : 1;
      g.shadowColor = rgba(amber, .9); setGlow(14);
      arc(cx, cy, R * 1.12, -Math.PI / 2, -Math.PI / 2 + TAU * frac, 3, rgba(amber, .4 + .5 * Math.abs(Math.sin(t * 3))));
    }

    // ── crosshair hairlines ──
    if (!mini) {
      setGlow(0);
      g.strokeStyle = rgba(c, .1); g.lineWidth = 1;
      g.beginPath();
      g.moveTo(cx - R * 1.3, cy); g.lineTo(cx - R * 1.1, cy);
      g.moveTo(cx + R * 1.1, cy); g.lineTo(cx + R * 1.3, cy);
      g.moveTo(cx, cy - R * 1.2); g.lineTo(cx, cy - R * 1.12);
      stroke();
    }
  }

  function setQuality(q) {
    cheap = q.glow === 'cheap';
    if (q.px2d !== pxCap) { pxCap = q.px2d; resize(); }
  }

  return { draw, setQuality };
}
