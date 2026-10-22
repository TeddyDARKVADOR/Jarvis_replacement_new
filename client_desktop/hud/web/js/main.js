/**
 * main.js — the conductor.
 *
 * Python pushes:   HUD.init(static) · HUD.tick(snapshot) · HUD.metrics(machine)
 *                  HUD.setMini(bool) · HUD.showGraph(graphs)
 * The page asks:   bridge.send / mic / interrupt / confirm / settings / ...
 *
 * `V` is the visual state every renderer reads once per frame. It is derived
 * from the last snapshot plus smoothing — never invented: an orb that pulses
 * "listening" while the microphone is shut is a lie users feel immediately.
 */
import { STATES, hintFor, colorOf, hexToRgb, mix, rgba } from './states.js';
import { createRings } from './rings.js';
import { createBackground } from './background.js';
import { sound } from './sound.js';
import { TIERS, guessTier, createGovernor } from './perf.js';
import { createSettings } from './settings.js';
import { createStudio } from './studio.js';
import { createMind } from './mind.js';
import { createGraph } from './graph.js';
import { pollBrain } from './matthew.js';

const SW = [['#7DD3FC', 'STARK'], ['#38BDF8', 'ARC'], ['#A78BFA', 'VIBRANIUM'], ['#6EE7B7', 'EMERAUDE'],
  ['#FBBF24', 'MARK'], ['#FB923C', 'ULTRON'], ['#F43F5E', 'HOT ROD'], ['#E2E8F0', 'IVOIRE']];
const LINK_LABELS = { OFFLINE: 'HORS LIGNE', CONNECTING: 'CONNEXION', CONNECTED: 'CONNECTÉ', RECONNECTING: 'RECONNEXION', ERROR: 'ERREUR' };
const $ = (id) => document.getElementById(id);
const setText = (el, v) => { if (el && el.textContent !== v) el.textContent = v; };

// ── visual state ──────────────────────────────────────────────────────────
const V = {
  state: 'CONNECTING', physics: STATES.CONNECTING,
  color: hexToRgb('#FBBF6B'), target: hexToRgb('#FBBF6B'), accent: hexToRgb('#7DD3FC'),
  level: 0, micShown: 0, voiceShown: 0, burst: 0, wakeAge: null,
  confirm: null, mini: false, no3d: false,
};
let S = null;                 // last snapshot
let accentHex = '#7DD3FC';
let prefs = { accent: '#7DD3FC', sound: true, volume: .35, quality: 'auto', quality_tier: null, clipboard: true };
let init = { server: {}, identity: {} };
let bridge = null;
let reactor = null;
let animated = true;

// ── bridge ────────────────────────────────────────────────────────────────
function connect() {
  return new Promise((resolve) => {
    if (window.qt && window.qt.webChannelTransport && window.QWebChannel) {
      new window.QWebChannel(window.qt.webChannelTransport, (ch) => resolve(ch.objects.hud));
    } else {
      import('./sim.js').then(({ createSim }) => resolve(createSim(window.HUD)));
    }
  });
}
const call = (name, ...args) => { try { bridge && bridge[name] && bridge[name](...args); } catch { /* bridge gone */ } };

// A request to the assistant's backend (memory, profile, plugins…), answered
// later by HUD.rpcResult. Oracle or this PC — the page does not know which.
const pending = new Map();
let rpcSeq = 0;
function rpc(op, data = {}) {
  return new Promise((resolve) => {
    const id = `r${++rpcSeq}`;
    pending.set(id, resolve);
    setTimeout(() => { if (pending.delete(id)) resolve({ error: 'pas de réponse' }); }, 30000);
    call('rpc', id, op, JSON.stringify(data));
  });
}

// ── formatting ────────────────────────────────────────────────────────────
function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['o', 'Ko', 'Mo', 'Go', 'To'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toLocaleString('fr-FR', { maximumFractionDigits: n < 10 && i ? 1 : 0 })} ${u[i]}`;
}
const fmtRate = (n) => (n == null ? '—' : `${fmtBytes(n)}/s`);
function fmtUptime(s) {
  if (s == null) return '—';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} j ${h} h` : `${h} h ${String(m).padStart(2, '0')}`;
}
const fmtTime = (a) => (a ? new Date(a * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) : '');
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const linkify = (s) => escapeHtml(s).replace(/https?:\/\/[^\s<]+/g, (u) => `<a href="#" data-url="${u}">${u}</a>`);

// ── sparklines ────────────────────────────────────────────────────────────
function spark(canvas, data, max) {
  const dpr = Math.min(devicePixelRatio || 1, gov ? gov.Q.px2d : 1);
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const c = V.accent;
  g.strokeStyle = rgba(c, .08); g.lineWidth = 1;
  for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(0, (h * i) / 4); g.lineTo(w, (h * i) / 4); g.stroke(); }
  if (data.length < 2) return;
  const m = max || Math.max(1, ...data);
  const step = w / (data.length - 1);
  g.beginPath();
  data.forEach((v, i) => { const y = h - (v / m) * (h - 3) - 1; i ? g.lineTo(i * step, y) : g.moveTo(0, y); });
  g.strokeStyle = rgba(c, .9); g.lineWidth = 1.4; g.shadowColor = rgba(c, .8); g.shadowBlur = 6; g.stroke();
  g.shadowBlur = 0;
  g.lineTo(w, h); g.lineTo(0, h); g.closePath();
  const grd = g.createLinearGradient(0, 0, 0, h);
  grd.addColorStop(0, rgba(c, .25)); grd.addColorStop(1, rgba(c, 0));
  g.fillStyle = grd; g.fill();
}

// ── meters ────────────────────────────────────────────────────────────────
function buildMeter(el) { el.innerHTML = '<b></b>'.repeat(24); return [...el.children]; }
const meters = { mic: buildMeter($('m-mic')), voice: buildMeter($('m-voice')) };
const meterLast = { mic: -1, voice: -1 };
function setMeter(name, v) {
  const n = Math.round(Math.min(1, v) * 24);
  if (n === meterLast[name]) return;
  meterLast[name] = n;
  meters[name].forEach((b, i) => {
    b.className = i < n ? (i >= 22 ? 'on peak' : i >= 18 ? 'on hi' : 'on') : '';
  });
}

// ── errors, in words a person can act on ─────────────────────────────────
// The raw text stays in the journal; the toast says what it means and where
// to go. Several reconnect attempts that fail the same way toast once.
function humanError(e) {
  const t = String(e || '');
  if (/401|refus|unauthori[sz]ed|invalid token/i.test(t)) return 'Jeton refusé par le serveur — vérifiez-le dans Paramètres (⚙).';
  if (/10061|refused|NewConnectionError|Max retries|getaddrinfo|Name or service|timed? ?out|unreachable|injoignable/i.test(t))
    return 'Serveur Matthew injoignable — nouvelle tentative automatique. Tailscale est-il connecté ?';
  return t.length > 140 ? `${t.slice(0, 137)}…` : t;
}

// ── toasts ────────────────────────────────────────────────────────────────
function toast(title, text, color = null, ms = 5000) {
  const el = document.createElement('div');
  el.className = 'toast';
  if (color) el.style.setProperty('--c', color);
  el.innerHTML = `<b>${escapeHtml(title)}</b>${escapeHtml(text)}`;
  $('toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 320); }, ms);
}

// ── decipher text (boot) ──────────────────────────────────────────────────
function decipher(el, delay = 0) {
  const final = el.textContent;
  const glyphs = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&@$<>/\\';
  const start = performance.now() + delay;
  const dur = 500 + final.length * 25;
  function step(now) {
    const p = Math.max(0, (now - start) / dur);
    if (p >= 1) { el.textContent = final; return; }
    const shown = Math.floor(p * final.length);
    el.textContent = final.split('').map((ch, i) => (i < shown || ch === ' ' || ch === '.' ? ch
      : glyphs[(Math.random() * glyphs.length) | 0])).join('');
    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ── conversation ──────────────────────────────────────────────────────────
let renderedKeys = [];
const keyOf = (m) => `${m.j ? 1 : 0}|${m.a}|${m.t.length}|${m.t.slice(0, 24)}`;
function messageEl(m, animate) {
  const el = document.createElement('div');
  const card = m.j && m.ti;
  el.className = `msg ${m.j ? 'j' : 'u'}${card ? ' card' : ''}`;
  if (!animate) el.style.animation = 'none';
  const who = m.j ? '<b>MATTHEW</b>' : 'VOUS';
  el.innerHTML = card
    ? `<div class="meta">${who}<span>${fmtTime(m.a)}</span></div><div class="title">${escapeHtml(m.ti)}</div><div class="body">${linkify(m.t)}</div>`
    : `<div class="meta">${who}<span>${fmtTime(m.a)}</span></div><div>${linkify(m.t)}</div>`;
  const copy = document.createElement('button');
  copy.className = 'copy'; copy.textContent = 'COPIER';
  copy.onclick = (e) => { e.stopPropagation(); call('copy', m.t); copy.textContent = 'COPIÉ ✓'; setTimeout(() => { copy.textContent = 'COPIER'; }, 1200); };
  el.appendChild(copy);
  return el;
}
function renderMessages(list) {
  const chat = $('chat');
  const keys = list.map(keyOf);
  const isAppend = renderedKeys.length && renderedKeys.length <= keys.length
    && renderedKeys.every((k, i) => k === keys[i]);
  if (!list.length) {
    chat.innerHTML = `<div class="empty">Aucun échange pour l'instant.<br>Dites <b>« Hey Jarvis »</b>, cliquez sur le noyau,<br>ou tapez une commande ci-dessous.</div>`;
  } else if (isAppend) {
    list.slice(renderedKeys.length).forEach((m) => chat.appendChild(messageEl(m, true)));
  } else {
    chat.innerHTML = '';
    list.forEach((m, i) => chat.appendChild(messageEl(m, i >= list.length - 1 && renderedKeys.length > 0)));
  }
  const initial = !renderedKeys.length;
  const old = new Set(renderedKeys);
  const fresh = isAppend ? list.slice(renderedKeys.length) : list.filter((m) => !old.has(keyOf(m)));
  renderedKeys = keys;
  chat.scrollTop = chat.scrollHeight;
  setText($('chat-count'), list.length ? `${list.length} MSG` : '');
  return { fresh, initial };
}

// ── caption (subtitles) ───────────────────────────────────────────────────
let typeTimer = null;
let clipText = '', clipTimer = null;
function caption(text, who = 'MATTHEW', user = false) {
  const el = $('caption-text');
  setText($('caption-who'), who);
  el.classList.toggle('user', user);
  clearInterval(typeTimer);
  if (user) { el.textContent = text; return; }
  const cps = Math.max(32, Math.min(90, text.length / 3));
  const t0 = performance.now();
  el.innerHTML = '<span></span><span class="cursor"></span>';
  const span = el.firstChild;
  typeTimer = setInterval(() => {
    const i = Math.min(text.length, Math.ceil(((performance.now() - t0) / 1000) * cps));
    span.textContent = text.slice(0, i);
    if (i >= text.length) { clearInterval(typeTimer); setTimeout(() => el.querySelector('.cursor')?.remove(), 1500); }
  }, 33);
}

// ── log ───────────────────────────────────────────────────────────────────
let lastLogLine = '';
function renderLog(lines) {
  const el = $('log');
  el.innerHTML = lines.map((l) => `<div class="${/erreur|échec|refus|sans réponse|error/i.test(l) ? 'err' : ''}">${escapeHtml(l)}</div>`).join('');
  el.scrollTop = el.scrollHeight;
  const newest = lines[lines.length - 1] || '';
  if (newest !== lastLogLine && lastLogLine && /sans réponse/i.test(newest)) {
    toast('COMMANDE PERDUE', 'Matthew n\'a pas répondu — renvoyez-la si elle comptait.', '#FBBF6B', 8000);
  }
  lastLogLine = newest;
}

// ── confirmation ──────────────────────────────────────────────────────────
let confirmTotal = {}, answered = null;
function renderConfirm(c) {
  const box = $('confirm');
  if (!c || c.id === answered) {
    if (!box.hidden) { box.hidden = true; document.body.classList.remove('alarm'); }
    V.confirm = null;
    return;
  }
  if (box.hidden) {
    box.hidden = false;
    document.body.classList.add('alarm');
    setText($('cf-title'), c.title || 'Action irréversible');
    setText($('cf-detail'), c.detail || '');
    sound.play('confirm');
  }
  if (c.remaining != null && !confirmTotal[c.id]) confirmTotal[c.id] = Math.max(c.remaining, 1);
  const total = confirmTotal[c.id];
  V.confirm = { remaining: c.remaining ?? 0, total };
  const frac = total ? c.remaining / total : 1;
  $('cf-arc').style.strokeDashoffset = String(276.5 * (1 - frac));
  setText($('cf-secs'), c.remaining != null ? String(Math.ceil(c.remaining)) : '∞');
}
function answer(ok) {
  if (!S || !S.confirm) return;
  answered = S.confirm.id;
  call('confirm', S.confirm.id, ok);
  sound.play(ok ? 'send' : 'close');
  renderConfirm(null);
}

// ── tick: a snapshot arrived ──────────────────────────────────────────────
let prevState = '', prevLink = '', prevError = null, lastWakeAge = null, booted = false;
function onTick(s) {
  S = Object.assign(S || {}, s);
  const state = S.confirm && S.confirm.id !== answered ? 'CONFIRM' : S.state;

  if (state !== V.state) {
    V.state = state;
    V.physics = STATES[state] || STATES.OFFLINE;
  }
  V.target = hexToRgb(colorOf(state, accentHex));
  V.wakeAge = S.wakeAge;

  // the wake word was heard (rising edge on a fresh wake)
  if (S.wakeAge != null && S.wakeAge < .5 && (lastWakeAge == null || S.wakeAge < lastWakeAge)) {
    V.burst = 1; sound.play('wake');
  }
  lastWakeAge = S.wakeAge;

  // chip + reactor label
  const P = STATES[state] || STATES.OFFLINE;
  document.body.style.setProperty('--sc', colorOf(state, accentHex));
  if (state !== prevState) {
    setText($('status-text'), P.label);
    setText($('state-label'), P.label);
    document.body.dataset.state = state;
    $('stop').hidden = !(state === 'SPEAKING' || state === 'THINKING');
    prevState = state;
  }
  const hint = hintFor(state, { paired: init.server.paired, local: !!init.server.local, retry: S.retry && S.retry.next,
    error: S.error, wake: S.wake && S.wake.name, gate: S.gate });
  setText($('state-hint'), hint);
  let aux = '';
  if (S.link === 'RECONNECTING' && S.retry) aux = `· tentative ${S.retry.attempt} · ${S.retry.next} s`;
  else if (S.link === 'CONNECTED' && init.server.local) aux = '· local';
  else if (S.link === 'CONNECTED' && init.server.host) aux = `· ${init.server.host.split('.')[0]}`;
  setText($('status-aux'), aux);

  // link transitions → toasts
  if (S.link !== prevLink) {
    if (S.link === 'CONNECTED' && prevLink) { toast('LIAISON ÉTABLIE', 'MARK LIII est en ligne.', '#6EE7B7', 3000); sound.play('open'); }
    else if (prevLink === 'CONNECTED' && S.link !== 'CONNECTED') toast('LIAISON PERDUE', 'Reconnexion automatique en cours…', '#FBBF6B', 4000);
    prevLink = S.link;
    setText($('link-text'), LINK_LABELS[S.link] || S.link);
    settings.setLink(LINK_LABELS[S.link] || S.link);
  }
  if (S.error && S.error !== prevError && humanError(S.error) !== humanError(prevError || '')) {
    toast('LIAISON', humanError(S.error), '#F87171', 6000); sound.play('error');
  }
  prevError = S.error;

  // mic button
  const micBtn = $('mic');
  micBtn.classList.toggle('open', !!S.gate);
  micBtn.setAttribute('aria-pressed', S.gate ? 'true' : 'false');
  $('mini-mic').classList.toggle('open', !!S.gate);
  setText($('gate-aux'), S.gate ? (S.micOpen ? 'MICRO OUVERT · FLUX' : 'MICRO OUVERT') : 'MICRO FERMÉ');

  if (s.counters) renderCounters(s.counters);
  if (s.wake) {
    setText($('wake-name'), s.wake.name ? 'ÉVEIL' : 'ÉVEIL OFF');
    $('wake-name').title = s.wake.name ? `« Hey Jarvis » — score de détection (${s.wake.name})` : 'Mot d\'éveil non installé (⚙ pour l\'installer)';
    $('wake-bar').style.width = `${Math.round((s.wake.score || 0) * 100)}%`;
    setText($('wake-val'), s.wake.name ? (s.wake.score || 0).toFixed(2) : '—');
  }
  if (s.messages) {
    const { fresh, initial } = renderMessages(s.messages);
    const lastJ = [...fresh].reverse().find((m) => m.j);
    const lastU = [...fresh].reverse().find((m) => !m.j);
    if (initial) {
      // history restored at start: show where we were, without fanfare
      const j = [...s.messages].reverse().find((m) => m.j);
      if (j) caption(j.t);
    } else if (lastJ) { caption(lastJ.t); V.burst = Math.max(V.burst, .45); sound.play('reply'); }
    else if (lastU) caption(lastU.t, 'VOUS', true);
  }
  if (s.log) renderLog(s.log);
  renderConfirm(S.confirm);
}

// ── counters (5 Hz) ───────────────────────────────────────────────────────
let lastRecv = null;
const linkHist = [];
function renderCounters(c) {
  setText($('c-recv'), fmtBytes(c.received));
  setText($('c-played'), fmtBytes(c.played));
  setText($('c-sent'), fmtBytes(c.sent));
  setText($('c-drop'), String(c.dropped));
  setText($('srv-rate'), c.rate ? `${(c.rate / 1000).toLocaleString('fr-FR')} kHz` : '—');
  $('c-drop').style.color = c.dropped ? '#FBBF6B' : '';
  V._recv = c.received;
}

// ── metrics (1 Hz) ────────────────────────────────────────────────────────
const cpuHist = [];
function onMetrics(m) {
  if (!m.available) { setText($('cpu-val'), 'N/D'); return; }
  setText($('cpu-val'), m.cpu != null ? String(Math.round(m.cpu)) : '--');
  cpuHist.push(m.cpu || 0); if (cpuHist.length > 60) cpuHist.shift();
  spark($('cpu-spark'), cpuHist, 100);
  const bar = (id, v) => { const el = $(id); el.style.width = `${v || 0}%`; el.classList.toggle('hot', v > 88); };
  bar('ram-bar', m.ram); setText($('ram-val'), m.ram != null ? `${Math.round(m.ram)}%` : '--');
  bar('disk-bar', m.disk); setText($('disk-val'), m.disk != null ? `${Math.round(m.disk)}%` : '--');
  setText($('net-down'), fmtRate(m.net_down));
  setText($('net-up'), fmtRate(m.net_up));
  setText($('uptime'), fmtUptime(m.uptime));
  if (m.battery != null) {
    $('batt-wrap').hidden = false;
    setText($('battery'), `${Math.round(m.battery)}%${m.plugged ? ' ⚡' : ''}`);
  }
  // voice link throughput, from the received counter
  if (V._recv != null) {
    linkHist.push(lastRecv == null ? 0 : Math.max(0, V._recv - lastRecv));
    if (linkHist.length > 60) linkHist.shift();
    lastRecv = V._recv;
    spark($('link-spark'), linkHist);
  }
}

// ── init (static facts + prefs) ───────────────────────────────────────────
function onInit(data) {
  init = { ...init, ...data };
  prefs = { ...prefs, ...(data.prefs || {}) };
  applyAccent(prefs.accent, false);
  sound.set(prefs.sound, prefs.volume);
  $('btn-sound').classList.toggle('muted', !prefs.sound);
  const id = data.identity || {};
  setText($('hostname'), (id.hostname || 'TERMINAL').toUpperCase());
  setText($('sys-host'), id.os || '');
  setServer(data.server || {});
  if (data.mini) setMini(true);
  if (typeof prefs.quality === 'number') gov.lock(prefs.quality);
  else if (typeof prefs.quality_tier === 'number') gov.force(prefs.quality_tier, 'mémorisé');
  refreshQualityButtons();
  startReactor();
  if (!booted) boot();
}

function setServer(sv) {
  init.server = sv;
  if (sv.local) {
    // the brain runs on this PC: say so, everywhere the server would be named
    setText($('srv-host'), 'ce PC · Gemini Live');
    $('srv-host').title = 'python main.py --local';
    setText($('srv-device'), 'mode local');
    setText(document.querySelector('#p-link h2 .aux'), 'LOCAL');
    $('set-local').hidden = false;
    return;
  }
  setText($('srv-host'), sv.host ? `${sv.host.split('.')[0]}:${sv.port}${sv.tls ? ' · TLS' : ''}` : 'non appairé');
  $('srv-host').title = sv.host || '';
  setText($('srv-device'), sv.device || '—');
}

function savePrefs() { call('savePrefs', JSON.stringify(prefs)); }

function applyAccent(hex, save = true) {
  if (!/^#[0-9a-f]{6}$/i.test(hex || '')) return;
  accentHex = hex;
  prefs.accent = hex;
  V.accent = hexToRgb(hex);
  document.documentElement.style.setProperty('--accent', hex);
  document.querySelectorAll('.swatches button').forEach((b) => b.classList.toggle('sel', b.dataset.hex.toLowerCase() === hex.toLowerCase()));
  $('accent-input').value = hex;
  if (save) savePrefs();
}

// ── mini ──────────────────────────────────────────────────────────────────
function setMini(on) {
  V.mini = on;
  document.body.classList.toggle('mini', on);
}

// ── mic / commands ────────────────────────────────────────────────────────
function toggleMic() { const open = !(S && S.gate); call('mic', open); sound.play(open ? 'open' : 'close'); }
const history = [];
let histIdx = -1;
function send(text) {
  text = (text || '').trim();
  if (!text) return;
  call('send', text);
  history.push(text); histIdx = -1;
  $('cmd').value = '';
  caption(text, 'VOUS', true);
  sound.play('send');
  showChips(false);
}

const CHIPS = [
  'Quelle est la météo aujourd\'hui ?',
  'Résume-moi l\'actualité du jour',
  'Rappelle-moi dans 10 minutes de…',
  'Que sais-tu de moi ?',
  'Ouvre YouTube',
  'Annule la dernière action',
];
function buildChips() {
  const box = $('chips');
  CHIPS.forEach((c) => {
    const b = document.createElement('button');
    b.textContent = c;
    b.onmousedown = (e) => e.preventDefault();   // keep focus in the input
    b.onclick = () => {
      if (c.endsWith('…')) { const i = $('cmd'); i.value = c.slice(0, -1); i.focus(); }
      else send(c);
    };
    box.appendChild(b);
  });
}
function showChips(on) { $('chips').classList.toggle('show', on); }

// ── wiring ────────────────────────────────────────────────────────────────
function wire() {
  const cmd = $('cmd');
  cmd.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); send(cmd.value); }
    else if (e.key === 'ArrowUp' && history.length) {
      e.preventDefault();
      histIdx = histIdx < 0 ? history.length - 1 : Math.max(0, histIdx - 1);
      cmd.value = history[histIdx];
    } else if (e.key === 'ArrowDown' && histIdx >= 0) {
      e.preventDefault();
      histIdx += 1;
      if (histIdx >= history.length) { histIdx = -1; cmd.value = ''; } else cmd.value = history[histIdx];
    }
  });
  cmd.addEventListener('focus', () => showChips(!cmd.value));
  cmd.addEventListener('input', () => showChips(!cmd.value));
  cmd.addEventListener('blur', () => setTimeout(() => showChips(false), 150));

  $('send').onclick = () => send(cmd.value);
  $('attach').onclick = () => call('attachFile');
  $('file-clear').onclick = () => { call('clearFile'); $('file-chip').hidden = true; };
  $('clip-close').onclick = () => { $('clip').hidden = true; };
  document.querySelectorAll('#clip .clip-actions button').forEach((b) => {
    b.onclick = () => { send(b.dataset.cmd.replace('{t}', clipText)); $('clip').hidden = true; };
  });
  $('stop').onclick = () => { call('interrupt'); sound.play('close'); };
  $('mic').onclick = toggleMic;
  $('mini-mic').onclick = (e) => { e.stopPropagation(); toggleMic(); };
  $('mini-expand').onclick = (e) => { e.stopPropagation(); call('setMini', false); };

  $('reactor').addEventListener('click', () => {
    if (V.mini) return;
    if (V.state === 'SPEAKING') { call('interrupt'); sound.play('close'); } else toggleMic();
  });

  $('btn-settings').onclick = () => settings.open('connexion');
  $('btn-help').onclick = () => toggleOverlay('help');
  $('btn-mini').onclick = () => call('setMini', true);
  $('btn-min').onclick = () => call('minimize');
  $('btn-max').onclick = () => call('toggleMaximize');
  $('btn-close').onclick = () => call('hideToTray');
  $('btn-sound').onclick = () => {
    prefs.sound = !prefs.sound; sound.set(prefs.sound, prefs.volume);
    $('btn-sound').classList.toggle('muted', !prefs.sound); savePrefs(); sound.play('click');
  };
  $('btn-palette').onclick = (e) => { e.stopPropagation(); toggleOverlay('palette'); };
  $('cf-yes').onclick = () => answer(true);
  $('cf-no').onclick = () => answer(false);
  $('log-toggle').onclick = () => {
    const p = $('p-log'); p.classList.toggle('closed');
    $('log-toggle').textContent = p.classList.contains('closed') ? '▸' : '▾';
  };
  document.querySelectorAll('[data-close]').forEach((b) => { b.onclick = () => { b.closest('.overlay').hidden = true; }; });
  $('help').addEventListener('click', (e) => { if (e.target.id === 'help') $('help').hidden = true; });
  document.addEventListener('click', (e) => { if (!$('palette').hidden && !e.target.closest('#palette')) $('palette').hidden = true; });
  $('chat').addEventListener('click', (e) => {
    const a = e.target.closest('a[data-url]');
    if (a) { e.preventDefault(); call('openUrl', a.dataset.url); }
  });

  // swatches
  const box = document.querySelector('#palette .swatches');
  SW.forEach(([hex, name]) => {
    const b = document.createElement('button');
    b.dataset.hex = hex; b.style.setProperty('--c', hex); b.title = name;
    b.innerHTML = `<span>${name}</span>`;
    b.onclick = () => { applyAccent(hex); sound.play('click'); };
    box.appendChild(b);
  });
  $('accent-input').addEventListener('input', (e) => applyAccent(e.target.value));

  // window: drag the header, double-click to maximise; resize from edges
  $('top').addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.target.closest('button, .chip')) return;
    call('startMove');
  });
  $('top').addEventListener('dblclick', (e) => { if (!e.target.closest('button')) call('toggleMaximize'); });
  document.querySelectorAll('.edge').forEach((el) => el.addEventListener('mousedown', (e) => {
    if (e.button === 0) { e.preventDefault(); call('startResize', el.dataset.e); }
  }));
  // mini: drag anywhere, double-click to open
  document.body.addEventListener('mousedown', (e) => {
    if (V.mini && e.button === 0 && !e.target.closest('button')) call('startMove');
  });
  document.body.addEventListener('dblclick', (e) => { if (V.mini && !e.target.closest('button')) call('setMini', false); });

  // keyboard
  document.addEventListener('keydown', (e) => {
    const inInput = !!(e.target.closest && e.target.closest('input, select, textarea'));
    if (e.ctrlKey && e.code === 'Space') { e.preventDefault(); toggleMic(); return; }
    if (e.key === 'Escape') {
      if (!$('confirm').hidden) { answer(false); return; }
      if (settings.isOpen) { settings.close(); return; }
      if (studio.isOpen) { studio.viewerOpen ? studio.closeViewer() : studio.close(); return; }
      if (mind.isOpen) { mind.close(); return; }
      if (graph.isOpen) { graph.close(); return; }
      if (!$('help').hidden) { $('help').hidden = true; return; }
      if (!$('palette').hidden) { $('palette').hidden = true; return; }
      if (V.state === 'SPEAKING' || V.state === 'THINKING') { call('interrupt'); sound.play('close'); return; }
      if (inInput) cmd.blur();
      return;
    }
    if (e.key === 'F11') { e.preventDefault(); call('fullscreen'); return; }
    if (e.key === 'F1') { e.preventDefault(); toggleOverlay('help'); return; }
    if (e.ctrlKey && e.shiftKey && (e.key === 'F' || e.key === 'f')) {
      e.preventDefault(); perfShown = !perfShown; $('perf').hidden = !perfShown; return;
    }
    if (e.ctrlKey && (e.key === 'm' || e.key === 'M')) { e.preventDefault(); call('setMini', !V.mini); return; }
    if (e.ctrlKey && e.key === ',') { e.preventDefault(); settings.isOpen ? settings.close() : settings.open('connexion'); return; }
    if (e.ctrlKey && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); cmd.focus(); return; }
    if (!inInput && (!$('confirm').hidden || settings.isOpen || studio.isOpen || mind.isOpen || graph.isOpen)) return;
    if (!inInput && e.key === '/') { e.preventDefault(); cmd.focus(); return; }
    // typing anywhere goes to the command line
    if (!inInput && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey && !V.mini) cmd.focus();
  });
}

function toggleOverlay(id) {
  const el = $(id);
  el.hidden = !el.hidden;
  sound.play(el.hidden ? 'close' : 'open');
}

// ── clock ─────────────────────────────────────────────────────────────────
function clock() {
  const d = new Date();
  setText($('clock-time'), d.toLocaleTimeString('fr-FR'));
  setText($('clock-date'), d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }));
}

// ── boot ──────────────────────────────────────────────────────────────────
function boot() {
  booted = true;
  sound.play('boot');
  const parts = [...document.querySelectorAll('.assemble')];
  parts.forEach((el, i) => setTimeout(() => el.classList.add('on'), 120 + i * 110));
  document.querySelectorAll('[data-decipher]').forEach((el, i) => decipher(el, 200 + i * 120));
  setTimeout(() => document.body.classList.remove('booting'), 250);
}

// ── frame loop: 2D layers + smoothing ─────────────────────────────────────
const bg = createBackground($('bg'), V);
const rings = createRings($('rings'), V);
const scopeCanvas = $('scope');
const scopeG = scopeCanvas.getContext('2d');
function drawScope(t) {
  const dpr = Math.min(devicePixelRatio || 1, gov.Q.px2d);
  const cheap = gov.Q.glow === 'cheap';
  const w = scopeCanvas.clientWidth, h = scopeCanvas.clientHeight;
  if (!w || !h) return;
  if (scopeCanvas.width !== w * dpr) { scopeCanvas.width = w * dpr; scopeCanvas.height = h * dpr; }
  scopeG.setTransform(dpr, 0, 0, dpr, 0, 0);
  scopeG.clearRect(0, 0, w, h);
  const c = V.color, mid = h / 2, amp = (h / 2 - 3) * Math.min(1, V.level * 1.3 + .04);
  for (let layer = 0; layer < 3; layer++) {
    scopeG.beginPath();
    for (let x = 0; x <= w; x += 3) {
      const u = x / w;
      const env = Math.sin(Math.PI * u);                         // tapered ends
      const y = mid + env * amp * (Math.sin(u * 22 + t * (6 + layer * 2.3) + layer) * .6
        + Math.sin(u * 51 - t * 9.1 + layer * 2) * .3 + Math.sin(u * 7 + t * 2) * .25) * (1 - layer * .25);
      x ? scopeG.lineTo(x, y) : scopeG.moveTo(x, y);
    }
    scopeG.strokeStyle = rgba(c, layer ? .25 : .85);
    scopeG.lineWidth = layer ? 1 : 1.6;
    if (cheap) {
      // same halo, without a blur pass: a wide faint stroke underneath
      if (!layer) { scopeG.globalAlpha = .2; scopeG.lineWidth = 7; scopeG.stroke(); scopeG.globalAlpha = 1; scopeG.lineWidth = 1.6; }
    } else { scopeG.shadowColor = rgba(c, .9); scopeG.shadowBlur = layer ? 0 : 8; }
    scopeG.stroke();
  }
  scopeG.shadowBlur = 0;
  scopeG.fillStyle = rgba(c, .12);
  scopeG.fillRect(0, mid, w, 1);
}

let last = performance.now();
function frame(now) {
  requestAnimationFrame(frame);
  if (!animated) return;
  if (gov.skip(now)) return;          // frame cap of the current tier
  gov.frame(now);
  const dt = Math.min(.05, (now - last) / 1000);
  last = now;
  const t = now / 1000;

  // colour glides toward the state's colour
  V.color = mix(V.color, V.target, 1 - Math.exp(-dt * 4));
  // the live level the state reacts to, smoothed (fast attack, slower release)
  if (S) {
    const P = V.physics;
    const raw = P.react === 'voice' ? S.voice : P.react === 'mic' || S.gate ? S.mic : 0;
    const tau = raw > V.level ? .09 : .26;
    V.level += (raw - V.level) * (1 - Math.exp(-dt / tau));
    V.micShown += ((S.mic || 0) - V.micShown) * (1 - Math.exp(-dt / .08));
    V.voiceShown += ((S.voice || 0) - V.voiceShown) * (1 - Math.exp(-dt / .08));
    setMeter('mic', V.micShown);
    setMeter('voice', V.voiceShown);
    $('mic').style.setProperty('--lvl', V.micShown.toFixed(3));
  }
  V.burst *= Math.exp(-dt * 2.2);

  // each layer is timed (main-thread cost) for the Ctrl+Shift+F readout
  // `window.__hudSkip = {r3d: true, …}` switches a layer off — a diagnostic
  // for finding what a given PC struggles with; nothing sets it by default.
  const off = window.__hudSkip || NO_SKIP;
  let a = performance.now();
  if (!off.bg) bg.draw(t, dt);
  let b = performance.now(); cost.bg += b - a; a = b;
  if (reactor && !off.r3d) reactor.render(now);
  b = performance.now(); cost.r3d += b - a; a = b;
  if (!off.rings) rings.draw(t, dt);
  b = performance.now(); cost.rings += b - a; a = b;
  if (!V.mini && !off.scope) drawScope(t);
  b = performance.now(); cost.scope += b - a;
  cost.n += 1;
  if (perfShown) drawPerf(now);
}
const NO_SKIP = {};
const cost = { bg: 0, r3d: 0, rings: 0, scope: 0, n: 0 };
window.__hudCost = () => {
  const n = Math.max(1, cost.n);
  const out = { bg: cost.bg / n, r3d: cost.r3d / n, rings: cost.rings / n, scope: cost.scope / n, frames: cost.n, fps: gov.fps, tier: gov.Q.name };
  Object.assign(cost, { bg: 0, r3d: 0, rings: 0, scope: 0, n: 0 });
  return out;
};

// ── adaptive quality ──────────────────────────────────────────────────────
// See perf.js. Nothing is removed at any tier — only how it is drawn.
const hw = guessTier();
let saveTierTimer = null;
const gov = createGovernor({
  initial: hw.tier,
  onChange: (Q, tier, why) => {
    applyQuality(Q);
    console.info(`[hud] qualité → ${Q.name} (${why})`);
    // remember what this PC can do, once it has held for a while
    clearTimeout(saveTierTimer);
    if (gov.locked === null) {
      saveTierTimer = setTimeout(() => { prefs.quality_tier = gov.tier; savePrefs(); }, 15000);
    }
  },
});
function applyQuality(Q) {
  bg.setQuality(Q);
  rings.setQuality(Q);
  if (reactor) reactor.setQuality(Q);
  document.body.classList.toggle('lite', !Q.backdrop);
  call('setPushRate', Q.push);
  refreshQualityButtons();
}

let perfShown = false, perfLast = 0;
function drawPerf(now) {
  if (now - perfLast < 500) return;
  perfLast = now;
  const r = reactor && reactor.info ? reactor.info() : null;
  const n = Math.max(1, cost.n);
  const ms = (v) => (v / n).toFixed(1);
  $('perf').textContent = `${gov.fps.toFixed(0)} FPS · ${gov.Q.name} · ${gov.locked === null ? 'AUTO' : 'MANUEL'}`
    + (r ? ` · 3D ×${r.pixel.toFixed(2)} · ${r.shell} pts` : '')
    + ` · JS ms : 3D ${ms(cost.r3d)} anneaux ${ms(cost.rings)} fond ${ms(cost.bg)} onde ${ms(cost.scope)} · ${hw.gpu}`;
  Object.assign(cost, { bg: 0, r3d: 0, rings: 0, scope: 0, n: 0 });
}

function refreshQualityButtons() {
  document.querySelectorAll('#quality button').forEach((b) => {
    const v = b.dataset.q;
    b.classList.toggle('sel', v === 'auto' ? gov.locked === null : gov.locked === Number(v));
  });
  const note = $('quality-note');
  if (note) note.textContent = `Actuel : ${gov.Q.name}${gov.locked === null ? ' (choisi automatiquement)' : ''}`;
}
function setQualityPref(v) {
  prefs.quality = v;
  gov.lock(v === 'auto' ? null : v);
  savePrefs(); refreshQualityButtons(); sound.play('click');
}
function buildQuality() {
  const box = $('quality');
  [['auto', 'AUTO'], ...TIERS.map((t, i) => [String(i), t.name])].forEach(([v, label]) => {
    const b = document.createElement('button');
    b.dataset.q = v; b.textContent = label;
    b.onclick = () => setQualityPref(v === 'auto' ? 'auto' : Number(v));
    box.appendChild(b);
  });
}

let reactorStarted = false;
function startReactor() {
  if (reactorStarted) return;
  reactorStarted = true;
  import('./reactor.js')
    .then(({ createReactor }) => createReactor($('r3d'), V, gov.Q))
    .then((r) => { reactor = r; r.setQuality(gov.Q); })
    .catch((err) => { console.error('reactor 3D indisponible :', err && err.message); V.no3d = true; });
}

// ── the public face of the page ───────────────────────────────────────────
window.HUD = {
  init: onInit,
  tick: onTick,
  metrics: onMetrics,
  setMini,
  setAnimated(on) {
    if (on && !animated) { last = performance.now(); if (reactor) reactor.resume(); }
    animated = on;
  },
};

const settings = createSettings({
  call, sound, rpc,
  caps: () => init.caps || {},
  isLocal: () => !!init.local,
  swatches: SW,
  tiers: TIERS,
  gov: () => gov,
  getPrefs: () => prefs,
  setPrefs: (p) => { Object.assign(prefs, p); savePrefs(); },
  applyAccent: (hex) => { applyAccent(hex); settings.refreshInterface(); },
  setSound: (on, vol) => {
    prefs.sound = on; prefs.volume = vol; sound.set(on, vol);
    $('btn-sound').classList.toggle('muted', !on); savePrefs();
  },
  setQuality: (v) => setQualityPref(v),
});
const studio = createStudio({
  call, sound, rpc, toast,
  caps: () => init.caps || {},
  isMini: () => V.mini,
});
const mind = createMind({
  rpc, sound, call,
  caps: () => init.caps || {},
});
const graph = createGraph({ sound });
pollBrain(rpc);                       // badge « qui répond » (caché si le backend ne connaît pas matthew.*)
Object.assign(window.HUD, {
  openStudio: () => studio.open(),
  openMind: () => mind.open(),
  showGraph: (payload) => graph.show(payload),
  rpcResult(id, result) {
    const done = pending.get(id);
    if (done) { pending.delete(id); done(result || {}); }
  },
  fileAttached(r) {
    const chip = $('file-chip');
    if (r.busy) { chip.hidden = false; chip.classList.add('busy'); setText($('file-name'), 'Envoi…'); return; }
    chip.classList.remove('busy');
    if (r.ok) {
      chip.hidden = false;
      setText($('file-name'), `📎 ${r.name}`);
      $('file-name').title = r.name;
      toast('FICHIER JOINT', `${r.name} — demandez par exemple « résume ce fichier ».`, null, 5000);
      sound.play('open');
      $('cmd').focus();
    } else {
      chip.hidden = true;
      toast('FICHIER', r.error || 'échec de l\'envoi', '#F87171', 6000);
      sound.play('error');
    }
  },
  dragging(on) { $('dropzone').hidden = !on; },
  clipboard(text) {
    if (V.mini || !prefs.clipboard) return;
    clipText = text;
    setText($('clip-text'), text);
    $('clip').hidden = false;
    clearTimeout(clipTimer);
    clipTimer = setTimeout(() => { $('clip').hidden = true; }, 15000);
  },
  camera(src) {
    const box = $('camera');
    if (!src) { box.hidden = true; return; }
    $('camera-img').src = src;
    box.hidden = false;
  },
  openSettings: (tab) => settings.open(tab || 'connexion'),
  settingsData: (d) => settings.fill(d),
  settingsTest: (r) => settings.tested(r),
  settingsSaved: (r) => settings.saved(r),
  server: (sv) => setServer(sv),
});

wire();
buildChips();
renderMessages([]);
clock();
setInterval(clock, 1000);
requestAnimationFrame(frame);

buildQuality();
applyQuality(gov.Q);

connect().then((b) => {
  bridge = b;
  call('ready');
  // a page that never hears from Python still boots, with the simulator's
  // absence visible as "INITIALISATION" rather than a blank screen
  setTimeout(() => { if (!booted) boot(); startReactor(); }, 1500);
});
