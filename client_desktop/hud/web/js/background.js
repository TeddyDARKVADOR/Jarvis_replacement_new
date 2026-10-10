/**
 * background.js — the room the hologram lives in.
 *
 * A perspective floor grid receding to a horizon, a field of slow motes, and
 * the occasional data streak. Drawn at ~30 fps: nothing here moves fast
 * enough to need 60, and the background is the one layer always on screen.
 */
import { rgba } from './states.js';

export function createBackground(canvas, V) {
  const g = canvas.getContext('2d');
  let W = 0, H = 0, dpr = 1;
  const motes = [];
  const streaks = [];
  let pxCap = 1.5, bgFps = 30;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, pxCap);
    W = innerWidth; H = innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    motes.length = 0;
    const n = Math.round((W * H) / 16000);
    for (let i = 0; i < n; i++) {
      motes.push({ x: Math.random() * W, y: Math.random() * H, z: Math.random(),
        vx: (Math.random() - .5) * 6, vy: -2 - Math.random() * 8 });
    }
  }
  addEventListener('resize', resize);
  resize();

  let acc = 0, scroll = 0;
  function draw(t, dt) {
    acc += dt;
    if (acc < 1 / bgFps) return;
    const step = acc; acc = 0;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const c = V.color;
    if (V.mini) return;

    // ── floor grid in perspective ──
    const horizon = H * .62;
    scroll = (scroll + step * (8 + V.physics.spin * 10)) % 40;
    g.lineWidth = 1;
    for (let i = 0; i < 18; i++) {
      const z = ((i * 40 + scroll) / (18 * 40));
      const y = horizon + Math.pow(z, 2.2) * (H - horizon) * 1.05;
      g.strokeStyle = rgba(c, .025 + z * .07);
      g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
    }
    const vpX = W / 2;
    for (let i = -14; i <= 14; i++) {
      const xb = vpX + i * (W / 14);
      g.strokeStyle = rgba(c, .045 - Math.abs(i) * .0022);
      g.beginPath(); g.moveTo(vpX + i * 6, horizon); g.lineTo(xb, H); g.stroke();
    }
    // horizon glow
    const hg = g.createLinearGradient(0, horizon - 40, 0, horizon + 40);
    hg.addColorStop(0, rgba(c, 0)); hg.addColorStop(.5, rgba(c, .05)); hg.addColorStop(1, rgba(c, 0));
    g.fillStyle = hg; g.fillRect(0, horizon - 40, W, 80);

    // ── motes ──
    for (const m of motes) {
      m.x += m.vx * step * (1 + V.level);
      m.y += m.vy * step * (1 + V.level * 2);
      if (m.y < -10) { m.y = H + 10; m.x = Math.random() * W; }
      if (m.x < -10) m.x = W + 10; else if (m.x > W + 10) m.x = -10;
      const a = (.12 + m.z * .35) * (.6 + .4 * Math.sin(t * 2 + m.x));
      g.fillStyle = rgba(c, a);
      const s = .6 + m.z * 1.6;
      g.fillRect(m.x, m.y, s, s);
    }

    // ── data streaks ──
    if (Math.random() < step * .6) {
      streaks.push({ y: Math.random() * H * .6, x: -200, v: 600 + Math.random() * 900, w: 60 + Math.random() * 180 });
    }
    for (let i = streaks.length - 1; i >= 0; i--) {
      const s = streaks[i];
      s.x += s.v * step;
      if (s.x > W + 200) { streaks.splice(i, 1); continue; }
      const sg = g.createLinearGradient(s.x - s.w, 0, s.x, 0);
      sg.addColorStop(0, rgba(c, 0)); sg.addColorStop(1, rgba(c, .22));
      g.fillStyle = sg; g.fillRect(s.x - s.w, s.y, s.w, 1);
    }
  }
  function setQuality(q) {
    bgFps = q.bgFps;
    const cap = Math.min(1.5, q.px2d);
    if (cap !== pxCap) { pxCap = cap; resize(); }
  }
  return { draw, setQuality };
}
