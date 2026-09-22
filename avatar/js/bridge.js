/**
 * bridge.js — the only door into the body.
 *
 * WHO KNOCKS, AND WHY THERE ARE THREE DOORS FOR ONE ROOM
 *   `window.JARVIS.perform(...)`   the desktop panel, through
 *                                  QWebEngineView.runJavaScript(). Synchronous,
 *                                  no serialisation surprises, no origin rules.
 *   `postMessage`                  a phone's WebView, and an <iframe> in the
 *                                  dashboard. Same payload, async, which is why
 *                                  the payload has to be self-contained.
 *   `?demo=1`                      nobody. The body drives itself through every
 *                                  expression and every installed gesture.
 *
 *   Three transports, one payload: the JSON `presence.Performance.as_json()`
 *   produces. Anything that can deliver that object can drive JARVIS, which is
 *   what makes the phone step later a hosting question and not a port.
 *
 * WHY THE DEMO MODE IS NOT A TOY
 *   It is the only way to judge an expression without talking to JARVIS, and
 *   the only way to see immediately whether a freshly installed model actually
 *   moves. Open `avatar/index.html?demo=1` in any browser — no Python, no
 *   server, no key — and every face and every gesture plays in turn with its
 *   name on screen. That loop is where the twelve faces in
 *   `presence/vocabulary.py` were actually tuned.
 *
 * WHY `speak()` IS SEPARATE FROM `perform()`
 *   The mouth needs the voice level thirty times a second; the face needs a
 *   decision once a sentence. Sending a whole performance per audio frame would
 *   make every syllable re-resolve an expression and re-trigger a gesture —
 *   JARVIS would nod on every vowel. One cheap method for the fast channel is
 *   the whole fix.
 */

const EXPRESSIONS = [
  'neutral', 'happy', 'amused', 'thinking', 'surprised', 'concerned',
  'serious', 'confused', 'proud', 'sad', 'tired', 'angry',
];

export class Bridge {
  /**
   * Built FIRST, before the model is loaded, and that ordering is the point.
   *
   * A .glb takes a few hundred milliseconds to arrive and parse. The host does
   * not know that and should not have to: the desktop panel pushes a
   * performance the moment its page reports loaded, which is well before there
   * is a face to put it on. If `window.JARVIS` only appeared once the body was
   * ready, that first performance — the one that says JARVIS just woke up —
   * would land on `undefined` and vanish.
   *
   * So the door opens immediately and what arrives early is kept. Only the
   * LAST performance is kept, not a queue of them: replaying a backlog of
   * expressions at load would make JARVIS visibly catch up through faces
   * nobody was waiting for. The last one is the only one that is still true.
   */
  constructor() {
    this.onPerform = null;
    this.onSpeak = null;
    this.demo = null;

    this.pendingPerformance = null;
    this.pendingLevel = null;
    this._onViseme = null;

    window.JARVIS = {
      perform: (payload) => this._perform(payload),
      speak: (level) => this._speak(Number(level) || 0),
      viseme: (name, weight) => this._viseme(name, weight),
      version: 1,
      ready: false,
    };

    window.addEventListener('message', (event) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'performance') this._perform(data.payload);
      else if (data.type === 'speak') this._speak(Number(data.level) || 0);
      else if (data.type === 'viseme') this._viseme(data.name, data.weight);
    });
  }

  /**
   * The body is built. Wire it up and flush whatever arrived while it was not.
   */
  attach(onPerform, onSpeak) {
    this.onPerform = onPerform;
    this.onSpeak = onSpeak;
    window.JARVIS.ready = true;

    if (this.pendingPerformance) {
      onPerform(this.pendingPerformance);
      this.pendingPerformance = null;
    }
    if (this.pendingLevel !== null) {
      onSpeak(this.pendingLevel);
      this.pendingLevel = null;
    }
  }

  _speak(level) {
    if (this.onSpeak) this.onSpeak(level);
    else this.pendingLevel = level;
  }

  /** Wired by main.js. The Audio2Face seam — see lipsync.js. */
  setVisemeSink(fn) { this._onViseme = fn; }

  _viseme(name, weight) {
    if (this._onViseme) this._onViseme(String(name), Number(weight) || 0);
  }

  _perform(payload) {
    let perf = payload;
    if (typeof perf === 'string') {
      try { perf = JSON.parse(perf); } catch { return; }
    }
    if (!perf || typeof perf !== 'object') return;
    this.stopDemo();
    if (this.onPerform) this.onPerform(perf);
    else this.pendingPerformance = perf;
  }

  // ── demo ────────────────────────────────────────────────────────────────

  /**
   * @param {string[]} gestures  what the loaded body can actually play, so the
   *                             demo never shows a gesture this model lacks.
   * @param {(text: string) => void} label
   */
  startDemo(gestures, label) {
    const playable = gestures.length ? gestures : ['idle'];
    let i = 0;

    const step = () => {
      const expression = EXPRESSIONS[i % EXPRESSIONS.length];
      const gesture = playable[i % playable.length];
      // L'intensite monte et descend au fil du cycle : c'est la seule facon de
      // voir que le cadran d'intensite est continu et pas un interrupteur.
      const intensity = 0.35 + 0.55 * Math.abs(Math.sin(i * 0.8));

      this._perform({
        expression,
        intensity,
        gesture,
        gaze: i % 4 === 2 ? 'away' : 'user',
        posture: i % 5 === 0 ? 'focused' : 'attentive',
        speech_level: 0,
        reason: 'demo',
      });
      label(`${expression} ${intensity.toFixed(2)} · ${gesture}`);
      i += 1;
    };

    step();
    this.demo = setInterval(step, 2600);

    // Une voix simulee, pour que la bouche bouge pendant la demo : sans elle on
    // ne juge que la moitie du visage.
    let t = 0;
    this.demoVoice = setInterval(() => {
      t += 0.06;
      const syllable = Math.max(0, Math.sin(t * 7) * Math.sin(t * 1.7));
      this._speak(syllable * 0.8);
    }, 60);
  }

  stopDemo() {
    if (this.demo) { clearInterval(this.demo); this.demo = null; }
    if (this.demoVoice) { clearInterval(this.demoVoice); this.demoVoice = null; }
  }
}
