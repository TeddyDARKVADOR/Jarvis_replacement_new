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
 *   idle      always running — respiration, derive, report du poids,
 *             micro-expressions. Voir idle.js.
 *
 *   They are SUMMED onto the rest pose, not blended by priority. Priority would
 *   mean a nod cancels the lean-in that made the nod mean something. Summing is
 *   why JARVIS can lean in, watch the user and nod at the same time — which is
 *   what attention looks like, and is one gesture slot in a priority system.
 */

import { Idle } from './idle.js';
import { Rng } from './rng.js';
import { GESTURE_NEEDS } from './catalog.js';

const TAU = Math.PI * 2;
const DEG = Math.PI / 180;

/**
 * Les canaux qui vivent sous la nuque, et que le mode visage remet a zero.
 *
 * La tete (`headRx/Ry/Rz`) n'y est evidemment pas. `rootRy` si : c'est le
 * report du poids d'une jambe sur l'autre, et geler les jambes sans lui serait
 * geler les jambes a moitie.
 */
const BODY_CHANNELS = ['spineRx', 'spineRy', 'rootY', 'rootZ', 'rootRy'];

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
 *  that turns as far as the gaze looks like a security camera.
 *
 *  Exported: `gaze.js` reads it as the head's share of each gaze and moves the
 *  head there itself, late and slow, while the eyes lead. `gestures.js` only
 *  falls back to it when no gaze controller is wired. */
export const GAZE_HEAD = {
  user:   { headRy: 0, headRx: 0 },
  screen: { headRy: 9 * DEG, headRx: -1 * DEG },
  away:   { headRy: -12 * DEG, headRx: -5 * DEG },
  down:   { headRy: 0, headRx: 8 * DEG },
  around: { headRy: 0, headRx: -2 * DEG },
  closed: { headRy: 0, headRx: 3 * DEG },
};

/**
 * Gestures during which the eyes keep their target while the head moves.
 *
 * Nodding while looking at someone is the ordinary case: the head moves, the
 * gaze does not. `look_away`, `turn`, `think` and `look_around` are the
 * opposite — the head moving IS the gaze moving — so they are not here.
 */
const KEEPS_EYE_CONTACT = new Set([
  'nod', 'shake_head', 'tilt_head', 'look_at_user', 'lean_in', 'lean_back',
  'blink_slow', 'sigh', 'shrug', 'bow',
]);

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
   * @param {Rng} [rng]    the engine's randomness — see rng.js.
   */
  constructor(body, rng) {
    this.rng = rng || new Rng();
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
    this.smoothed = { headRx: 0, headRy: 0, headRz: 0, spineRx: 0, spineRy: 0,
                      rootY: 0, rootZ: 0, rootRy: 0 };

    // Le repos. Il tourne en permanence, y compris pendant un clip : une
    // animation qui fige la respiration se lit comme un blocage.
    this.idle = new Idle(this.nodes, this.rng.fork('idle'));

    // Ce que les autres couches ajoutent a la tete, pose par le moteur a
    // chaque image : la part de tete du regard (gaze.js, en retard sur les
    // yeux), l'inclinaison tenue de l'etat de presence (states.js), et
    // l'accent de l'intention (accents.js).
    this.gazeHead = null;
    this.gazeDecided = false;
    this.stateHead = { rx: 0, rz: 0 };
    this.accentHead = null;
    /** La rotation de tete que les yeux doivent compenser, en sortie. */
    this.vorHead = { rx: 0, ry: 0 };
    /** Ce que le dernier `play()` a reellement fait. */
    this.lastPlay = { name: 'idle', via: 'procedural' };

    // Vitesse des gestes, venue de l'affect. 1.0 = nominal.
    this.tempo = 1.0;

    // La pose de repos des bras, appliquee une fois et poursuivie tant qu'aucun
    // clip ne joue. Voir `_restArms`.
    this.armRest = (body.manifest && body.manifest.rig && body.manifest.rig.armRest) || {};
    this._armPhase = this.rng.next() * TAU;
    this._restArms();

    /**
     * `rig.motion: "face"` — le corps est la, on ne le bouge pas.
     *
     * POURQUOI CE N'EST PAS UN REPLI MAIS UN CHOIX
     *   Un humanoide telecharge arrive avec des bras, des mains et des jambes,
     *   et rien pour les animer : `wave`, `point` et `explain` demandent des
     *   clips Mixamo, retargetes et fondus. Tant qu'ils n'existent pas, offrir
     *   ces gestes fait que JARVIS choisit `wave` et obtient un mouvement de
     *   nuque — un corps qui salue avec sa tete, ce qui se lit plus mal qu'un
     *   corps qui se tient tranquille.
     *
     *   `presence/catalog.py` lit le meme champ et retire ces gestes du
     *   vocabulaire, donc ils ne sont plus demandes. Ce garde-ci est la
     *   deuxieme moitie : ce qui arrive quand meme — une posture, une derive de
     *   repos, le labo — ne descend pas sous la nuque non plus.
     */
    const rig = (body.manifest && body.manifest.rig) || {};
    this.faceOnly = String(rig.motion || 'full').toLowerCase() === 'face';

    /**
     * Un clip `idle` installe devient la couche de fond, en boucle.
     *
     * Ce n'est pas utilise aujourd'hui — aucun clip n'est installe, et le mode
     * visage n'en jouerait pas. C'est la preparation de `"motion": "full"` :
     * le jour ou un idle Mixamo arrive, il tourne en permanence, chaque geste
     * se fond PAR-DESSUS et le corps revient a lui a la fin, au lieu de
     * retomber en pose de bind. Le cerveau n'en sait rien.
     */
    this.base = null;
    const idleClip = body.clips && body.clips.idle;
    if (idleClip && body.mixer && !this.faceOnly) {
      this.base = body.mixer.clipAction(idleClip);
      this.base.setLoop(2201 /* THREE.LoopRepeat */, Infinity);
      this.base.play();
    }
    if (body.mixer && body.mixer.addEventListener) {
      body.mixer.addEventListener('finished', (event) => {
        if (event.action !== this.action) return;
        // Un geste fini rend la main a l'idle, en fondu — ou a la pose de
        // repos s'il n'y en a pas.
        if (this.base) this.base.reset().play().crossFadeFrom(this.action, 0.35, true);
        this.action = null;
      });
    }
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
    if (this.action || this.base) return;   // un clip pilote les bras : ne pas le contredire
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

  /**
   * Play a gesture, and say what actually happened.
   *
   * Returns `{name, via}` where `via` is `clip`, `procedural`, `frozen` (the
   * gesture needs a limb that `rig.motion: "face"` holds still — nothing plays),
   * `absent` (the model has no bone to move) or `none` (nothing can play it). The lab prints this, not the request: a
   * gesture announced as played and zeroed on the next frame is the one lie
   * the lab exists to prevent.
   */
  play(name) {
    if (!name) return this.lastPlay;

    const needs = GESTURE_NEEDS[name] || 'head';
    if (this.faceOnly && needs !== 'head') {
      this.lastPlay = { name, via: 'frozen' };
      return this.lastPlay;
    }
    // Un geste de tete sans os de tete ne tourne rien. Le dire, plutot que
    // d'annoncer un hochement que personne ne verra : c'est le modele qui n'a
    // pas la capacite, et le profil l'affiche deja (`HEAD ✗`).
    const bone = needs === 'head' ? (this.nodes.head || this.nodes.neck)
      : needs === 'torso' ? (this.nodes.spine || this.nodes.root) : true;
    if (name !== 'idle' && !bone && !(this.body.clips && this.body.clips[name])) {
      this.lastPlay = { name, via: 'absent' };
      return this.lastPlay;
    }

    const clip = this.body.clips && this.body.clips[name];
    if (clip && this.body.mixer && name !== 'idle') {
      const next = this.body.mixer.clipAction(clip);
      next.reset();
      next.setLoop(2200 /* THREE.LoopOnce */, 1);
      next.clampWhenFinished = false;
      const from = this.action && this.action !== next ? this.action : this.base;
      if (from) next.crossFadeFrom(from, 0.25, true);
      next.play();
      this.action = next;
      this.active = null;
      this.lastPlay = { name, via: 'clip' };
      return this.lastPlay;
    }

    const spec = PROCEDURAL[name];
    if (spec === undefined) {                // pas jouable : le catalogue a deja substitue
      this.lastPlay = { name, via: 'none' };
      return this.lastPlay;
    }
    this.active = spec ? { name, t: 0, duration: spec.duration, f: spec.f } : null;
    this.lastPlay = { name, via: 'procedural' };
    return this.lastPlay;
  }

  /** The gesture moving the body right now, or null. */
  get playing() {
    if (this.active) return this.active.name;
    return this.action ? (this.lastPlay && this.lastPlay.name) : null;
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

    // `rootRy` appartient a cette liste : `add()` ne copie que les cles deja
    // presentes dans la cible, donc son absence ici jetait en silence le report
    // du poids que `idle._weightShift` calcule — `smoothed.rootRy` restait a
    // zero pour toujours, et `_write` ecrivait ce zero. La couche etait
    // documentee, mesuree, et morte.
    // Reutilise d'une image a l'autre. Les cles doivent toutes y etre : `add()`
    // ne copie que les cles deja presentes — c'est exactement ce qui avait tue
    // `rootRy`.
    const out = this._out || (this._out = { headRx: 0, headRy: 0, headRz: 0,
      spineRx: 0, spineRy: 0, rootY: 0, rootZ: 0, rootRy: 0 });
    for (const key in out) out[key] = 0;

    add(out, POSTURES[this.posture]);
    if (this.gazeHead) {
      // La part de tete du regard, deja en retard sur les yeux : gaze.js.
      out.headRx += this.gazeHead.rx;
      out.headRy += this.gazeHead.ry;
    } else {
      add(out, GAZE_HEAD[this.gaze]);
    }
    out.headRx += this.stateHead.rx;
    out.headRz += this.stateHead.rz;

    // Ce que les yeux compensent : le repos, les gestes « en regardant », et
    // les accents. Pas la part du regard — celle-la, les yeux la suivent.
    let vorRx = 0;
    let vorRy = 0;

    if (this.active) {
      // Le tempo accelere les gestes procedures comme il accelere les clips :
      // un JARVIS presse hoche la tete plus vite, sinon seul son visage est
      // presse et le reste dement.
      this.active.t += dt * this.tempo;
      const p = this.active.t / this.active.duration;
      if (p >= 1) {
        this.active = null;
      } else {
        const moved = this.active.f(p);
        add(out, moved);
        // Un regard DECIDE (explicite, intention) garde sa cible meme quand le
        // geste emporte la tete : `investigate` + `gaze: user` tourne la tete
        // vers l'ecran et garde les yeux sur l'utilisateur. Sans ca, un geste
        // derive de l'intention deplacait un regard que JARVIS avait nomme.
        if (this.gazeDecided || KEEPS_EYE_CONTACT.has(this.active.name)) {
          vorRx += moved.headRx || 0;
          vorRy += moved.headRy || 0;
        }
      }
    }

    if (this.accentHead) {
      out.headRx += this.accentHead.rx;
      out.headRy += this.accentHead.ry;
      out.headRz += this.accentHead.rz;
      if (this.accentHead.keepsEyes !== false) {
        vorRx += this.accentHead.rx;
        vorRy += this.accentHead.ry;
      }
    }

    this.idle.update(dt);
    add(out, this.idle.offsets);
    vorRx += this.idle.offsets.headRx;
    vorRy += this.idle.offsets.headRy;
    this.vorHead.rx = vorRx;
    this.vorHead.ry = vorRy;
    this._swayArms(dt);

    // Mode visage : tout ce qui est sous la nuque retombe a zero, quelle que
    // soit la couche qui l'a ecrit — posture, regard, geste ou repos.
    //
    // POURQUOI ICI ET PAS DANS CHAQUE COUCHE
    //   C'est le seul endroit ou les quatre sources sont deja additionnees. Un
    //   garde par couche serait quatre endroits a retrouver le jour ou une
    //   cinquieme arrive, et la cinquieme est celle qu'on oublierait.
    //
    //   La respiration des epaules (`_swayArms`) et celle de la poitrine ne
    //   sont pas ici : ce ne sont pas des canaux d'os du buste, et un corps
    //   parfaitement rigide se lit comme un mannequin — c'est exactement ce que
    //   `_restArms` existe pour eviter.
    if (this.faceOnly) {
      for (const key of BODY_CHANNELS) out[key] = 0;
    }

    // 90 ms : assez rapide pour qu'un hochement reste un hochement, assez lent
    // pour qu'aucune transition ne claque.
    const k = 1 - Math.exp(-dt / 0.09);
    for (const key in out) this.smoothed[key] += (out[key] - this.smoothed[key]) * k;

    this._write();
  }

  /** What the affect asks of the idle. Passed straight through. */
  setAffect(params) {
    if (typeof params.tempo === 'number') this.tempo = Math.max(0.3, Math.min(2.5, params.tempo));
    this.idle.setAffect(params);
    if (this.body.mixer) this.body.mixer.timeScale = this.tempo;
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
      root.rotation.y = r.ry + s.rootRy;   // report du poids
    }
  }
}

function add(target, source) {
  if (!source) return;
  for (const key in source) {
    if (key in target) target[key] += source[key];
  }
}
