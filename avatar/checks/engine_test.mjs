/**
 * engine_test.mjs — le vrai moteur, mesure par sa SORTIE, sous Node.
 *
 *     node avatar/checks/engine_test.mjs
 *
 * CE QUE CE FICHIER PROUVE, ET QUE LES AUTRES NE PEUVENT PAS
 *   `presence/selftest.py` lit les tables ; il ne peut pas executer le
 *   JavaScript. Les controles Qt (`avatar/checks/*.py`) l'executent, mais
 *   lentement, sur un seul modele, et avec un hasard qu'ils ne controlent pas.
 *
 *   Ici le moteur tourne tel quel — `engine.js`, `rig.js`, `gaze.js`, tout —
 *   sur `body_null.js`, un corps qui retient chaque valeur qu'on lui ecrit. La
 *   graine est fixee, le pas de temps aussi : chaque mesure est exacte et
 *   reproductible, et l'ensemble tourne en quelques secondes, sans GPU.
 *
 *   La regle est celle de `rootRy` : on ne verifie pas qu'un champ existe, on
 *   mesure ce qui arrive au corps.
 */

import { AvatarEngine, sanitizePerformance } from '../js/engine.js';
import { NullBody } from '../js/body_null.js';
import { replay } from '../js/recorder.js';
import { Director, parseDirective } from '../js/director.js';
import { Catalogue } from '../js/catalog.js';
import { INTENTS } from '../js/affect.js';

const DT = 1 / 60;
const DEG = 180 / Math.PI;
const results = [];

function check(name, fn) {
  try {
    const detail = fn();
    results.push([name, true, detail || 'ok']);
  } catch (err) {
    results.push([name, false, err && err.message ? err.message : String(err)]);
  }
}

function assert(cond, message) {
  if (!cond) throw new Error(message);
}

function face(spec = {}) {
  return new NullBody(Object.assign({
    parts: ['head', 'torso', 'arms', 'legs'],
    manifest: { rig: { motion: 'face' } },
  }, spec));
}

/** `n` images, en appelant `each(engine, i)` avant chacune. */
function run(engine, seconds, each) {
  const frames = Math.round(seconds / DT);
  for (let i = 0; i < frames; i++) {
    if (each) each(engine, i);
    engine.update(DT);
  }
}

/** Le temps pour qu'une valeur lue atteigne `fraction` de sa cible. */
function timeTo(engine, read, target, fraction, limit = 3) {
  for (let t = 0; t < limit; t += DT) {
    engine.update(DT);
    if (read(engine) >= target * fraction) return t + DT;
  }
  return Infinity;
}

const shape = (e, name) => e.body.morphs[name] || 0;

/** Une Performance comme le directeur Python l'enverrait. */
function perf(expression, blendshapes, extra = {}) {
  return Object.assign({ expression, intensity: 0.7, gesture: 'idle', gaze: 'user',
                         posture: 'relaxed', blendshapes, state: 'ACTIVE' }, extra);
}

// ── 1. le temps d'un visage ──────────────────────────────────────────────────

check('aucune valeur ne saute : un visage part a vitesse nulle', () => {
  // La signature d'un depart a vitesse nulle : la 1re image bouge MOINS que la
  // 2e. L'ancien lissage exponentiel faisait l'inverse — son plus grand pas
  // etait le premier, le coin que l'oeil voit comme un tressaillement.
  const steps = (name, shapes, key) => {
    const e = new AvatarEngine(face(), { seed: 1 });
    run(e, 1);
    e.perform(perf(name, shapes));
    const v = [shape(e, key)];
    for (let i = 0; i < 90; i++) { e.update(DT); v.push(shape(e, key)); }
    const d = v.slice(1).map((x, i) => x - v[i]);
    return { first: d[0], second: d[1], biggest: Math.max(...d.map(Math.abs)) };
  };
  const surprised = steps('surprised', { browInnerUp: 0.9, jawOpen: 0.45 }, 'browInnerUp');
  const thinking = steps('thinking', { browDownLeft: 0.45 }, 'browDownLeft');
  for (const [name, s] of [['surprised', surprised], ['thinking', thinking]]) {
    assert(s.first < s.second, `${name} : 1re image +${s.first.toFixed(3)} >= 2e +${s.second.toFixed(3)}`);
  }
  // La surprise EST rapide (90 % en 0.12 s) : son plus grand pas reste sous
  // celui de l'ancien lissage sur la meme cible (0.18).
  assert(surprised.biggest < 0.18, `surprise : pas de ${surprised.biggest.toFixed(3)}`);
  assert(thinking.biggest < 0.03, `pensee : pas de ${thinking.biggest.toFixed(3)}`);
  return `surprise +${surprised.first.toFixed(3)} puis +${surprised.second.toFixed(3)} (max ${surprised.biggest.toFixed(3)}) ; `
    + `pensee max ${thinking.biggest.toFixed(3)} par image`;
});

check('chaque visage a son rythme : surprise vive, pensee lente', () => {
  const t90 = (name, shapes, key) => {
    const e = new AvatarEngine(face(), { seed: 2 });
    run(e, 0.5);
    e.perform(perf(name, shapes));
    return timeTo(e, (x) => shape(x, key), shapes[key], 0.9);
  };
  const surprised = t90('surprised', { browInnerUp: 0.9 }, 'browInnerUp');
  const thinking = t90('thinking', { browDownLeft: 0.45 }, 'browDownLeft');
  const amused = t90('amused', { browOuterUpLeft: 0.45 }, 'browOuterUpLeft');
  assert(surprised < 0.2, `surprise a 90 % en ${surprised.toFixed(2)} s`);
  assert(thinking > 0.45, `pensee a 90 % en ${thinking.toFixed(2)} s — trop vive`);
  assert(amused > surprised && thinking > amused * 0.9, 'ordre des rythmes inattendu');
  return `90 % : surprised ${surprised.toFixed(2)} s · amused ${amused.toFixed(2)} s · thinking ${thinking.toFixed(2)} s`;
});

check('la surprise ne se tient pas : elle retombe a son residu', () => {
  const e = new AvatarEngine(face(), { seed: 3 });
  e.perform(perf('surprised', { browInnerUp: 0.9 }));
  run(e, 0.6);
  const peak = shape(e, 'browInnerUp');
  run(e, 2.0);
  const after = shape(e, 'browInnerUp');
  assert(peak > 0.8, `pic ${peak.toFixed(2)}`);
  assert(after < 0.9 * 0.45 && after > 0.9 * 0.25, `apres 2.6 s : ${after.toFixed(2)} (residu attendu ~0.32)`);
  return `pic ${peak.toFixed(2)} -> ${after.toFixed(2)} apres 2.6 s, sans nouvelle decision`;
});

check('l\'ironie se compose : les yeux avant la bouche', () => {
  const e = new AvatarEngine(face(), { seed: 4 });
  run(e, 0.3);
  e.perform(perf('amused', { browOuterUpLeft: 0.45, mouthSmileLeft: 0.55 }));
  let brow = null; let mouth = null;
  for (let t = 0; t < 2; t += DT) {
    e.update(DT);
    if (brow === null && shape(e, 'browOuterUpLeft') >= 0.225) brow = t;
    if (mouth === null && shape(e, 'mouthSmileLeft') >= 0.275) mouth = t;
  }
  assert(brow !== null && mouth !== null && mouth - brow > 0.1,
    `bouche ${mouth} vs sourcils ${brow}`);
  return `mi-parcours : sourcils ${brow.toFixed(2)} s, bouche ${mouth.toFixed(2)} s`;
});

check('hold_s relache le visage reflexe, et seulement lui', () => {
  const e = new AvatarEngine(face(), { seed: 5 });
  e.perform(perf('concerned', { browInnerUp: 0.7 }, { hold_s: 0.5 }));
  run(e, 0.6);
  const held = shape(e, 'browInnerUp');
  run(e, 2.0);
  const released = shape(e, 'browInnerUp');
  assert(held > 0.5 && released < 0.05, `tenu ${held.toFixed(2)}, relache ${released.toFixed(2)}`);
  return `tenu ${held.toFixed(2)} -> ${released.toFixed(3)}`;
});

// ── 2. la vie ────────────────────────────────────────────────────────────────

check('sans aucune decision, le visage vit : clignements, saccades, micro', () => {
  const e = new AvatarEngine(face(), { seed: 6 });
  let saccades = 0; let last = null; let micro = 0;
  run(e, 20, (x) => {
    const p = x.rig.gazeCtl.saccade;
    const point = `${p.tx.toFixed(4)},${p.ty.toFixed(4)}`;
    if (point !== last) { saccades += 1; last = point; }
    if (Object.keys(x.gestures.idle.shapes).length) micro += 1;
  });
  const blinks = e.rig.blinks;
  assert(blinks >= 3, `${blinks} clignements en 20 s`);
  assert(saccades >= 10, `${saccades} saccades`);
  assert(micro > 0, 'aucune micro-expression');
  return `20 s sans decision : ${blinks} clignements, ${saccades} saccades, micro sur ${micro} images`;
});

check('le repos se tait devant une decision forte, et jamais a sa place', () => {
  const e = new AvatarEngine(face(), { seed: 7 });
  e.perform(perf('sad', { browInnerUp: 0.8, mouthFrownLeft: 0.6, mouthFrownRight: 0.6 }));
  let overwrote = 0; let maxGain = 0;
  run(e, 20, (x, i) => {
    if (i > 90) maxGain = Math.max(maxGain, x.rig.microGain);
    for (const name in x.gestures.idle.shapes) {
      if ((x.rig.expression[name] || 0) > 0.05) {
        const why = x.explain(name);
        if (why.winner === 'micro') overwrote += 1;
      }
    }
  });
  assert(overwrote === 0, `${overwrote} images ou le repos a gagne sur l'expression`);
  assert(maxGain < 0.5, `gain du repos ${maxGain.toFixed(2)} sous un visage fort`);
  return `gain du repos plafonne a ${maxGain.toFixed(2)} sous sad 0.8 ; 0 ecrasement`;
});

check('les clignements different selon l\'etat, et sont parfois doubles', () => {
  const count = (state) => {
    const e = new AvatarEngine(face(), { seed: 8 });
    e.perform(perf('neutral', {}, { state }));
    run(e, 120);
    return e.rig.blinks;
  };
  const speaking = count('SPEAKING');
  const listening = count('LISTENING');
  assert(speaking > listening * 1.3, `parle ${speaking} vs ecoute ${listening} sur 2 min`);
  const e = new AvatarEngine(face(), { seed: 9 });
  e.perform(perf('neutral', {}, { state: 'SPEAKING' }));
  let doubles = 0;
  run(e, 120, (x) => { if (x.rig.pendingDouble > 0 && x.rig.pendingDouble < DT * 1.01) doubles += 1; });
  assert(doubles > 0, 'aucun clignement double en 2 min de parole');
  return `2 min : ${speaking} en parlant, ${listening} en ecoutant ; ${doubles} doubles`;
});

// ── 3. le regard ─────────────────────────────────────────────────────────────

check('les yeux partent d\'abord, la tete suit, les yeux se posent', () => {
  const e = new AvatarEngine(face(), { seed: 10 });
  run(e, 1);
  e.perform(perf('neutral', { eyeLookInLeft: 0.55, eyeLookOutRight: 0.55 },
                 { gaze: 'screen', gaze_source: 'intent' }));
  const headTarget = e.rig.gazeCtl.headTarget.ry;
  let eyeHalf = null; let headHalf = null; let peak = 0;
  for (let t = 0; t < 2; t += DT) {
    e.update(DT);
    const eye = e.rig.gazeCtl.out.x;
    peak = Math.max(peak, eye);
    if (eyeHalf === null && eye >= 0.275) eyeHalf = t;
    if (headHalf === null && e.rig.gazeCtl.headOut.ry >= headTarget / 2) headHalf = t;
  }
  const settled = e.rig.gazeCtl.out.x;
  assert(eyeHalf !== null && headHalf !== null && headHalf - eyeHalf > 0.08,
    `yeux ${eyeHalf} s, tete ${headHalf} s`);
  assert(peak > settled + 0.1, `les yeux ne depassent pas : pic ${peak.toFixed(2)}, pose ${settled.toFixed(2)}`);
  assert(Math.abs(settled - 0.55) < 0.1, `pose finale ${settled.toFixed(2)} au lieu de 0.55`);
  return `mi-course : yeux ${(eyeHalf * 1000).toFixed(0)} ms, tete ${(headHalf * 1000).toFixed(0)} ms ; `
    + `yeux ${peak.toFixed(2)} -> ${settled.toFixed(2)}`;
});

check('en hochant la tete, les yeux restent sur l\'utilisateur', () => {
  const e = new AvatarEngine(face(), { seed: 11 });
  run(e, 1);
  e.perform(perf('neutral', {}, { gesture: 'nod', gesture_id: 'intent#1', gaze_source: 'intent' }));
  let headSpan = [0, 0]; let eyeCounter = 0;
  run(e, 0.8, (x) => {
    const rx = x.gestures.smoothed.headRx;
    headSpan = [Math.min(headSpan[0], rx), Math.max(headSpan[1], rx)];
    // La tete baisse (+rx) -> les yeux montent (+y) : le signe doit suivre.
    if (Math.abs(rx) > 0.05) eyeCounter += Math.sign(rx) === Math.sign(x.rig.gazeCtl.out.y) ? 1 : 0;
  });
  const span = (headSpan[1] - headSpan[0]) * DEG;
  assert(span > 8, `le hochement n'a pas eu lieu (${span.toFixed(1)} deg)`);
  assert(eyeCounter > 5, 'les yeux ne compensent pas le hochement');
  return `hochement ${span.toFixed(1)} deg, yeux compensant sur ${eyeCounter} images`;
});

check('un regard decide n\'est jamais deplace par le fond ; un regard derive, si', () => {
  const wander = (source) => {
    const e = new AvatarEngine(face(), { seed: 12 });
    e.perform(perf('neutral', {}, { state: 'SPEAKING', gaze_source: source }));
    let far = 0;
    run(e, 30, (x) => { far = Math.max(far, Math.abs(x.rig.gazeCtl.glance.x)); });
    return far;
  };
  const decided = wander('explicit');
  const derived = wander('reflex');
  assert(decided === 0, `un regard explicite a ete detourne (${decided.toFixed(2)})`);
  assert(derived > 0.1, `aucun coup d'oeil en parlant (${derived.toFixed(2)})`);
  return `30 s de parole : explicite 0 ecart, derive jusqu'a ${derived.toFixed(2)}`;
});

check('investigate + gaze user : la tete se detourne, les yeux restent sur lui', () => {
  const play = (source) => {
    const e = new AvatarEngine(face(), { seed: 27 });
    run(e, 0.5);
    e.perform(perf('thinking', {}, { gesture: 'look_away', gesture_id: 'intent#1', gaze: 'user', gaze_source: source }));
    let head = 0; let eyes = 0;
    run(e, 1.2, (x) => {
      head = Math.min(head, x.gestures.smoothed.headRy);
      eyes = Math.max(eyes, x.rig.gazeCtl.out.x);
    });
    return { head: head * DEG, eyes };
  };
  const explicit = play('explicit');
  const derived = play('reflex');
  assert(explicit.head < -10, `la tete ne s'est pas detournee (${explicit.head.toFixed(1)} deg)`);
  assert(explicit.eyes > 0.3, `regard explicite : les yeux suivent la tete (${explicit.eyes.toFixed(2)})`);
  assert(derived.eyes < 0.15, `regard derive : les yeux restent fixes (${derived.eyes.toFixed(2)})`);
  return `tete ${explicit.head.toFixed(1)} deg ; yeux compensent jusqu'a ${explicit.eyes.toFixed(2)} `
    + `(explicite) vs ${derived.eyes.toFixed(2)} (derive, ils suivent la tete)`;
});

// ── 4. la tete ───────────────────────────────────────────────────────────────

check('la tete additionne repos, etat et geste, sans jamais s\'accumuler', () => {
  const e = new AvatarEngine(face(), { seed: 13 });
  e.perform(perf('neutral', {}, { state: 'LISTENING' }));
  run(e, 2);
  const tilt = e.gestures.smoothed.headRz * DEG;
  const gestures = ['nod', 'shake_head', 'tilt_head', 'look_away', 'look_around'];
  let worst = 0;
  for (let i = 0; i < 60; i++) {
    e.perform(perf('neutral', {}, { state: 'LISTENING', gesture: gestures[i % 5], gesture_id: `intent#${i}` }));
    run(e, 0.5, (x) => {
      const s = x.gestures.smoothed;
      worst = Math.max(worst, Math.abs(s.headRx), Math.abs(s.headRy), Math.abs(s.headRz));
    });
  }
  run(e, 6);
  const back = e.gestures.smoothed.headRz * DEG;
  assert(tilt > 1.2, `inclinaison d'ecoute ${tilt.toFixed(1)} deg`);
  assert(worst * DEG < 30, `tete a ${(worst * DEG).toFixed(1)} deg : ca s'accumule`);
  assert(Math.abs(back - tilt) < 2.5, `retour a ${back.toFixed(1)} au lieu de ~${tilt.toFixed(1)}`);
  return `ecoute ${tilt.toFixed(1)} deg ; 60 gestes enchaines, max ${(worst * DEG).toFixed(1)} deg, retour ${back.toFixed(1)} deg`;
});

check('mode visage : rien ne bouge sous la nuque, et le geste gele le DIT', () => {
  const e = new AvatarEngine(face(), { seed: 14 });
  const d = e.perform(perf('neutral', {}, { gesture: 'shrug', gesture_id: 'intent#1' }));
  let body = 0;
  run(e, 3, (x) => {
    const s = x.gestures.smoothed;
    body = Math.max(body, Math.abs(s.spineRx), Math.abs(s.spineRy), Math.abs(s.rootY), Math.abs(s.rootZ), Math.abs(s.rootRy));
  });
  assert(d.gesture_played === 'frozen', `shrug rapporte « ${d.gesture_played} »`);
  assert(body < 1e-3, `le corps a bouge de ${body}`);
  const full = new AvatarEngine(face({ manifest: { rig: { motion: 'full' } } }), { seed: 14 });
  const f = full.perform(perf('neutral', {}, { gesture: 'shrug', gesture_id: 'intent#1' }));
  assert(f.gesture_played === 'procedural', `en mode complet shrug rapporte ${f.gesture_played}`);
  return 'face : shrug gele et declare gele ; full : shrug joue';
});

check('motion full : un idle installe tourne en fond, un geste s\'y fond et y revient', () => {
  // Un mixer simule : on teste le CABLAGE du moteur (qui joue quoi, en
  // boucle ou non, fondu depuis quoi), pas three.js.
  const log = [];
  const listeners = {};
  class Action {
    constructor(name) { this.name = name; this.loop = null; }
    setLoop(mode) { this.loop = mode; return this; }
    reset() { log.push(`reset ${this.name}`); return this; }
    play() { log.push(`play ${this.name}`); return this; }
    crossFadeFrom(other, t) { log.push(`fade ${other.name}->${this.name} ${t}`); return this; }
  }
  const actions = {};
  const mixer = {
    clipAction: (clip) => (actions[clip.name] = actions[clip.name] || new Action(clip.name)),
    addEventListener: (type, fn) => { listeners[type] = fn; },
    update: () => {},
    timeScale: 1,
  };
  const body = face({ manifest: { rig: { motion: 'full' } } });
  body.mixer = mixer;
  body.clips = { idle: { name: 'idle' }, wave: { name: 'wave' } };
  const e = new AvatarEngine(body, { seed: 28 });
  assert(actions.idle && actions.idle.loop === 2201, 'l\'idle ne tourne pas en boucle');
  const played = e.perform(perf('happy', {}, { gesture: 'wave', gesture_id: 'intent#1' }));
  assert(played.gesture_played === 'clip', `wave : ${played.gesture_played}`);
  assert(actions.wave.loop === 2200, 'le geste boucle au lieu de jouer une fois');
  assert(log.includes('fade idle->wave 0.25'), `pas de fondu depuis l'idle : ${log}`);
  listeners.finished({ action: actions.wave });
  assert(log.includes('fade wave->idle 0.35'), `pas de retour a l'idle : ${log}`);
  const faceOnly = new AvatarEngine(Object.assign(face(), { mixer, clips: body.clips }), { seed: 28 });
  assert(faceOnly.gestures.base === null, 'en mode visage, l\'idle du corps tourne quand meme');
  return 'idle en boucle ; wave fondu depuis lui (0.25 s) puis retour (0.35 s) ; rien en mode visage';
});

// ── 5. la parole ─────────────────────────────────────────────────────────────

function speakFor(e, seconds, levelAt) {
  const frames = Math.round(seconds / DT);
  for (let i = 0; i < frames; i++) {
    e.speak(levelAt(i * DT));
    e.update(DT);
  }
}
const voice = (t) => Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3 + 0.5)) * 0.8;

check('le sourire survit a la parole, la bouche lui appartient quand meme', () => {
  const e = new AvatarEngine(face(), { seed: 15 });
  e.perform(perf('amused', { mouthSmileLeft: 0.55, mouthSmileRight: 0.28 }, { state: 'SPEAKING' }));
  run(e, 1.5);
  const smile = shape(e, 'mouthSmileLeft');
  let minSmile = 1; let jawMax = 0;
  speakFor(e, 3, (t) => {
    minSmile = Math.min(minSmile, shape(e, 'mouthSmileLeft'));
    jawMax = Math.max(jawMax, shape(e, 'jawOpen'));
    return voice(t);
  });
  assert(jawMax > 0.3, `la bouche ne s'ouvre pas (${jawMax.toFixed(2)})`);
  assert(minSmile > smile * 0.7, `sourire ${smile.toFixed(2)} tombe a ${minSmile.toFixed(2)} en parlant`);
  return `sourire ${smile.toFixed(2)} -> jamais sous ${minSmile.toFixed(2)} ; machoire jusqu'a ${jawMax.toFixed(2)}`;
});

check('une machoire ouverte par l\'emotion laisse la parole fermer la bouche', () => {
  const e = new AvatarEngine(face(), { seed: 16 });
  e.perform(perf('surprised', { jawOpen: 0.45, browInnerUp: 0.9 }));
  run(e, 0.5);
  const open = shape(e, 'jawOpen');
  let minJaw = 1;
  speakFor(e, 3, (t) => { if (t > 0.5) minJaw = Math.min(minJaw, shape(e, 'jawOpen')); return voice(t); });
  assert(minJaw < open * 0.5, `machoire jamais sous ${minJaw.toFixed(2)} (emotion ${open.toFixed(2)})`);
  return `emotion ${open.toFixed(2)} ; pendant la parole la machoire descend a ${minJaw.toFixed(2)}`;
});

check('fin de phrase, interruption : la bouche se referme sans claquer', () => {
  const e = new AvatarEngine(face(), { seed: 17 });
  e.perform(perf('neutral', {}, { state: 'SPEAKING' }));
  speakFor(e, 1.2, (t) => 0.7 + 0.2 * Math.sin(t * 20));  // coupee en pleine voyelle
  const open = shape(e, 'jawOpen');
  let biggest = 0; let last = open; let closedAt = null;
  for (let t = 0; t < 1.5; t += DT) {
    e.speak(0);
    e.update(DT);
    const jaw = shape(e, 'jawOpen');
    biggest = Math.max(biggest, last - jaw);
    last = jaw;
    if (closedAt === null && jaw < 0.02) closedAt = t;
  }
  assert(open > 0.3, `la bouche n'etait pas ouverte (${open.toFixed(2)})`);
  assert(biggest < 0.15, `fermeture brutale : -${biggest.toFixed(2)} en une image`);
  assert(closedAt !== null && closedAt > 0.1, `fermee en ${closedAt} s`);
  return `ouverte ${open.toFixed(2)}, fermee en ${(closedAt * 1000).toFixed(0)} ms, pire image -${biggest.toFixed(2)}`;
});

check('changer d\'emotion en parlant ne fait rien sauter', () => {
  const e = new AvatarEngine(face(), { seed: 18 });
  e.perform(perf('amused', { mouthSmileLeft: 0.55, browOuterUpLeft: 0.45 }, { state: 'SPEAKING' }));
  let biggest = 0; const last = {};
  let switched = false;
  speakFor(e, 3, (t) => {
    if (!switched && t > 1.5) {
      e.perform(perf('concerned', { browInnerUp: 0.7, mouthFrownLeft: 0.28, mouthPressLeft: 0.35 }, { state: 'SPEAKING' }));
      switched = true;
    }
    // La couche EMOTION, lue dans le rig : la bouche, elle, bouge a la vitesse
    // des syllabes, et c'est voulu. Ce qu'on teste est le changement de visage.
    for (const name of ['mouthSmileLeft', 'browInnerUp', 'mouthFrownLeft', 'browOuterUpLeft']) {
      const v = e.explain(name).layers.expression;
      if (last[name] !== undefined) biggest = Math.max(biggest, Math.abs(v - last[name]));
      last[name] = v;
    }
    return voice(t);
  });
  assert(biggest < 0.1, `saut de ${biggest.toFixed(3)} en une image`);
  return `amused -> concerned en pleine phrase, plus grand saut ${biggest.toFixed(3)}`;
});

check('un modele a visemes natifs parle avec les siens', () => {
  const e = new AvatarEngine(face({ visemes: 'oculus' }), { seed: 19 });
  e.perform(perf('neutral', {}, { state: 'SPEAKING' }));
  let native = 0; let arkitJaw = 0;
  speakFor(e, 2, (t) => {
    native = Math.max(native, ...Object.values(e.body.visemes), 0);
    arkitJaw = Math.max(arkitJaw, shape(e, 'jawOpen'));
    return voice(t);
  });
  assert(native > 0.3, `visemes natifs a ${native.toFixed(2)}`);
  assert(arkitJaw < 0.01, `l'approximation ARKit est aussi ecrite (jawOpen ${arkitJaw.toFixed(2)}) : double machoire`);
  const names = Object.keys(e.body.visemes).sort().join(',');
  return `visemes natifs jusqu'a ${native.toFixed(2)} (${names}), aucune approximation ARKit`;
});

// ── 6. priorites ─────────────────────────────────────────────────────────────

check('priorites : surete > explicite > parole > intention > emotion > repos', () => {
  const e = new AvatarEngine(face(), { seed: 20 });
  e.perform(perf('happy', { mouthSmileLeft: 0.85, browInnerUp: 0.15 }, { accent: 'brow_flash', gesture_id: 'intent#1' }));
  run(e, 0.25);
  const flash = e.explain('browInnerUp');
  assert(flash.winner === 'accent', `pendant le flash, browInnerUp gagne par ${flash.winner}`);

  e.setOverride({ mouthSmileLeft: 0.1, eyeBlinkLeft: 0 });
  run(e, 0.1);
  assert(Math.abs(shape(e, 'mouthSmileLeft') - 0.1) < 1e-9, 'l\'explicite ne gagne pas');

  e.perform(perf('tired', { eyeBlinkLeft: 1 }, { gaze: 'closed', gaze_source: 'safety' }));
  run(e, 0.1);
  assert(shape(e, 'eyeBlinkLeft') === 1, 'la surete (yeux fermes) ne gagne pas sur l\'explicite');
  e.setOverride(null);

  // Un clignement ne touche jamais la bouche.
  const b = new AvatarEngine(face(), { seed: 21 });
  b.perform(perf('neutral', {}, { state: 'SPEAKING' }));
  let mouthDuringBlink = true;
  speakFor(b, 6, (t) => {
    if (b.rig.blinkPhase >= 0 && b.explain('jawOpen').winner === 'lid') mouthDuringBlink = false;
    return voice(t);
  });
  assert(mouthDuringBlink, 'un clignement a gagne sur la bouche');
  return 'accent > emotion, explicite > tout, surete > explicite, clignement hors de la bouche';
});

check('NaN, infinis, valeurs hors bornes : rien n\'atteint le corps', () => {
  const e = new AvatarEngine(face(), { seed: 22 });
  e.perform({ expression: 42, intensity: 'fort', gaze: null, tempo: NaN, stillness: -Infinity,
              blendshapes: { browInnerUp: NaN, jawOpen: 7, mouthSmileLeft: -3, eyeWideLeft: 'x',
                             eyeLookInLeft: Infinity } });
  e.speak(NaN); e.speak(Infinity); e.viseme('XX', Infinity); e.viseme('aa', NaN);
  e.setOverride({ browDownLeft: NaN, cheekPuff: 5 });
  run(e, 2, (x, i) => { if (i % 7 === 0) x.speak(i % 2 ? -1 : 'loud'); });
  const bad = Object.entries(e.body.morphs).filter(([, v]) => !Number.isFinite(v) || v < 0 || v > 1);
  const head = Object.values(e.gestures.smoothed).filter((v) => !Number.isFinite(v));
  assert(!bad.length, `valeurs invalides ecrites : ${JSON.stringify(bad)}`);
  assert(!head.length, 'rotation de tete invalide');
  assert(shape(e, 'jawOpen') <= 1 && shape(e, 'cheekPuff') === 1, 'bornes non appliquees');
  const s = sanitizePerformance({ blendshapes: { a: NaN, b: 2 } });
  assert(!('a' in s.blendshapes) && s.blendshapes.b === 1, 'sanitizePerformance');
  return `${Object.keys(e.body.morphs).length} formes ecrites, toutes finies et dans [0, 1]`;
});

// ── 7. une decision, un geste ────────────────────────────────────────────────

check('une decision rejoue son geste, une re-resolution jamais', () => {
  const e = new AvatarEngine(face(), { seed: 23 });
  const plays = [];
  const original = e.gestures.play.bind(e.gestures);
  e.gestures.play = (name) => { plays.push(name); return original(name); };
  const nod = (id) => e.perform(perf('neutral', {}, { gesture: 'nod', gesture_id: id }));
  nod('reflex:SPEAKING');
  nod('reflex:SPEAKING');       // meme decision renvoyee
  nod('intent#1');              // agree pendant la parole : meme nom, nouvelle decision
  nod('intent#1');              // re-resolution au changement d'etat
  nod('intent#2');              // acknowledge : un deuxieme hochement
  e.perform(perf('neutral', {}, { gesture: 'nod' }));            // hote ancien, sans id
  e.perform(perf('neutral', {}, { gesture: 'nod' }));
  assert(plays.length === 4, `${plays.length} hochements joues au lieu de 4 : ${plays}`);
  return 'reflexe, agree, acknowledge, hote ancien : 4 hochements, 3 renvois ignores';
});

// ── 8. les seize intentions, par la sortie ───────────────────────────────────

/** Une intention jouee de bout en bout : demande -> miroir du directeur -> moteur. */
function playIntent(intent, motion) {
  const body = face({ manifest: { rig: { motion } } });
  const director = new Director(new Catalogue([...body.detectedParts], motion, []));
  const e = new AvatarEngine(body, { seed: 24 });
  run(e, 0.5);
  director.setIntent(parseDirective({ intent }), 0);
  const p = director.resolve('SPEAKING', { now: 0.1 });
  const decision = e.perform(p);
  const peak = { brow: 0, rxUp: 0, rxDown: 0, ry: 0, rz: 0 };
  run(e, 2.2, (x) => {
    const s = x.gestures.smoothed;
    peak.brow = Math.max(peak.brow, x.body.morphs.browOuterUpLeft || 0);
    peak.rxUp = Math.min(peak.rxUp, s.headRx);
    peak.rxDown = Math.max(peak.rxDown, s.headRx);
    peak.ry = Math.max(peak.ry, Math.abs(s.headRy));
    peak.rz = Math.max(peak.rz, Math.abs(s.headRz));
  });
  const out = e.output();
  return { perf: p, decision, out, peak };
}

check('les seize intentions jouent, et se distinguent a la SORTIE (mode visage)', () => {
  const rows = {};
  for (const intent of Object.keys(INTENTS)) rows[intent] = playIntent(intent, 'face');
  for (const [name, r] of Object.entries(rows)) {
    assert(r.decision.gesture_played === 'procedural', `${name} : geste ${r.perf.gesture} ${r.decision.gesture_played}`);
    if (r.perf.accent) assert(r.decision.accent_played, `${name} : accent ${r.perf.accent} non joue`);
    if (r.perf.gaze === 'screen') assert(r.out.eyes.x > 0.3, `${name} : yeux a ${r.out.eyes.x.toFixed(2)}`);
    if (r.perf.gaze === 'down') assert(r.out.eyes.y < -0.3, `${name} : yeux a ${r.out.eyes.y.toFixed(2)}`);
  }
  // Signature observable : visage (formes), tete (pics), yeux.
  const feature = (r) => {
    const f = {};
    for (const [k, v] of Object.entries(r.out.shapes)) if (!k.startsWith('eyeBlink')) f[k] = v;
    f.__rxUp = r.peak.rxUp * 3; f.__rxDown = r.peak.rxDown * 3; f.__ry = r.peak.ry * 3;
    f.__rz = r.peak.rz * 3; f.__brow = r.peak.brow;
    return f;
  };
  const names = Object.keys(rows);
  let closest = [Infinity, ''];
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      const a = feature(rows[names[i]]); const b = feature(rows[names[j]]);
      let d = 0;
      for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) d += ((a[k] || 0) - (b[k] || 0)) ** 2;
      d = Math.sqrt(d);
      if (d < closest[0]) closest = [d, `${names[i]}/${names[j]}`];
    }
  }
  assert(closest[0] > 0.08, `${closest[1]} a ${closest[0].toFixed(3)} : indiscernables a l'ecran`);
  const g = rows.greet; const s = rows.report_success;
  assert(g.peak.brow > s.peak.brow + 0.2, 'greet ne leve pas les sourcils plus que report_success');
  assert(s.peak.rxUp < g.peak.rxUp - 0.03, 'report_success ne releve pas le menton');
  return `16/16 jouees ; paire la plus proche ${closest[1]} a ${closest[0].toFixed(2)}`;
});

check('les memes seize en mode complet : le corps s\'ajoute, le cerveau n\'a rien change', () => {
  let bodyMoved = 0;
  for (const intent of Object.keys(INTENTS)) {
    const r = playIntent(intent, 'full');
    const faceRun = playIntent(intent, 'face');
    assert(r.perf.expression === faceRun.perf.expression && r.perf.gaze === faceRun.perf.gaze,
      `${intent} : le visage change avec le corps`);
    if (r.perf.gesture !== faceRun.perf.gesture) bodyMoved += 1;
  }
  assert(bodyMoved >= 5, `seulement ${bodyMoved} intentions gagnent un geste de corps`);
  return `${bodyMoved} intentions gagnent un geste de buste, visage et regard identiques`;
});

// ── 9. rejouer ───────────────────────────────────────────────────────────────

check('une seance se rejoue a l\'identique, et une autre graine diverge', () => {
  const session = (engine) => {
    engine.perform(perf('amused', { mouthSmileLeft: 0.5, browOuterUpLeft: 0.4 },
                        { state: 'SPEAKING', gesture: 'nod', gesture_id: 'intent#1', accent: 'brow_flash' }));
    speakFor(engine, 3, voice);
    engine.perform(perf('thinking', { browDownLeft: 0.45, eyeLookUpLeft: 0.35, eyeLookUpRight: 0.35 },
                        { state: 'THINKING', gaze: 'away', gesture: 'tilt_head', gesture_id: 'intent#2' }));
    engine.setOverride({ cheekPuff: 0.3 });
    run(engine, 4, (x, i) => { if (i % 3 === 0) x.speak(0); });
    engine.setOverride(null);
    run(engine, 3);
  };
  // Pas de temps irreguliers : une vraie seance n'est jamais a 60 Hz pile.
  const live = new AvatarEngine(face(), { seed: 777, record: true });
  const jitter = live.update.bind(live);
  let k = 0;
  live.update = (dt) => jitter(dt * (0.7 + 0.6 * ((k++ * 7919) % 100) / 100));
  session(live);
  const recording = JSON.parse(JSON.stringify(live.recorder.export()));

  const again = replay(recording, new AvatarEngine(face(), { seed: recording.seed }));
  assert(again.mismatches.length === 0, `rejeu : ${again.mismatches.length} ecarts, ${again.mismatches[0] && again.mismatches[0].diff}`);
  const other = replay(recording, new AvatarEngine(face(), { seed: recording.seed + 1 }));
  assert(other.mismatches.length > 0, 'une autre graine donne la meme seance : le hasard n\'est pas celui du moteur');
  return `${again.frames} images, ${recording.samples.length} echantillons : 0 ecart ; autre graine : ${other.mismatches.length} ecarts`;
});

check('l\'etat de presence passe par « transitioning »', () => {
  const e = new AvatarEngine(face(), { seed: 25 });
  e.perform(perf('neutral', {}, { state: 'LISTENING' }));
  run(e, 1);
  e.perform(perf('neutral', {}, { state: 'SPEAKING' }));
  e.update(DT);
  const during = e.states.reported;
  run(e, 1);
  assert(during === 'transitioning' && e.states.reported === 'speaking', `${during} -> ${e.states.reported}`);
  return 'listening -> transitioning -> speaking';
});

// ── 10. le cout ──────────────────────────────────────────────────────────────

check('le cout d\'une image, et ce qui est reellement ecrit', () => {
  const e = new AvatarEngine(face(), { seed: 26 });
  e.perform(perf('amused', { mouthSmileLeft: 0.5, browOuterUpLeft: 0.4 }, { state: 'LISTENING' }));
  run(e, 2);
  const writes = e.body.writes;
  run(e, 10);
  const perFrame = (e.body.writes - writes) / 600;
  const start = process.hrtime.bigint();
  speakFor(e, 60, voice);
  const ms = Number(process.hrtime.bigint() - start) / 1e6 / 3600;
  assert(ms < 0.5, `${ms.toFixed(3)} ms par image`);
  assert(perFrame < 20, `${perFrame.toFixed(1)} ecritures par image au repos`);
  return `${(ms * 1000).toFixed(0)} us par image en parlant ; ${perFrame.toFixed(1)} ecritures/image au repos (52+ avant)`;
});

// ── rapport ──────────────────────────────────────────────────────────────────

const width = Math.max(...results.map(([n]) => n.length));
console.log('\n  avatar/ moteur — sortie mesuree sous Node\n');
for (const [name, ok, detail] of results) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name.padEnd(width)}  ${detail}`);
}
const failed = results.filter(([, ok]) => !ok).length;
console.log(`\n  ${results.length - failed}/${results.length} passed\n`);
process.exit(failed ? 1 : 0);
