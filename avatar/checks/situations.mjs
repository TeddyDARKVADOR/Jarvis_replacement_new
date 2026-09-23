/**
 * situations.mjs — chaque moment ordinaire du labo, joue et mesure.
 *
 *     node avatar/checks/situations.mjs
 *
 * `avatar/js/situations.js` ecrit des moments — quelqu'un arrive, un resultat
 * etonne, l'utilisateur remercie — et, pour chacun, ce qu'un observateur
 * devrait percevoir. Le labo les joue a l'ecran. Ici ils passent par le vrai
 * directeur et le vrai moteur, a la cadence de l'hote, et la phrase `expect`
 * devient une mesure sur ce que le corps a recu. Un scenario de labo qui
 * cesserait de faire ce qu'il annonce tomberait ici.
 */

import { AvatarEngine } from '../js/engine.js';
import { NullBody } from '../js/body_null.js';
import { Director, parseDirective } from '../js/director.js';
import { Catalogue } from '../js/catalog.js';
import { EYE_REACH_RAD } from '../js/gaze.js';
import { SITUATIONS, durationOf, voiceAt, micAt } from '../js/situations.js';
import { VISEMES } from '../js/visemes.js';
import { FROM_OCULUS } from '../js/lipsync.js';

/** L'ouverture de machoire que des visemes natifs impliquent (voir engine_test). */
function jawOfVisemes(visemes) {
  let jaw = 0;
  for (const name in visemes) {
    const ours = FROM_OCULUS[name];
    jaw += (visemes[name] || 0) * ((ours && VISEMES[ours] && VISEMES[ours].jawOpen) || 0);
  }
  return jaw;
}

const DT = 1 / 60;
const DEG = 180 / Math.PI;

/** Jouer une situation ; rendre, image par image, ce qui est sorti. */
function play(name, seed = 3) {
  const s = SITUATIONS[name];
  const body = new NullBody({ parts: ['head', 'torso', 'arms', 'legs'], visemes: 'oculus',
                              manifest: { rig: { motion: 'face' } } });
  const e = new AvatarEngine(body, { seed });
  const d = new Director(new Catalogue(['head', 'torso', 'arms', 'legs'], 'face', []));
  let state = 'ACTIVE';
  e.perform(d.resolve(state, { now: 0 }));
  const steps = [...s.steps].sort((a, b) => a.t - b.t);
  let i = 0;
  let lastPush = 0;
  const frames = [];
  const decisions = [];
  const end = durationOf(s);
  for (let f = 0; f * DT < end; f++) {
    const t = f * DT;
    while (i < steps.length && steps[i].t <= t) {
      const step = steps[i++];
      if (step.state) state = step.state;
      if (step.ask) d.setIntent(parseDirective(step.ask), t);
      if (step.state || step.ask) {
        decisions.push({ t, d: e.perform(d.resolve(state, { now: t })) });
        lastPush = t;
      }
    }
    if (t - lastPush >= 1.5) { e.perform(d.resolve(state, { now: t })); lastPush = t; }
    if (f % 2 === 0) e.speak(voiceAt(steps, t));
    if (f % 4 === 0 && (state === 'LISTENING' || state === 'CONFIRM')) e.listen(micAt(steps, t));
    e.update(DT);
    const g = e.gestures.smoothed;
    const eye = e.rig.gazeCtl.out;
    frames.push({
      t, state,
      shapes: Object.assign({}, body.morphs),
      head: { rx: g.headRx, ry: g.headRy, rz: g.headRz },
      gesture: Object.assign({}, e.gestures.gestureHead),
      world: { x: eye.x * EYE_REACH_RAD + g.headRy, y: eye.y * EYE_REACH_RAD - g.headRx },
      eyes: { x: eye.x, y: eye.y },
      speaking: e.lipsync.speaking,
      blink: e.rig.blinkPhase >= 0,
      accent: e.accents.name,
      // L'ouverture REELLE de la bouche : un corps a visemes natifs parle par
      // eux, pas par `jawOpen` (voir lipsync.js).
      jaw: Math.max(body.morphs.jawOpen || 0, jawOfVisemes(body.visemes)),
      nods: e.conversation.stats.backchannels,
    });
  }
  return { s, e, frames, decisions };
}

const between = (frames, a, b) => frames.filter((x) => x.t >= a && x.t < b);
const peak = (frames, fn) => Math.max(0, ...frames.map(fn));
const low = (frames, fn) => Math.min(...frames.map(fn));
const sh = (x, n) => x.shapes[n] || 0;
const smile = (x) => Math.max(sh(x, 'mouthSmileLeft'), sh(x, 'mouthSmileRight'));
const worldOff = (x) => Math.max(Math.abs(x.world.x), Math.abs(x.world.y)) * DEG;

const results = [];
function check(name, fn) {
  try {
    const r = fn(play(name));
    results.push([name, true, r]);
  } catch (err) {
    results.push([name, false, err.message]);
  }
}
function assert(c, m) { if (!c) throw new Error(m); }

check('quelqu\'un arrive', ({ frames, e }) => {
  const wake = between(frames, 0, 1.2);
  const greet = between(frames, 1.5, 2.4);
  const wide = peak(wake, (x) => sh(x, 'eyeWideLeft'));
  const brow = peak(greet, (x) => sh(x, 'browOuterUpLeft'));
  const onUser = low(between(frames, 1.9, 2.6), (x) => -worldOff(x)) * -1;
  assert(wide > 0.1, `les yeux ne s'ouvrent pas au reveil (${wide.toFixed(2)})`);
  assert(brow > 0.25, `pas de salut des sourcils (${brow.toFixed(2)})`);
  assert(onUser < 3.5, `le regard n'est pas sur lui (${onUser.toFixed(1)}°)`);
  return `yeux ${wide.toFixed(2)} au reveil · sourcils ${brow.toFixed(2)} au bonjour · regard a ${onUser.toFixed(1)}°`;
});

check('resultat inattendu', ({ frames }) => {
  const at = 1.6;
  const surprise = between(frames, at, at + 0.35);
  const brow = peak(surprise, (x) => sh(x, 'browInnerUp'));
  const t90 = surprise.find((x) => sh(x, 'browInnerUp') >= brow * 0.9);
  const blinked = between(frames, at, at + 0.6).some((x) => x.blink);
  const later = peak(between(frames, at + 2.3, at + 2.6), (x) => sh(x, 'browInnerUp'));
  assert(brow > 0.5, `surprise trop faible (${brow.toFixed(2)})`);
  assert(t90 && t90.t - at < 0.25, 'la surprise se compose au lieu d\'arriver');
  assert(!blinked, 'clignement pendant les yeux grands ouverts');
  assert(later < brow * 0.6, `la surprise tient (${later.toFixed(2)} apres 2.3 s)`);
  return `sourcils ${brow.toFixed(2)} en ${(t90.t - at).toFixed(2)} s, aucun clignement, ${later.toFixed(2)} apres 2.3 s`;
});

check('besoin d\'une confirmation', ({ frames }) => {
  const wait = between(frames, 2.6, 4.6);
  const serious = peak(wait, (x) => Math.max(sh(x, 'browDownLeft'), sh(x, 'mouthPressLeft')));
  const off = peak(wait, worldOff);
  assert(serious > 0.15, `pas de gravite (${serious.toFixed(2)})`);
  assert(off < 3.5, `le regard ne tient pas (${off.toFixed(1)}°)`);
  return `gravite ${serious.toFixed(2)} · regard tenu a ${off.toFixed(1)}° pres pendant l'attente`;
});

check('avertissement', ({ frames, e }) => {
  const talk = between(frames, 0.6, 3.0);
  const concern = peak(talk, (x) => sh(x, 'browInnerUp'));
  const off = peak(talk, worldOff);
  assert(concern > 0.3, `inquietude trop faible (${concern.toFixed(2)})`);
  assert(off < 3.5, `le regard s'echappe pendant l'avertissement (${off.toFixed(1)}°)`);
  assert((e.rig.gazeCtl.averts || 0) === 0, 'une echappee du regard en avertissant');
  return `inquietude ${concern.toFixed(2)} · regard tenu a ${off.toFixed(1)}° · aucune echappee`;
});

check('l\'utilisateur remercie', ({ frames, decisions }) => {
  const reply = between(frames, 1.95, 3.2);
  const nod = peak(reply, (x) => Math.abs(x.gesture.rx)) * DEG;
  const warm = peak(reply, smile);
  assert(nod > 3 && nod < 12, `signe de tete ${nod.toFixed(1)}°`);
  assert(warm > 0.08, `aucune chaleur (${warm.toFixed(2)})`);
  const d = decisions[decisions.length - 1].d;
  return `hochement ${nod.toFixed(1)}° · sourire ${warm.toFixed(2)} (${d.expression} ${d.intensity.toFixed(2)})`;
});

check('l\'utilisateur plaisante', ({ frames }) => {
  const at = 2.95;
  const f = between(frames, at, at + 1.5);
  const brow = f.find((x) => sh(x, 'browOuterUpLeft') > 0.1);
  const mouth = f.find((x) => smile(x) > 0.1);
  const asym = peak(f, (x) => sh(x, 'mouthSmileLeft') - sh(x, 'mouthSmileRight'));
  assert(brow && mouth && mouth.t > brow.t, 'le sourire n\'arrive pas apres les yeux');
  assert(asym > 0.05, 'un sourire symetrique : de la joie, pas de l\'ironie');
  return `sourcil a +${(brow.t - at).toFixed(2)} s, sourire a +${(mouth.t - at).toFixed(2)} s, asymetrie ${asym.toFixed(2)}`;
});

check('JARVIS reflechit', ({ frames }) => {
  const f = between(frames, 3.0, 4.3);
  const away = peak(f, (x) => Math.abs(x.world.x)) * DEG;
  const brow = peak(f, (x) => sh(x, 'browDownLeft'));
  const tilt = peak(f, (x) => Math.abs(x.head.rz)) * DEG;
  assert(away > 6, `le regard reste sur l'utilisateur (${away.toFixed(1)}°)`);
  assert(brow > 0.15, `le front ne travaille pas (${brow.toFixed(2)})`);
  assert(tilt > 3, `la tete ne penche pas (${tilt.toFixed(1)}°)`);
  return `regard a ${away.toFixed(1)}° · front ${brow.toFixed(2)} · tete penchee ${tilt.toFixed(1)}°`;
});

check('JARVIS est interrompu', ({ frames }) => {
  const cut = 1.15;   // voir situations.js : au sommet d'une syllabe appuyee
  const open = peak(between(frames, cut - 0.1, cut), (x) => x.jaw);
  const closing = between(frames, cut, cut + 0.6);
  // La machoire, pas le « il parle » du lip-sync (qui tient 0.3 s de plus a
  // dessein : c'est ce qui fait qu'une phrase se lit d'un seul tenant).
  const closed = closing.find((x) => x.jaw < 0.05);
  let worst = 0;
  for (let k = 1; k < closing.length; k++) worst = Math.max(worst, closing[k - 1].jaw - closing[k].jaw);
  const back = peak(between(frames, cut + 1.0, cut + 1.8), worldOff);
  assert(open > 0.1, `la coupure ne tombe pas en pleine voyelle (machoire ${open.toFixed(2)})`);
  assert(closed && closed.t - cut < 0.3, `la bouche se ferme en ${closed ? (closed.t - cut).toFixed(2) : '∞'} s`);
  assert(worst < 0.08, `la bouche claque (${worst.toFixed(3)} en une image)`);
  assert(back < 4, `le regard n'est pas revenu (${back.toFixed(1)}°)`);
  return `coupe a machoire ${open.toFixed(2)}, fermee en ${(closed.t - cut).toFixed(2)} s, pire pas ${worst.toFixed(2)} · `
    + `regard revenu a ${back.toFixed(1)}°`;
});

check('tache reussie', ({ frames, e }) => {
  const f = between(frames, 0.3, 2.0);
  const chin = low(f, (x) => x.head.rx) * DEG;
  const warm = peak(f, smile);
  assert(chin < -2, `le menton ne se releve pas (${chin.toFixed(1)}°)`);
  assert(warm > 0.15, `pas de satisfaction (${warm.toFixed(2)})`);
  return `menton ${chin.toFixed(1)}° · sourire ${warm.toFixed(2)} · accent ${e.accents.last}`;
});

check('tache echouee', ({ frames }) => {
  const before = peak(between(frames, 0.8, 1.3), smile);
  const after = peak(between(frames, 1.75, 3.2), smile);
  const concern = peak(between(frames, 1.8, 3.2), (x) => Math.max(sh(x, 'browInnerUp'), sh(x, 'mouthFrownLeft')));
  assert(before > 0.1, `pas de sourire avant (${before.toFixed(2)})`);
  assert(after < 0.08, `le sourire traine sous la mauvaise nouvelle (${after.toFixed(2)})`);
  assert(concern > 0.25, `pas de preoccupation (${concern.toFixed(2)})`);
  return `sourire ${before.toFixed(2)} -> ${after.toFixed(2)} en 0.45 s · preoccupation ${concern.toFixed(2)}`;
});

check('l\'utilisateur explique longtemps', ({ frames, e }) => {
  const nods = e.conversation.stats.backchannels;
  // Ou chaque hochement a commence : jamais pendant qu'il parle. Un hochement
  // part 0.25 s apres le debut du silence (PAUSE_S).
  const starts = frames.filter((x, k) => k > 0 && x.nods > frames[k - 1].nods).map((x) => x.t);
  // L'utilisateur se taisait-il deja, au moment ou la tete est partie ?
  const steps = SITUATIONS["l'utilisateur explique longtemps"].steps;
  const inPause = starts.filter((t) => micAt(steps, t - 0.2) <= 0.021 && micAt(steps, t) <= 0.021);
  assert(nods >= 1 && nods <= 4, `${nods} hochements sur trois pauses`);
  assert(inPause.length === starts.length, `hochement pendant qu'il parle, a ${starts.map((t) => t.toFixed(1)).join(', ')} s`);
  return `${nods} hochement(s) sur trois pauses, tous dans une pause · `
    + `${e.rig.blink.byCause.listener_pause || 0} clignement(s) a une pause`;
});

check('il ne se passe rien', ({ frames, e }) => {
  const blinks = e.rig.blink.count;
  const moves = peak(frames, (x) => Math.abs(x.head.ry)) * DEG;
  const faces = new Set(frames.map((x) => Object.keys(x.shapes).filter((n) => !n.startsWith('eye') && x.shapes[n] > 0.12).join(','))).size;
  assert(blinks >= 8 && blinks <= 30, `${blinks} clignements en une minute`);
  assert(moves > 0.3 && moves < 6, `la tete ${moves.toFixed(1)}°`);
  assert(faces <= 2, `${faces} visages differents sans aucune decision`);
  return `${blinks} clignements, tete vivante a ${moves.toFixed(1)}°, aucun visage invente`;
});

const width = Math.max(...results.map(([n]) => n.length));
console.log('\n  avatar/ situations — chaque moment du labo, mesure sur la sortie\n');
for (const [name, ok, detail] of results) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name.padEnd(width)}  ${detail}`);
  if (!ok) console.log(`         attendu : ${SITUATIONS[name].expect}`);
}
const failed = results.filter(([, ok]) => !ok).length;
console.log(`\n  ${results.length - failed}/${results.length} passed\n`);
process.exit(failed ? 1 : 0);
