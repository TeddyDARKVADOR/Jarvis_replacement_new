/**
 * gestures.js — the body's movement, from two sources that look like one.
 *
 * THE PROBLEM THIS SOLVES
 *   A gesture can come from a Mixamo clip (a real animation, on a real rig) or
 *   from arithmetic on three bones. Those are completely different mechanisms,
 *   and every caller above this file would otherwise have to know which one it
 *   is getting — including the director, in Python, which must not know what a
 *   bone is.
 *
 *   So both are hidden behind `play(name)`. A clip is used when one is
 *   installed for that gesture; otherwise the procedural version runs, if there
 *   is one; otherwise nothing plays and `presence/catalog.py` already knew that
 *   and sent a different gesture instead.
 *
 * WHY PROCEDURAL GESTURES EXIST AT ALL WHEN CLIPS ARE BETTER
 *   Because a nod is not worth a 400 KB download, and because the eleven
 *   gestures a neck and a spine can perform are the eleven JARVIS needs most —
 *   nodding, shaking, tilting, leaning in, looking away. Those are the grammar
 *   of listening, they happen constantly, and a body that can only do them once
 *   someone has installed assets is a body that is mute on first run.
 *
 *   Clips win for everything with arms. That is exactly the split in
 *   `presence/model.GESTURE_REQUIRES`.
 *
 * LAYERING, WHICH IS THE PART THAT IS EASY TO GET WRONG
 *   posture   a sustained offset. Outlives every gesture.
 *   gesture   a transient, windowed to its own duration.
 *   gaze      a small head turn, following the eyes.
 *   breath    always running.
 *
 *   They are SUMMED onto the rest pose, not blended by priority. Priority would
 *   mean a nod cancels the lean-in that made the nod mean something. Summing is
 *   why JARVIS can lean in, watch the user and nod at the same time — which is
 *   what attention looks like, and is one gesture slot in a priority system.
 */

const TAU = Math.PI * 2;
const DEG = Math.PI / 180;

/**
 * The procedural repertoire. Each entry is a duration and a function of
 * normalised time `p` (0..1) returning small rotations in radians.
 *
 * Keys are the `Gesture` values from `presence/model.py`, exactly. A key that
 * is not a Gesture member can never be played, because nothing will ever send
 * that string.
 */
const PROCEDURAL = {
  idle: null,   // le souffle et les saccades suffisent — voir _breathe et rig.js

  nod: {
    duration: 0.85,
    f: (p) => ({ headRx: Math.sin(p * TAU * 1.5) * 9 * DEG * env(p) }),
  },

  shake_head: {
    duration: 0.95,
    f: (p) => ({ headRy: Math.sin(p * TAU * 1.75) * 11 * DEG * env(p) }),
  },

  tilt_head: {
    duration: 1.6,
    // Ne revient pas a zero au milieu : une tete penchee LE RESTE le temps de
    // la question. env() la ramene seulement a la fin.
    f: (p) => ({ headRz: 13 * DEG * hold(p), headRy: 3 * DEG * hold(p) }),
  },

  look_at_user: {
    duration: 0.7,
    f: (p) => ({ headRy: 0, headRx: -2 * DEG * hold(p) }),
  },

  look_away: {
    duration: 1.8,
    f: (p) => ({ headRy: -17 * DEG * hold(p), headRx: -6 * DEG * hold(p) }),
  },

  look_around: {
    duration: 3.2,
    f: (p) => ({
      headRy: Math.sin(p * TAU * 1.25) * 19 * DEG * env(p),
      headRx: Math.sin(p * TAU * 0.6) * 5 * DEG * env(p),
    }),
  },

  lean_in: {
    duration: 1.4,
    f: (p) => ({ spineRx: 5 * DEG * hold(p), rootZ: 0.055 * hold(p), headRx: -2 * DEG * hold(p) }),
  },

  lean_back: {
    duration: 1.6,
    f: (p) => ({ spineRx: -5 * DEG * hold(p), rootZ: -0.05 * hold(p), headRx: 3 * DEG * hold(p) }),
  },

  blink_slow: { duration: 1.0, f: () => ({}) },   // rig.js s'en occupe, via setSlowBlink

  sigh: {
    duration: 2.1,
    f: (p) => ({
      rootY: Math.sin(p * Math.PI) * 0.028,
      spineRx: -Math.sin(p * Math.PI) * 4 * DEG,
      headRx: Math.sin(p * Math.PI) * 5 * DEG,
    }),
  },

  shrug: {
    duration: 1.5,
    f: (p) => ({
      rootY: Math.sin(hold(p) * Math.PI * 0.5) * 0.035,
      headRz: 4 * DEG * hold(p),
      headRx: -3 * DEG * hold(p),
    }),
  },

  turn: {
    duration: 2.4,
    f: (p) => ({ spineRy: -26 * DEG * hold(p), headRy: -14 * DEG * hold(p) }),
  },

  bow: {
    duration: 1.5,
    f: (p) => ({ spineRx: 13 * DEG * env(p), headRx: 16 * DEG * env(p) }),
  },

  stretch: {
    duration: 2.6,
    f: (p) => ({
      rootY: Math.sin(p * Math.PI) * 0.05,
      spineRx: -Math.sin(p * Math.PI) * 7 * DEG,
      headRx: -Math.sin(p * Math.PI) * 10 * DEG,
    }),
  },

  think: {
    duration: 2.8,
    f: (p) => ({ headRz: 9 * DEG * hold(p), headRy: -11 * DEG * hold(p), headRx: -4 * DEG * hold(p) }),
  },
};

/** Sustained offsets. No duration — they hold until replaced. */
const POSTURES = {
  attentive: { spineRx: 2.5 * DEG, headRx: -1 * DEG, rootZ: 0.02 },
  relaxed:   { spineRx: 0, headRx: 0, rootZ: 0 },
  focused:   { spineRx: 6 * DEG, headRx: -2 * DEG, rootZ: 0.05 },
  formal:    { spineRx: -2 * DEG, headRx: 1.5 * DEG, rootZ: 0 },
  dormant:   { spineRx: -4 * DEG, headRx: 7 * DEG, rootY: -0.03, rootZ: -0.02 },
};

/** A small head turn following the eyes. The eyes do most of the work — a head
 *  that turns as far as the gaze looks like a security camera. */
const GAZE_HEAD = {
  user:   { headRy: 0, headRx: 0 },
  screen: { headRy: 9 * DEG, headRx: -1 * DEG },
  away:   { headRy: -12 * DEG, headRx: -5 * DEG },
  down:   { headRy: 0, headRx: 8 * DEG },
  around: { headRy: 0, headRx: -2 * DEG },
  closed: { headRy: 0, headRx: 3 * DEG },
};

/** Ease in and out — for gestures that go and come back. */
function env(p) {
  return Math.sin(Math.min(1, Math.max(0, p)) * Math.PI);
}

/** Rise, hold, fall — for gestures that are a *position* held for a while. */
function hold(p) {
  if (p < 0.18) return smooth(p / 0.18);
  if (p > 0.76) return smooth(1 - (p - 0.76) / 0.24);
  return 1;
}

function smooth(x) {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
}

export class Gestures {
  /**
   * @param {object} body  exposes `nodes` ({head, neck, spine, root}), and
   *                       optionally `mixer` + `clips` for the glTF backend.
   */
  constructor(body) {
    this.body = body;
    this.nodes = body.nodes || {};
    this.rest = new Map();
    for (const key of ['head', 'neck', 'spine', 'root',
                       'armLeftUpper', 'armRightUpper', 'armLeftLower', 'armRightLower']) {
      const node = this.nodes[key];
      if (node) {
        this.rest.set(key, {
          rx: node.rotation.x, ry: node.rotation.y, rz: node.rotation.z,
          px: node.position.x, py: node.position.y, pz: node.position.z,
        });
      }
    }

    this.active = null;       // { name, t, duration }
    this.action = null;       // three.js AnimationAction, quand un clip joue
    this.posture = 'relaxed';
    this.gaze = 'user';
    this.t = 0;

    // Lisse entre deux gestes : passer d'un `look_away` tenu a un `nod` sans
    // interpolation fait claquer la nuque d'une image a l'autre.
    this.smoothed = { headRx: 0, headRy: 0, headRz: 0, spineRx: 0, spineRy: 0, rootY: 0, rootZ: 0 };

    // La pose de repos des bras, appliquee une fois et poursuivie tant qu'aucun
    // clip ne joue. Voir `_restArms`.
    this.armRest = (body.manifest && body.manifest.rig && body.manifest.rig.armRest) || {};
    this._armPhase = Math.random() * TAU;
    this._restArms();
  }

  /**
   * Lower the arms out of the bind pose.
   *
   * WHY THIS EXISTS
   *   Every humanoid on every store ships in T-pose or A-pose, because that is
   *   what a bind pose is for. Load one and give it no animation and you get a
   *   scarecrow — arms straight out, for as long as nobody installs an idle
   *   clip. That is the first thing anyone sees after installing a model, and
   *   it reads as "broken" rather than as "no animation yet".
   *
   *   Three bones and two numbers fix it. It is not a replacement for a real
   *   Mixamo idle — those move the weight, the hips, the fingers — but it is
   *   the difference between a character standing there and a T.
   *
   * WHY THE ANGLES ARE OVERRIDABLE
   *   The rotation axis that lowers an arm depends on how the rig was authored.
   *   Mixamo and VRM agree often enough for one default to be right most of the
   *   time, and `manifest.rig.armRest` is the escape hatch for the rest —
   *   nobody should have to edit this file because one model was built by
   *   someone with different habits.
   */
  _restArms() {
    const shoulder = this.armRest.shoulder !== undefined ? this.armRest.shoulder : 1.13;
    const elbow = this.armRest.elbow !== undefined ? this.armRest.elbow : 0.16;
    const forward = this.armRest.forward !== undefined ? this.armRest.forward : 0.10;

    // Le signe est oppose d'un cote a l'autre : les deux bras descendent, donc
    // ils tournent en sens contraire autour du meme axe.
    const pose = [
      ['armLeftUpper', -shoulder, -forward],
      ['armRightUpper', shoulder, forward],
      ['armLeftLower', -elbow, 0],
      ['armRightLower', elbow, 0],
    ];
    for (const [key, z, x] of pose) {
      const node = this.nodes[key];
      if (!node) continue;
      const rest = this.rest.get(key);
      node.rotation.z = rest.rz + z;
      node.rotation.x = rest.rx + x;
    }
  }

  /** A breath in the shoulders. Stops the rest pose reading as a mannequin. */
  _swayArms(dt) {
    if (this.action) return;          // un clip pilote les bras : ne pas le contredire
    this._armPhase += dt;
    const sway = Math.sin(this._armPhase * 0.55) * 0.012;
    for (const [key, sign] of [['armLeftUpper', -1], ['armRightUpper', 1]]) {
      const node = this.nodes[key];
      if (!node) continue;
      const rest = this.rest.get(key);
      const base = this.armRest.shoulder !== undefined ? this.armRest.shoulder : 1.13;
      node.rotation.z = rest.rz + sign * (base + sway);
    }
  }

  /** What is actually playable here. `presence/catalog.py` already filtered on
   *  the manifest; this is the renderer's own last word. */
  can(name) {
    if (this.body.clips && this.body.clips[name]) return true;
    return Object.prototype.hasOwnProperty.call(PROCEDURAL, name);
  }

  play(name) {
    if (!name) return;

    const clip = this.body.clips && this.body.clips[name];
    if (clip && this.body.mixer) {
      const next = this.body.mixer.clipAction(clip);
      next.reset();
      next.setLoop(2200 /* THREE.LoopOnce */, 1);
      next.clampWhenFinished = false;
      if (this.action && this.action !== next) next.crossFadeFrom(this.action, 0.25, true);
      next.play();
      this.action = next;
      this.active = null;
      return;
    }

    const spec = PROCEDURAL[name];
    if (spec === undefined) return;          // pas jouable : le catalogue a deja substitue
    this.active = spec ? { name, t: 0, duration: spec.duration, f: spec.f } : null;
  }

  setPosture(name) {
    if (POSTURES[name]) this.posture = name;
  }

  setGaze(name) {
    if (GAZE_HEAD[name]) this.gaze = name;
  }

  update(dt) {
    this.t += dt;
    if (this.body.mixer) this.body.mixer.update(dt);

    const out = { headRx: 0, headRy: 0, headRz: 0, spineRx: 0, spineRy: 0, rootY: 0, rootZ: 0 };

    add(out, POSTURES[this.posture]);
    add(out, GAZE_HEAD[this.gaze]);

    if (this.active) {
      this.active.t += dt;
      const p = this.active.t / this.active.duration;
      if (p >= 1) {
        this.active = null;
      } else {
        add(out, this.active.f(p));
      }
    }

    this._breathe(out);
    this._swayArms(dt);

    // 90 ms : assez rapide pour qu'un hochement reste un hochement, assez lent
    // pour qu'aucune transition ne claque.
    const k = 1 - Math.exp(-dt / 0.09);
    for (const key in out) this.smoothed[key] += (out[key] - this.smoothed[key]) * k;

    this._write();
  }

  /**
   * Breathing. Never stops, including while a clip plays — a clip that holds a
   * pose on a body that has stopped breathing reads as a freeze, which users
   * report as "it crashed".
   */
  _breathe(out) {
    const slow = this.posture === 'dormant';
    const rate = slow ? 0.18 : 0.26;          // Hz
    const depth = slow ? 0.011 : 0.007;
    const phase = this.t * TAU * rate;
    out.rootY += Math.sin(phase) * depth;
    out.spineRx += Math.sin(phase) * 0.9 * DEG;
    // Tres leger contre-mouvement de la tete : une tete parfaitement solidaire
    // du buste est une tete vissee.
    out.headRx -= Math.sin(phase) * 0.45 * DEG;
  }

  _write() {
    const s = this.smoothed;
    const head = this.nodes.head || this.nodes.neck;
    const spine = this.nodes.spine;
    const root = this.nodes.root;

    if (head) {
      const r = this.rest.get(this.nodes.head ? 'head' : 'neck');
      head.rotation.x = r.rx + s.headRx;
      head.rotation.y = r.ry + s.headRy;
      head.rotation.z = r.rz + s.headRz;
    }
    if (spine) {
      const r = this.rest.get('spine');
      spine.rotation.x = r.rx + s.spineRx;
      spine.rotation.y = r.ry + s.spineRy;
    }
    if (root) {
      const r = this.rest.get('root');
      root.position.y = r.py + s.rootY;
      root.position.z = r.pz + s.rootZ;
    }
  }
}

function add(target, source) {
  if (!source) return;
  for (const key in source) {
    if (key in target) target[key] += source[key];
  }
}
