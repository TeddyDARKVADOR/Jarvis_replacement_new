/**
 * engine.js — le moteur d'avatar. Une Performance entre, un visage sort.
 *
 * POURQUOI CE FICHIER EXISTE
 *   `main.js` (le panneau) et `lab.js` (le labo) cablaient chacun leur propre
 *   moteur : leur propre `applyPerformance`, leur propre boucle, leur propre
 *   ordre des couches. Ils divergeaient deja — le labo ne transmettait pas
 *   `hold_s`, ne dedoublonnait pas les gestes, et tranchait seul ce qui etait
 *   jouable. Un labo dont le moteur n'est pas celui du produit prouve des
 *   choses sur lui-meme.
 *
 *   Il n'y a donc plus qu'un moteur. Le panneau et le labo le construisent sur
 *   le corps qu'ils ont charge, lui passent des Performances, et lui laissent
 *   faire le reste. Ce que le labo montre est ce que le panneau fait, parce que
 *   c'est le meme objet.
 *
 * CE QU'IL NE CONNAIT PAS
 *   three.js. Le corps est une interface — `setMorph`, `nodes`, `update`,
 *   `capabilities()` — et le moteur ne sait pas si c'est un GLB, un VRM, la
 *   tete procedurale ou `body_null.js`, le corps sans rendu des tests. C'est
 *   ce qui permet de faire tourner exactement ce code sous Node, en quelques
 *   millisecondes, sans GPU : `avatar/checks/engine_test.mjs`.
 *
 *   Et le modele : aucun nom d'os, aucun nom de maillage. Changer de visage
 *   change le corps passe au constructeur, rien d'autre.
 *
 * L'ORDRE D'UNE IMAGE, ET POURQUOI CET ORDRE
 *     1. etat de presence   les parametres de fond de l'image (states.js)
 *     2. lip-sync           la couche bouche, et « est-il en train de parler »
 *     3. accent             le geste facial de l'intention en cours
 *     4. gestes             la tete et le corps — y compris la part de tete du
 *                           regard, calculee a l'image precedente
 *     5. rig                expression, repos, accent, parole, paupieres, yeux
 *                           — avec la rotation de tete de l'etape 4 a compenser
 *     6. corps              ce qui depend de tout ce qui precede (yeux a os, VRM)
 */

import { Rng } from './rng.js';
import { Rig, clean } from './rig.js';
import { LipSync } from './lipsync.js';
import { Gestures, GAZE_HEAD } from './gestures.js';
import { Accents } from './accents.js';
import { PresenceStates } from './states.js';
import { Recorder } from './recorder.js';

/** Plafond d'un pas de temps : un onglet qui revient d'un arriere-plan livre un
 *  dt enorme, et un dt enorme fait sauter chaque ressort a sa cible. */
export const MAX_DT = 0.1;

const clock = () => (globalThis.performance && globalThis.performance.now
  ? globalThis.performance.now() : Date.now());

/**
 * Une Performance telle qu'elle arrive, rendue sure.
 *
 * Elle vient d'un JSON — du fil, du labo, d'un enregistrement. Un nombre
 * absent, une chaine a la place d'un nombre, un NaN : rien de cela ne doit
 * atteindre un ressort. Les champs inconnus sont gardes (un hote plus recent
 * peut en envoyer), les connus sont types.
 */
export function sanitizePerformance(raw) {
  const perf = Object.assign({}, raw && typeof raw === 'object' ? raw : {});
  const num = (v, lo, hi, fallback) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : fallback;
  };
  const word = (v, fallback) => (typeof v === 'string' && v ? v : fallback);

  perf.expression = word(perf.expression, 'neutral');
  perf.intensity = num(perf.intensity, 0, 1, 0);
  perf.gesture = word(perf.gesture, 'idle');
  perf.gaze = word(perf.gaze, 'user');
  perf.posture = word(perf.posture, 'relaxed');
  perf.hold_s = num(perf.hold_s, 0, 60, 0);
  perf.tempo = num(perf.tempo, 0.3, 2.5, 1);
  perf.stillness = num(perf.stillness, 0, 1, 0.7);
  perf.gaze_hold_s = num(perf.gaze_hold_s, 0.2, 30, 4);
  if (perf.speech_level !== undefined) perf.speech_level = num(perf.speech_level, 0, 1, 0);
  perf.gaze_source = word(perf.gaze_source, 'reflex');

  const shapes = Object.create(null);
  const given = perf.blendshapes && typeof perf.blendshapes === 'object' ? perf.blendshapes : {};
  for (const name in given) {
    const v = clean(given[name]);
    if (v > 0) shapes[name] = v;
  }
  perf.blendshapes = shapes;
  return perf;
}

export class AvatarEngine {
  /**
   * @param {object} body  n'importe quel corps : gltf, vrm, procedural, null
   * @param {object} [options]
   * @param {number} [options.seed]    graine ; absente = tiree au hasard
   * @param {boolean} [options.record] enregistrer depuis la premiere image
   */
  constructor(body, options = {}) {
    this.body = body;
    this.rng = new Rng(options.seed);
    this.caps = body && typeof body.capabilities === 'function' ? body.capabilities() : {};

    const native = this.caps.visemes === 'oculus' || this.caps.visemes === 'vrm';
    this.rig = new Rig(
      (name, weight) => body.setMorph(name, weight),
      {
        rng: this.rng.fork('rig'),
        applyViseme: native && typeof body.setViseme === 'function'
          ? (name, weight) => body.setViseme(name, weight) : null,
      },
    );
    this.lipsync = new LipSync(this.rig, this.rng.fork('lipsync'));
    this.gestures = new Gestures(body, this.rng.fork('gestures'));
    this.accents = new Accents();
    this.states = new PresenceStates();

    this._accentHead = { rx: 0, ry: 0, rz: 0, keepsEyes: true };
    this.lastGestureKey = null;
    this.decision = null;
    this.history = [];            // les dernieres decisions, pour le journal
    this.t = 0;
    this.frame = 0;

    this.recorder = new Recorder();
    if (options.record) this.recorder.start(this.rng.seed, { caps: this.caps });

    this.metrics = {
      frames: 0, performs: 0, speaks: 0,
      updateMs: 0, rigMs: 0, gesturesMs: 0, lipsyncMs: 0, bodyMs: 0,
      writesPerFrame: 0,
    };
  }

  // ── les entrees ─────────────────────────────────────────────────────────

  /** Une Performance — celle de `presence.Performance.as_json()`. */
  perform(raw) {
    const perf = sanitizePerformance(raw);
    this.recorder.input('perform', perf);
    this.metrics.performs += 1;

    const gaze = perf.gaze;
    const table = GAZE_HEAD[gaze] || GAZE_HEAD.user;
    this.rig.setExpression(perf.blendshapes, gaze, perf.expression, perf.hold_s,
                           perf.gaze_source, { rx: table.headRx, ry: table.headRy });
    this.rig.setSlowBlink(perf.expression === 'tired' || perf.posture === 'dormant');

    this.gestures.setPosture(perf.posture);
    this.gestures.setGaze(gaze);
    this.gestures.setAffect({
      tempo: perf.tempo, stillness: perf.stillness, gazeHold: perf.gaze_hold_s,
    });
    this.states.set(perf.state);

    // Rejouer le geste quand c'est une NOUVELLE decision, pas quand c'est la
    // meme decision renvoyee. `gesture_id` le dit ; un hote qui ne l'envoie pas
    // (plus ancien) retombe sur l'ancienne regle, le nom du geste.
    const gesture = perf.gesture;
    const key = perf.gesture_id ? `${perf.gesture_id}|${gesture}` : gesture;
    let played = null;
    let accent = null;
    if (key !== this.lastGestureKey) {
      played = this.gestures.play(gesture);
      if (perf.accent && this.accents.play(perf.accent, perf.tempo)) accent = perf.accent;
      this.lastGestureKey = key;
    }

    if (typeof perf.speech_level === 'number') this.lipsync.setLevel(perf.speech_level);

    this.decision = {
      t: this.t,
      state: perf.state || '',
      intent: perf.intent || null,
      expression: perf.expression,
      intensity: perf.intensity,
      gaze,
      gaze_source: perf.gaze_source,
      requested_gesture: perf.requested_gesture || gesture,
      gesture,
      // Ce que le moteur a FAIT du geste — pas ce qu'on lui a demande.
      gesture_played: played ? played.via : 'deja en cours',
      accent: perf.accent || null,
      accent_played: accent !== null,
      hold_s: perf.hold_s,
      reason: perf.reason || '',
    };
    this.history.push(this.decision);
    if (this.history.length > 50) this.history.shift();
    return this.decision;
  }

  /** Le niveau de la voix, ~25 fois par seconde. */
  speak(level) {
    this.recorder.input('speak', Number(level) || 0);
    this.metrics.speaks += 1;
    this.lipsync.setLevel(level);
  }

  /** Un viseme venu d'une vraie source de phonemes. */
  viseme(name, weight) {
    this.recorder.input('viseme', { name: String(name), weight: Number(weight) || 0 });
    this.lipsync.setViseme(name, weight);
  }

  /** Des valeurs imposees a la main — le labo. `null` rend la main. */
  setOverride(shapes) {
    this.recorder.input('override', shapes ? Object.assign({}, shapes) : null);
    this.rig.setOverride(shapes);
  }

  // ── une image ───────────────────────────────────────────────────────────

  update(rawDt) {
    const dt = Math.max(0, Math.min(Number(rawDt) || 0, MAX_DT));
    const started = clock();
    this.t += dt;
    this.frame += 1;
    this.recorder.frame(dt);

    const b = this.states.update(dt);
    this.rig.setBehaviour(b);
    this.gestures.idle.microRate = b.microRate;

    let mark = clock();
    this.lipsync.update(dt);
    const lipsyncMs = clock() - mark;

    this.accents.update(dt);

    mark = clock();
    this.gestures.gazeHead = this.rig.gazeCtl.headOut;
    this.gestures.gazeDecided = this.rig.gazeCtl.decided && this.rig.gaze === 'user';
    this.gestures.stateHead.rx = b.headRx;
    this.gestures.stateHead.rz = b.headRz;
    if (this.accents.active) {
      const h = this._accentHead;
      h.rx = this.accents.head.rx;
      h.ry = this.accents.head.ry;
      h.rz = this.accents.head.rz;
      // La tete basse de l'excuse emmene les yeux ; les autres accents gardent
      // le contact visuel.
      h.keepsEyes = this.accents.active.name !== 'head_down';
      this.gestures.accentHead = h;
    } else {
      this.gestures.accentHead = null;
    }
    this.gestures.update(dt);
    const gesturesMs = clock() - mark;

    mark = clock();
    const before = this.rig.writes;
    this.rig.setMicro(this.gestures.idle.shapes);
    this.rig.setAccent(this.accents.shapes);
    this.rig.setVor(this.gestures.vorHead.rx, this.gestures.vorHead.ry);
    this.rig.update(dt);
    const rigMs = clock() - mark;

    mark = clock();
    if (this.body && typeof this.body.update === 'function') this.body.update(dt);
    const bodyMs = clock() - mark;

    this.recorder.sample(this.frame, this.t, () => this.output());

    // Moyennes glissantes : une image lente isolee ne doit pas masquer la
    // tendance, ni la tendance une pointe qu'on voudrait voir.
    const m = this.metrics;
    const a = m.frames < 30 ? 1 / (m.frames + 1) : 0.05;
    m.frames += 1;
    m.updateMs += ((clock() - started) - m.updateMs) * a;
    m.rigMs += (rigMs - m.rigMs) * a;
    m.gesturesMs += (gesturesMs - m.gesturesMs) * a;
    m.lipsyncMs += (lipsyncMs - m.lipsyncMs) * a;
    m.bodyMs += (bodyMs - m.bodyMs) * a;
    m.writesPerFrame += ((this.rig.writes - before) - m.writesPerFrame) * a;
  }

  // ── ce qui sort ─────────────────────────────────────────────────────────

  /**
   * L'etat EFFECTIF : ce que le corps a recu, pas ce qu'on lui a demande.
   *
   * `shapes` sont les valeurs reellement ecrites (non nulles), `head` la
   * rotation reellement appliquee, `eyes` la direction reellement visee.
   */
  output() {
    const shapes = {};
    const written = this.rig.written;
    for (const name in written) if (written[name] > 0.001) shapes[name] = written[name];
    const visemes = {};
    for (const name in this.rig.writtenViseme) {
      if (this.rig.writtenViseme[name] > 0.001) visemes[name] = this.rig.writtenViseme[name];
    }
    const s = this.gestures.smoothed;
    return {
      t: this.t,
      frame: this.frame,
      presence: this.states.reported,
      shapes,
      visemes,
      head: { rx: s.headRx, ry: s.headRy, rz: s.headRz },
      body: { spineRx: s.spineRx, spineRy: s.spineRy, rootY: s.rootY, rootZ: s.rootZ, rootRy: s.rootRy },
      eyes: { x: this.rig.gazeCtl.out.x, y: this.rig.gazeCtl.out.y },
      gazeHead: { rx: this.rig.gazeCtl.headOut.rx, ry: this.rig.gazeCtl.headOut.ry },
      gesture: this.gestures.playing,
      accent: this.accents.name,
      blinking: this.rig.blinkPhase >= 0,
      speaking: this.lipsync.speaking,
      phase: this.rig.performance.phase,
    };
  }

  /** Pourquoi cette forme a cette valeur — chaque couche, et la gagnante. */
  explain(name) {
    return this.rig.explain(name);
  }
}
