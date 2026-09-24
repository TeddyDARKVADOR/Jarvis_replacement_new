/**
 * host.js — what the phone needs to drive the same page the desktop drives.
 *
 * WHY THIS FILE EXISTS
 *   The desktop resolves each face in Python (`presence.Director`, inside
 *   client_desktop/ui/avatar_view.py) and pushes the finished performance
 *   through `window.JARVIS.perform`. A phone has no Python — but it has this
 *   page, and this page already carries `director.js`, the same Director,
 *   proven identical on 3 432 decisions (avatar/checks/director_parity.py).
 *   So the phone sends raw facts and the page resolves them. There is no
 *   second engine and no Kotlin copy of any rule.
 *
 * WHAT IT MIRRORS, LINE FOR LINE
 *   avatar_view._state_word   the one word the Director reasons about
 *   net.py (EV_AVATAR)        a directive older than 25 s is dropped — /ws
 *                             replays the last 50 events to every client that
 *                             connects, and a face is not a transcript line —
 *                             and one older than the last played is ignored,
 *                             so a reconnection never puts an old face back on
 *   avatar_view.reresolve     ask the Director again as intents decay, push
 *                             only what changed
 *
 * WHO CALLS IT
 *   Only a page opened with `?host=phone`. The Android WebView posts
 *   `{type: 'host-…'}` messages (see bottom). The desktop never loads this
 *   path, and `bridge.js`'s own message types are untouched.
 */
import { Director, parseDirective, INTENT_TTL_S } from './director.js';
import { Catalogue, motionOf } from './catalog.js';

/** Same number as client_desktop/protocol.py AVATAR_FRESH_SECONDS. */
export const AVATAR_FRESH_SECONDS = INTENT_TTL_S;

/** Same as client_desktop/ui/avatar_view.py WAKE_MAX_AGE_S. */
export const WAKE_MAX_AGE_S = 1.5;

/** Android LinkState -> the desktop's LinkState values. */
const LINK_WORD = {
  DISCONNECTED: 'OFFLINE', CONNECTING: 'CONNECTING', CONNECTED: 'CONNECTED',
  RECONNECTING: 'RECONNECTING', ERROR: 'ERROR',
};

/**
 * The state word, exactly as avatar_view._state_word computes it:
 * a pending confirmation first, then a link that is not up, then a fresh
 * wake, then what the assistant says it is doing.
 */
export function stateWord({ link = 'DISCONNECTED', assistant = 'UNKNOWN', confirm = false,
                            wokeAgoS = Infinity } = {}) {
  if (confirm) return 'CONFIRM';
  const linkWord = LINK_WORD[String(link).toUpperCase()] || 'OFFLINE';
  if (linkWord !== 'CONNECTED') return linkWord;
  if (Number.isFinite(wokeAgoS) && wokeAgoS >= 0 && wokeAgoS < WAKE_MAX_AGE_S) return 'WAKING';
  return String(assistant || 'UNKNOWN').toUpperCase();
}

export class PhoneHost {
  /**
   * @param {object} o
   * @param {Director} o.director
   * @param {(perf: object) => void} o.perform   window.JARVIS.perform
   * @param {(level: number) => void} o.speak    window.JARVIS.speak
   * @param {(level: number) => void} o.listen   window.JARVIS.listen
   * @param {() => number} o.monotonic  seconds, for the Director's decay
   * @param {() => number} o.wall       epoch seconds, to age a server `ts`
   */
  constructor({ director, perform, speak, listen, monotonic, wall }) {
    this.director = director;
    this.perform = perform;
    this.speak = speak || (() => {});
    this.listen = listen || (() => {});
    this.monotonic = monotonic;
    this.wall = wall;
    this.word = 'OFFLINE';
    this.speaker = 0;
    this.lastTs = 0;           // server ts of the last directive played
    this.lastJson = '';
    this.dropped = { stale: 0, older: 0, invalid: 0 };
    this.played = 0;
    this.levels = 0;
    this.maxSpeaker = 0;
  }

  /** What the host did so far: read by the emulator suite through logcat. */
  stats() {
    let expression = null;
    try { expression = JSON.parse(this.lastJson || '{}').expression || null; } catch { /* rien */ }
    return { word: this.word, played: this.played, ...this.dropped, levels: this.levels,
             maxSpeaker: Math.round(this.maxSpeaker * 1000) / 1000, expression };
  }

  /** New machine facts. Resolves and pushes only if the face changed. */
  state(facts) {
    this.word = stateWord(facts || {});
    return this._push();
  }

  /**
   * An `avatar` event off /ws: `{type, ts, directive}`. Returns what was
   * done with it — 'played', 'stale', 'older' or 'invalid' — so the tests
   * and the host can see the rule at work instead of guessing.
   */
  intent(event) {
    if (!event || typeof event !== 'object' || !event.directive) {
      this.dropped.invalid += 1;
      return 'invalid';
    }
    let age = 0;
    const stamp = Number(event.ts);
    if (event.ts !== undefined && event.ts !== null && Number.isFinite(stamp)) {
      age = Math.max(0, this.wall() - stamp);
      if (age > AVATAR_FRESH_SECONDS) {
        this.dropped.stale += 1;
        return 'stale';
      }
      // Ordered by the server's own clock: no phone clock skew can reorder
      // two server stamps.
      if (stamp <= this.lastTs) {
        this.dropped.older += 1;
        return 'older';
      }
      this.lastTs = stamp;
    }
    const directive = parseDirective(event.directive);
    if (!directive) {
      this.dropped.invalid += 1;
      return 'invalid';
    }
    // Aged on arrival: a decision already 10 s old has 15 s left, not 25.
    this.director.setIntent(directive, this.monotonic() - age);
    this._push(true);
    this.played += 1;
    return 'played';
  }

  /** Audio levels, ~30 Hz. The mouth follows the speaker; ears follow the mic. */
  level({ speaker = 0, mic = 0 } = {}) {
    this.speaker = Number(speaker) || 0;
    this.levels += 1;
    this.maxSpeaker = Math.max(this.maxSpeaker, this.speaker);
    this.speak(this.speaker);
    if (mic > 0) this.listen(Number(mic) || 0);
  }

  /** Called on a timer: intents decay, the face follows. */
  tick() {
    return this._push();
  }

  _push(force = false) {
    const perf = this.director.resolve(this.word, { speechLevel: this.speaker, now: this.monotonic() });
    const json = JSON.stringify(perf);
    if (!force && json === this.lastJson) return false;
    this.lastJson = json;
    this.perform(perf);
    return true;
  }
}

// ── browser wiring: only for `?host=phone` ─────────────────────────────────

function androidBridge() {
  return typeof window !== 'undefined' ? window.JarvisAndroid : undefined;
}

function tell(method, payload) {
  const a = androidBridge();
  try {
    if (a && typeof a[method] === 'function') a[method](JSON.stringify(payload || {}));
  } catch (err) {
    console.warn('[host] rapport a l\'hote impossible :', err.message);
  }
}

async function waitForBody(timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (document.body.classList.contains('failed')) return 'failed';
    if (window.JARVIS && window.JARVIS.ready && typeof window.JARVIS.status === 'function') return 'ready';
    await new Promise((r) => setTimeout(r, 50));
  }
  return 'timeout';
}

export async function bootPhoneHost(manifest) {
  const outcome = await waitForBody(20000);
  if (outcome !== 'ready') {
    tell('onFailed', { reason: outcome });
    return null;
  }
  const status = window.JARVIS.status();
  if (status.procedural) {
    // The phone only shows the face for a verified model. A procedural body
    // means the model did not load: say so, the host falls back to the 2D core.
    tell('onFailed', { reason: 'procedural', status });
    return null;
  }
  const body = window.__body || {};
  const catalogue = new Catalogue(
    body.detectedParts ? [...body.detectedParts] : (status.parts || ['head', 'torso']),
    motionOf(manifest || {}),
    Object.keys(body.clips || {}),
  );
  const host = new PhoneHost({
    director: new Director(catalogue),
    perform: (p) => window.JARVIS.perform(p),
    speak: (l) => window.JARVIS.speak(l),
    listen: (l) => window.JARVIS.listen(l),
    monotonic: () => performance.now() / 1000,
    wall: () => Date.now() / 1000,
  });
  let visible = true;
  const every = () => [setInterval(() => host.tick(), 500),
    // A report every 5 s while seen, none while hidden: the emulator suite
    // reads these lines, and their absence is how it sees the pause.
    setInterval(() => tell('onStats', Object.assign({ visible }, host.stats())), 5000)];
  let timers = every();

  window.addEventListener('message', (event) => {
    const d = event.data;
    if (!d || typeof d !== 'object') return;
    if (d.type === 'host-state') host.state(d.facts);
    else if (d.type === 'host-intent') {
      const outcome = host.intent(d.event);
      tell('onIntent', { outcome, expression: host.stats().expression });
    } else if (d.type === 'host-level') host.level(d);
    else if (d.type === 'host-visible') {
      visible = Boolean(d.visible);
      window.JARVIS.setAnimated(visible);
      timers.forEach(clearInterval);
      timers = visible ? every() : [];
      tell('onStats', Object.assign({ visible }, host.stats()));
    }
  });
  window.JARVIS.host = host;
  // A face nobody can see is not ready: a WebView laid out WRAP_CONTENT gets a
  // zero-height viewport, every `height: 100%` collapses, and the model draws
  // into nothing — while the host would hide its core. Say so; the core stays.
  const canvas = typeof document !== 'undefined' && document.getElementById('stage');
  const view = canvas ? [canvas.clientWidth, canvas.clientHeight] : null;
  if (view && !(view[0] > 0 && view[1] > 0)) {
    tell('onFailed', { reason: 'canvas vide', view });
    return host;
  }
  tell('onReady', { status: { model: status.model, morphs: status.morphs, parts: status.parts }, view });
  return host;
}

if (typeof window !== 'undefined' && typeof location !== 'undefined'
    && new URLSearchParams(location.search).get('host') === 'phone') {
  // The manifest the phone installed is served at manifest.json by the
  // WebView; main.js reads the same one, so page and Director agree.
  fetch(new URL('../manifest.json', import.meta.url).href, { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}))
    .then((manifest) => bootPhoneHost(manifest));
}
