/**
 * rig.js — the 52 coefficients, and the life that is not in them.
 *
 * WHAT THIS OWNS
 *   One number per ARKit shape, moving toward whatever `presence/` last asked
 *   for. Nothing here decides anything: the director chose the face, this makes
 *   the face arrive smoothly and keeps it alive between decisions.
 *
 * WHY THE SMOOTHING IS FRAME-RATE INDEPENDENT
 *   `w += (target - w) * k` is the obvious one-liner and it is wrong: its speed
 *   depends on how often it runs, so the same face snaps on a 144 Hz monitor
 *   and drifts on a throttled background tab — and Windows throttles this tab
 *   the moment a maximised editor covers the panel, which is where it spends
 *   most of its life. The exponential form below settles in the same wall-clock
 *   time at any frame rate. Same argument `client_desktop/ui/core_widget.py`
 *   makes for driving its animation off `time.monotonic()`.
 *
 * WHY BLINKING, BREATHING AND SACCADES LIVE HERE AND NOT IN PYTHON
 *   They are 60 fps phenomena with no decision content. Sending a blink over a
 *   websocket would be spending 200 ms of round trip to close an eyelid, and a
 *   face that blinks only when the server speaks is a face that is obviously
 *   being puppeted. These run off the local clock and never stop — which is the
 *   single largest contributor to "it looks alive" and costs nothing.
 *
 * THE LAYERS, AND HOW THEY COMBINE
 *   expression   what the director sent
 *   micro        micro-expressions from the idle layer (idle.js)
 *   viseme       what the mouth is doing while speaking
 *   life         blink / saccade, generated here
 *
 *   Combined with max(), never by adding. Two layers that both raise a brow
 *   must not sum past 1.0 and clip — and max() is what lets JARVIS keep smiling
 *   while he talks instead of the smile being overwritten by every syllable.
 */

const SETTLE_FAST = 0.075;   // s — l'expression arrive vite
const SETTLE_SLOW = 0.22;    // s — mais repart lentement : un visage se defait
const VISEME_SETTLE = 0.035; // s — la bouche doit suivre la voix, pas la suivre en retard

/** Eyelid shapes: driven by the blink generator, never smoothed like the rest. */
const LIDS = ['eyeBlinkLeft', 'eyeBlinkRight'];

/** Shapes the gaze owns. The saccade generator adds to these. */
const LOOKS = [
  'eyeLookInLeft', 'eyeLookOutLeft', 'eyeLookUpLeft', 'eyeLookDownLeft',
  'eyeLookInRight', 'eyeLookOutRight', 'eyeLookUpRight', 'eyeLookDownRight',
];

export class Rig {
  /**
   * @param {(name: string, weight: number) => void} apply
   *        Writes one resolved coefficient to whatever body is loaded. The rig
   *        never touches three.js: this indirection is what lets the exact same
   *        code drive a GLB with 52 morph targets and the procedural head that
   *        has no morph targets at all.
   */
  constructor(apply) {
    this.apply = apply;

    this.current = Object.create(null);   // ce qui est affiche
    this.expression = Object.create(null); // ce que le directeur a demande
    this.viseme = Object.create(null);     // ce que la bouche fait
    this.visemeCurrent = Object.create(null);
    this.micro = Object.create(null);      // ce que le repos ajoute, voir idle.js

    this.gaze = 'user';
    this.closed = false;      // Gaze.CLOSED — les paupieres restent baissees

    // ── blink ────────────────────────────────────────────────────────────
    // Humans blink every 2–10 s, not on a timer. A periodic blink is uncanny in
    // a way people notice without being able to name.
    this.nextBlink = 1.5 + Math.random() * 4;
    this.blinkPhase = -1;     // <0 : pas de clignement en cours
    this.blinkSpeed = 1;
    this.slowBlink = false;

    // ── saccades ─────────────────────────────────────────────────────────
    // Eyes never hold perfectly still. 1–3 small jumps per second while awake.
    this.saccade = { x: 0, y: 0, tx: 0, ty: 0, next: 0 };

    this.t = 0;
  }

  /** A `Performance`'s blendshapes, straight off the wire. */
  setExpression(blendshapes, gaze) {
    this.expression = Object.assign(Object.create(null), blendshapes || {});
    if (gaze) {
      this.gaze = gaze;
      this.closed = gaze === 'closed';
    }
  }

  /** One viseme's weights. Called by lipsync.js, ~30 times a second. */
  setViseme(weights) {
    this.viseme = weights || Object.create(null);
  }

  /**
   * Micro-expressions from the idle layer.
   *
   * A separate input from `setExpression` on purpose: these change several
   * times a second and must not be smoothed against, or replaced by, what the
   * director decided. They are combined with max() like every other layer —
   * which is what lets a 0.08 brow flicker happen on top of a held smile
   * without either one eating the other.
   */
  setMicro(shapes) {
    this.micro = shapes || Object.create(null);
  }

  /** Slow, heavy blinks — TIRED and SLEEPING ask for these. */
  setSlowBlink(on) {
    this.slowBlink = !!on;
  }

  update(dt) {
    this.t += dt;

    this._blink(dt);
    this._saccade(dt);

    // Le viseme a sa propre constante de temps : la bouche doit coller a la
    // voix, alors que le reste du visage a le droit de trainer.
    approach(this.visemeCurrent, this.viseme, dt, VISEME_SETTLE, VISEME_SETTLE);

    const target = Object.create(null);
    for (const name in this.expression) target[name] = this.expression[name];
    for (const name in this.micro) {
      target[name] = Math.max(target[name] || 0, this.micro[name]);
    }
    for (const name in this.visemeCurrent) {
      target[name] = Math.max(target[name] || 0, this.visemeCurrent[name]);
    }

    approach(this.current, target, dt, SETTLE_FAST, SETTLE_SLOW);

    // Les paupieres sont ecrites APRES le lissage : un clignement lisse n'est
    // plus un clignement, c'est un endormissement.
    const lid = this._lidWeight();
    if (lid > 0) {
      for (const name of LIDS) {
        this.current[name] = Math.max(this.current[name] || 0, lid);
      }
    }

    // Idem pour les micro-saccades, qui s'ajoutent au regard demande.
    this._writeSaccade();

    for (const name in this.current) this.apply(name, this.current[name]);
  }

  // ── life ──────────────────────────────────────────────────────────────────

  _blink(dt) {
    if (this.closed) { this.blinkPhase = -1; return; }

    if (this.blinkPhase >= 0) {
      this.blinkPhase += dt * this.blinkSpeed;
      if (this.blinkPhase >= 1) this.blinkPhase = -1;
      return;
    }
    this.nextBlink -= dt;
    if (this.nextBlink <= 0) {
      this.blinkPhase = 0;
      this.blinkSpeed = this.slowBlink ? 2.2 : 7.0;  // 1/duree du clignement
      this.nextBlink = this.slowBlink
        ? 1.2 + Math.random() * 2.0
        : 2.0 + Math.random() * 6.0;
    }
  }

  _lidWeight() {
    if (this.closed) return 1;
    if (this.blinkPhase < 0) return 0;
    // Fermeture rapide, ouverture plus lente — c'est ainsi qu'une paupiere
    // bouge, et sin() donnerait le contraire.
    const p = this.blinkPhase;
    return p < 0.35 ? p / 0.35 : 1 - (p - 0.35) / 0.65;
  }

  _saccade(dt) {
    if (this.closed) { this.saccade.x = this.saccade.y = 0; return; }
    this.saccade.next -= dt;
    if (this.saccade.next <= 0) {
      // Amplitude plus large quand le regard est "ailleurs" : chercher, c'est
      // bouger les yeux. Regarder quelqu'un, c'est les garder presque fixes.
      const wide = this.gaze === 'away' || this.gaze === 'around';
      const a = wide ? 0.22 : 0.06;
      this.saccade.tx = (Math.random() * 2 - 1) * a;
      this.saccade.ty = (Math.random() * 2 - 1) * a * 0.6;
      this.saccade.next = wide ? 0.3 + Math.random() * 0.5
                               : 0.6 + Math.random() * 1.6;
    }
    const k = 1 - Math.exp(-dt / 0.045);   // les saccades sont quasi instantanees
    this.saccade.x += (this.saccade.tx - this.saccade.x) * k;
    this.saccade.y += (this.saccade.ty - this.saccade.y) * k;
  }

  _writeSaccade() {
    const { x, y } = this.saccade;
    const add = (name, v) => {
      if (v > 0.002) this.current[name] = Math.min(1, (this.current[name] || 0) + v);
    };
    // ARKit nomme les regards par anatomie : l'oeil gauche qui va vers le nez
    // est `In`, l'oeil droit qui va du meme cote est `Out`.
    if (x > 0) { add('eyeLookInLeft', x); add('eyeLookOutRight', x); }
    else       { add('eyeLookOutLeft', -x); add('eyeLookInRight', -x); }
    if (y > 0) { add('eyeLookUpLeft', y); add('eyeLookUpRight', y); }
    else       { add('eyeLookDownLeft', -y); add('eyeLookDownRight', -y); }
  }
}

/**
 * Move every weight in `state` toward `target`, in wall-clock time.
 *
 * Shapes present in `state` but absent from `target` relax to zero rather than
 * being deleted — a face that drops a coefficient in one frame twitches.
 */
function approach(state, target, dt, tauUp, tauDown) {
  const kUp = 1 - Math.exp(-dt / tauUp);
  const kDown = 1 - Math.exp(-dt / tauDown);

  for (const name in target) {
    const want = target[name];
    const have = state[name] || 0;
    state[name] = have + (want - have) * (want > have ? kUp : kDown);
  }
  for (const name in state) {
    if (name in target) continue;
    const have = state[name];
    if (have < 0.0015) { state[name] = 0; continue; }
    state[name] = have + (0 - have) * kDown;
  }
}
