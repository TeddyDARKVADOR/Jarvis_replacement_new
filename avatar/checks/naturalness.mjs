/**
 * naturalness.mjs — vingt minutes de conversation, et ce qui s'y repete.
 *
 *     node avatar/checks/naturalness.mjs [minutes] [graine]
 *
 * POURQUOI UN CONTROLE DE LONGUE DUREE
 *   Chaque comportement du moteur a son test, sur quelques secondes. Aucun ne
 *   voit ce qu'un utilisateur voit au bout d'un quart d'heure : la meme boucle.
 *   C'est ainsi qu'on a trouve que chaque tour de parole jouait exactement
 *   `look_at_user 4° -> tilt 10° -> nod 9°` — correct a chaque fois, faux au
 *   huitieme.
 *
 *   Ce fichier simule une seance : l'utilisateur parle (son micro), JARVIS
 *   reflechit, repond (sa voix, avec des syllabes appuyees et des pauses),
 *   s'interrompt, se trompe, s'excuse, avertit ; l'utilisateur s'absente une
 *   minute, revient. Tout passe par le VRAI directeur (le miroir exact du
 *   Python, `director_parity.py`) et le VRAI moteur, avec la meme cadence de
 *   poussees que l'hote (`client_desktop/ui/avatar_view.py`).
 *
 * CE QUI EST MESURE — ET CE QUI NE L'EST PAS
 *   Des proprietes techniques et des repetitions : clignements (rythme,
 *   irregularite, causes), regard (changements, echappees), gestes (combien,
 *   quelle amplitude, trajectoires identiques), appuis de parole, hochements
 *   d'ecoute, visages (duree, retombee), valeurs invalides, cout, memoire,
 *   etat final. Aucune de ces mesures ne prouve qu'un visage est naturel ;
 *   elles prouvent qu'il ne tombe pas dans les pieges qu'on sait reconnaitre.
 */

import { AvatarEngine } from '../js/engine.js';
import { NullBody } from '../js/body_null.js';
import { Director, parseDirective } from '../js/director.js';
import { Catalogue } from '../js/catalog.js';
import { Rng } from '../js/rng.js';

const DT = 1 / 60;
const DEG = 180 / Math.PI;
const MINUTES = Number(process.argv[2]) || 20;
const SEED = Number(process.argv[3]) || 2026;
const RERESOLVE_S = 1.5;          // client_desktop/ui/avatar_view.py

// ── la seance ────────────────────────────────────────────────────────────────

/** Les intentions qu'un JARVIS ordinaire envoie en repondant, et leur poids. */
const REPLIES = [
  [null, 40], ['explain', 14], ['acknowledge', 9], ['agree', 7], ['amuse', 6],
  ['investigate', 4], ['report_success', 5], ['reassure', 3], ['report_failure', 3],
  ['warn', 2], ['confirm', 2], ['disagree', 2], ['apologise', 1], ['think', 2],
];

function weighted(rng, table) {
  const total = table.reduce((a, [, w]) => a + w, 0);
  let x = rng.next() * total;
  for (const [value, w] of table) { x -= w; if (x < 0) return value; }
  return table[table.length - 1][0];
}

/**
 * La seance, en evenements dates : etats, intentions, voix de JARVIS (des
 * syllabes), voix de l'utilisateur (des segments). Tiree d'une graine : la
 * meme graine donne la meme seance.
 */
function script(minutes, rng) {
  const events = [];
  const speech = [];     // syllabes de JARVIS : { t, peak }
  const user = [];       // segments de l'utilisateur : [debut, fin]
  let t = 1;
  const at = (dt, e) => { events.push(Object.assign({ t: t + dt }, e)); };
  at(0, { state: 'WAKING' });
  t += 1.4;
  at(0, { state: 'LISTENING' });
  at(0.2, { intent: 'greet' });
  let turn = 0;
  while (t < minutes * 60 - 30) {
    turn += 1;
    // L'utilisateur parle : 2 a 8 s, coupees de pauses.
    at(0, { state: 'LISTENING' });
    let u = t + 0.6;
    const end = u + rng.range(2, 8);
    while (u < end) {
      const len = rng.range(0.8, 2.6);
      user.push([u, Math.min(end, u + len)]);
      u += len + rng.range(0.3, 0.9);
    }
    t = end + rng.range(0.3, 0.8);

    // Il reflechit — parfois il cherche vraiment.
    at(0, { state: 'THINKING' });
    if (rng.next() < 0.2) at(0.3, { intent: rng.next() < 0.5 ? 'investigate' : 'think' });
    t += rng.range(0.8, 3.0);

    // Il repond.
    const reply = weighted(rng, REPLIES);
    const surprised = rng.next() < 0.04;
    at(0, { state: 'SPEAKING' });
    if (surprised) at(0.05, { directive: { expression: 'surprised', intensity: rng.range(0.4, 0.8) } });
    else if (reply) at(rng.range(0.05, 0.6), { intent: reply });
    const duration = rng.range(2, 12);
    const interrupted = rng.next() < 0.1;
    const stop = interrupted ? duration * rng.range(0.3, 0.6) : duration;
    let s = t + 0.15;
    let k = 0;
    while (s < t + stop) {
      const phrase = rng.range(1.2, 3.5);
      for (let p = s; p < Math.min(s + phrase, t + stop); p += rng.range(0.18, 0.28)) {
        const stressed = k % 4 === 0 || rng.next() < 0.1;
        speech.push({ t: p, peak: (stressed ? 0.8 : 0.42) * rng.range(0.85, 1.15) });
        k += 1;
      }
      s += phrase + rng.range(0.35, 0.8);
    }
    t += stop + 0.2;
    if (interrupted) {
      at(0, { state: 'LISTENING' });   // l'utilisateur coupe la parole
      user.push([t + 0.1, t + rng.range(1, 3)]);
      t += 3.2;
    }
    // Parfois une erreur, parfois un long silence : il s'absente.
    if (rng.next() < 0.05) { at(0, { state: 'ERROR' }); t += 2.5; }
    const pause = rng.next() < 0.08 ? rng.range(40, 70) : rng.range(1, 5);
    at(0, { state: 'LISTENING' });
    t += pause;
  }
  at(0, { state: 'LISTENING' });
  events.sort((a, b) => a.t - b.t);
  return { events, speech, user, turns: turn, end: t + 90 };
}

function speechLevel(speech, t, cursor) {
  // `cursor` avance : les syllabes sont triees.
  while (cursor.i < speech.length && speech[cursor.i].t < t - 0.25) cursor.i += 1;
  let v = 0;
  for (let j = cursor.i; j < speech.length && speech[j].t <= t; j++) {
    const d = t - speech[j].t;
    if (d > 0.2) continue;
    const k = d < 0.06 ? d / 0.06 : Math.max(0, 1 - (d - 0.06) / 0.11);
    v = Math.max(v, speech[j].peak * k);
  }
  return v;
}

function userLevel(user, t, rng) {
  const inside = user.some(([a, b]) => t >= a && t < b);
  const syll = Math.max(0, Math.sin(t * 2 * Math.PI * 4.1));
  return 0.02 + rng.range(0, 0.01) + (inside ? 0.32 * (0.4 + 0.6 * syll) : 0);
}

/** La regle de l'hote : pousser seulement ce qui change a l'oeil. */
function material(a, b) {
  if (!b) return true;
  for (const k of ['expression', 'gaze', 'posture', 'gesture_id', 'gaze_source', 'state']) {
    if (a[k] !== b[k]) return true;
  }
  if (Math.abs(a.intensity - b.intensity) >= 0.03) return true;
  for (const k of ['tempo', 'stillness']) if (Math.abs(a[k] - b[k]) >= 0.05) return true;
  const names = new Set([...Object.keys(a.blendshapes), ...Object.keys(b.blendshapes)]);
  for (const n of names) if (Math.abs((a.blendshapes[n] || 0) - (b.blendshapes[n] || 0)) >= 0.03) return true;
  return false;
}

// ── la simulation ────────────────────────────────────────────────────────────

const rng = new Rng(SEED);
const session = script(MINUTES, rng.fork('script'));
const body = new NullBody({ parts: ['head', 'torso', 'arms', 'legs'], visemes: 'oculus',
                            manifest: { rig: { motion: 'face' } } });
const engine = new AvatarEngine(body, { seed: SEED });
const director = new Director(new Catalogue(['head', 'torso', 'arms', 'legs'], 'face', []));
const noise = rng.fork('mic');

const m = {
  frames: 0, invalid: 0, pushes: 0, decisions: 0,
  blinks: [], gazeShifts: 0, glances: 0,
  plays: [], trajectories: new Map(),
  expressions: new Map(), path: [],
  maxHead: 0, maxBody: 0,
  worstUs: 0, totalUs: 0,
};
let state = 'ACTIVE';
let last = null;
let lastPushAt = -10;
let ei = 0;
const cursor = { i: 0 };
let lastSpeak = -1; let lastListen = -1;
let blinkCount = 0;
let glanceUntil = 0;
let lastExpression = null; let expressionSince = 0;
let current = null;    // le geste en cours d'enregistrement
let averts = 0; let avertCheck = null;
let microWas = null; let lastMicroIndex = -1; m.micros = 0; m.microRepeats = 0;
m.avertsReached = 0; m.avertsPreempted = 0; m.avertsLost = 0;
const heapBefore = process.memoryUsage().heapUsed;

function push(now) {
  const p = director.resolve(state, { speechLevel: 0, now });
  if (!material(p, last)) return;
  const d = engine.perform(p);
  last = p;
  lastPushAt = now;
  m.pushes += 1;
  if (d.gesture_played !== 'deja en cours') {
    m.decisions += 1;
    m.plays.push({ t: now, gesture: d.gesture, via: d.gesture_played, scale: d.gesture_scale,
                   reflex: String(p.gesture_id || '').startsWith('reflex:') });
    current = { key: d.gesture, samples: [], until: now + 3 };
  }
}

const frames = Math.round(session.end / DT);
for (let f = 0; f < frames; f++) {
  const now = f * DT;
  while (ei < session.events.length && session.events[ei].t <= now) {
    const e = session.events[ei++];
    if (e.state) { state = e.state; push(now); }
    if (e.intent) { director.setIntent(parseDirective({ intent: e.intent }), now); last = null; push(now); }
    if (e.directive) { director.setIntent(parseDirective(e.directive), now); last = null; push(now); }
  }
  if (now - lastPushAt >= RERESOLVE_S) { lastPushAt = now; push(now); }

  const sample = Math.floor(now / 0.04);
  if (sample !== lastSpeak) { lastSpeak = sample; engine.speak(speechLevel(session.speech, sample * 0.04, cursor)); }
  const ls = Math.floor(now * 15);
  if (ls !== lastListen && state === 'LISTENING') { lastListen = ls; engine.listen(userLevel(session.user, now, noise)); }

  const started = process.hrtime.bigint();
  engine.update(DT);
  const us = Number(process.hrtime.bigint() - started) / 1000;
  m.totalUs += us;
  if (f > 600) m.worstUs = Math.max(m.worstUs, us);
  m.frames += 1;

  // ── ce qui est sorti ──────────────────────────────────────────────────
  for (const name in body.morphs) {
    const v = body.morphs[name];
    if (!Number.isFinite(v) || v < 0 || v > 1) m.invalid += 1;
  }
  const s = engine.gestures.smoothed;
  for (const v of [s.headRx, s.headRy, s.headRz]) if (!Number.isFinite(v)) m.invalid += 1;
  m.maxHead = Math.max(m.maxHead, Math.abs(s.headRx), Math.abs(s.headRy), Math.abs(s.headRz));
  m.maxBody = Math.max(m.maxBody, Math.abs(s.spineRx), Math.abs(s.spineRy), Math.abs(s.rootRy));

  const blink = engine.rig.blink;
  if (blink.count > blinkCount) {
    blinkCount = blink.count;
    m.blinks.push({ t: now, cause: blink.lastCause, state: engine.states.name });
  }
  const g = engine.rig.gazeCtl;
  if (g.shiftAt >= 0 && Math.abs(g.shiftAt - (g.t - DT)) < DT / 2) m.gazeShifts += 1;
  if (g.glance.until > glanceUntil) { glanceUntil = g.glance.until; m.glances += 1; }
  // Une echappee acceptee doit ARRIVER aux yeux dans la demi-seconde.
  const idle = engine.gestures.idle;
  if (idle.micro && idle.micro !== microWas) {
    m.micros += 1;
    if (idle.microIndex === lastMicroIndex) m.microRepeats += 1;
    lastMicroIndex = idle.microIndex;
  }
  microWas = idle.micro;
  // Chaque echappee acceptee a UNE issue : arrivee aux yeux, reprise par une
  // decision avant d'arriver, ou perdue — et perdue est un defaut.
  if (g.averts > averts) {
    averts = g.averts;
    avertCheck = { until: now + 0.5, max: 0, cancelled: g.avertsCancelled };
  }
  if (avertCheck) {
    avertCheck.max = Math.max(avertCheck.max, Math.abs(g.out.x));
    const preempted = g.avertsCancelled > avertCheck.cancelled;
    if (avertCheck.max > 0.12) { m.avertsReached += 1; avertCheck = null; }
    else if (preempted) { m.avertsPreempted += 1; avertCheck = null; }
    else if (now >= avertCheck.until) { m.avertsLost += 1; avertCheck = null; }
  }

  const expr = engine.rig.performance.expression;
  if (expr !== lastExpression) {
    if (lastExpression !== null) {
      m.expressions.set(lastExpression, (m.expressions.get(lastExpression) || 0) + now - expressionSince);
    }
    m.path.push(expr);
    lastExpression = expr;
    expressionSince = now;
  }

  if (current && f % 6 === 0) {
    const gh = engine.gestures.gestureHead;
    current.samples.push(`${(gh.rx * DEG).toFixed(1)},${(gh.ry * DEG).toFixed(1)},${(gh.rz * DEG).toFixed(1)}`);
    if (now >= current.until) {
      if (current.samples.some((x) => x !== '0.0,0.0,0.0' && x !== '-0.0,0.0,0.0')) {
        const key = `${current.key}|${current.samples.join(';')}`;
        m.trajectories.set(key, (m.trajectories.get(key) || 0) + 1);
      }
      current = null;
    }
  }
}
m.expressions.set(lastExpression, (m.expressions.get(lastExpression) || 0) + session.end - expressionSince);
const heapAfter = process.memoryUsage().heapUsed;

// ── les mesures ──────────────────────────────────────────────────────────────

const results = [];
function check(name, ok, detail) { results.push([name, !!ok, detail]); }
const minutes = session.end / 60;
const stat = (xs) => {
  const mean = xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
  const sd = Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, xs.length));
  return { mean, cv: mean ? sd / mean : 0, max: Math.max(...xs), min: Math.min(...xs) };
};

// Clignements
const ibi = [];
for (let i = 1; i < m.blinks.length; i++) {
  const d = m.blinks[i].t - m.blinks[i - 1].t;
  if (m.blinks[i].cause !== 'double') ibi.push(d);
}
const b = stat(ibi);
const causes = {};
for (const x of m.blinks) causes[x.cause] = (causes[x.cause] || 0) + 1;
const perMin = m.blinks.length / minutes;
// La periodicite : l'autocorrelation du train de clignements, par cases de 0.25 s.
const bins = new Array(Math.ceil(session.end / 0.25)).fill(0);
for (const x of m.blinks) bins[Math.floor(x.t / 0.25)] = 1;
const meanBin = bins.reduce((a, v) => a + v, 0) / bins.length;
let worstAc = 0; let worstLag = 0;
for (let lag = 8; lag <= 80; lag++) {
  let num = 0; let den = 0;
  for (let i = 0; i + lag < bins.length; i++) num += (bins[i] - meanBin) * (bins[i + lag] - meanBin);
  for (let i = 0; i < bins.length; i++) den += (bins[i] - meanBin) ** 2;
  const ac = num / den;
  if (ac > worstAc) { worstAc = ac; worstLag = lag * 0.25; }
}
check('clignements : un rythme humain, sans horloge',
  perMin > 8 && perMin < 30 && b.cv > 0.45 && b.cv < 1.3 && worstAc < 0.1,
  `${m.blinks.length} en ${minutes.toFixed(1)} min (${perMin.toFixed(1)}/min), CV ${b.cv.toFixed(2)}, `
  + `plus long ${b.max.toFixed(1)} s ; autocorrelation max ${worstAc.toFixed(3)} a ${worstLag} s ; `
  + Object.entries(causes).map(([k, v]) => `${k} ${v}`).join(', '));

// Gestes
const byGesture = {};
for (const p of m.plays) {
  const r = byGesture[p.gesture] || (byGesture[p.gesture] = { n: 0, skipped: 0, scales: [] });
  r.n += 1;
  if (p.via === 'habituated') r.skipped += 1;
  else if (Number.isFinite(p.scale)) r.scales.push(p.scale);
}
const line = Object.entries(byGesture)
  .filter(([name]) => name !== 'idle')
  .sort((a, b2) => b2[1].n - a[1].n)
  .map(([name, r]) => {
    const s = stat(r.scales);
    return `${name} ${r.n}${r.skipped ? ` (${r.skipped} sautes)` : ''} x${s.min.toFixed(2)}–${s.max.toFixed(2)}`;
  }).join(' · ');
check('gestes : chacun a sa cause, aucun ne tourne en boucle',
  m.maxHead * DEG < 25 && m.maxBody < 1e-6,
  `${m.decisions} decisions jouees ; ${line} ; tete max ${(m.maxHead * DEG).toFixed(1)}°, corps ${m.maxBody.toFixed(4)}`);

// Trajectoires identiques
let dup = 0; let total = 0;
for (const count of m.trajectories.values()) { total += count; if (count > 1) dup += count - 1; }
check('aucune trajectoire de geste ne se rejoue a l\'identique',
  total > 0 && dup / total < 0.02,
  `${total} gestes enregistres a 10 Hz et 0.1° pres ; ${dup} copies exactes (${(100 * dup / Math.max(1, total)).toFixed(1)} %)`);

// Conversation
const cs = engine.conversation.stats;
const bc = engine.rig.gazeCtl.averts || 0;
check('la conversation se voit : appuis, echappees, hochements, clignements de fin',
  cs.emphasised > 0 && m.avertsReached >= cs.utterances * 0.15
    && m.avertsLost === 0 && m.avertsReached + m.avertsPreempted === bc
    && cs.backchannels > 0 && (causes.utterance_end || 0) > 0,
  `${cs.utterances} enonces, ${cs.averts} echappees tirees, ${bc} acceptees : ${m.avertsReached} arrivees aux yeux, `
  + `${m.avertsPreempted} reprises avant par une decision, ${m.avertsLost} perdues ; ${cs.beats} impulsions `
  + `dont ${cs.emphasised} appuis, ${cs.brows} sourcils ; ${cs.backchannels} hochements d'ecoute ; `
  + `${causes.utterance_end || 0} clignements de fin de phrase`);

check('micro-expressions : jamais la meme deux fois de suite',
  m.micros > 0 && m.microRepeats === 0,
  `${m.micros} micro-expressions (${(m.micros / minutes).toFixed(1)}/min), ${m.microRepeats} repetition(s) immediate(s)`);

// Regard
check('regard : il bouge, et revient',
  m.gazeShifts > 0 && m.glances > 0,
  `${m.gazeShifts} changements de cible, ${m.glances} coups d'oeil ou echappees (${(m.glances / minutes).toFixed(1)}/min)`);

// Visages
const faces = [...m.expressions.entries()].sort((a, b2) => b2[1] - a[1])
  .map(([k, v]) => `${k} ${(100 * v / session.end).toFixed(0)} %`).join(' · ');
check('visages : du neutre surtout, et des emotions qui passent',
  (m.expressions.get('neutral') || 0) / session.end > 0.4,
  `${faces} ; ${m.path.length} changements de visage (${(m.path.length / minutes).toFixed(1)}/min)`);

// Proprietes
check('aucune valeur invalide, en ${frames} images'.replace('${frames}', m.frames),
  m.invalid === 0, `${m.invalid} valeurs NaN, infinies ou hors [0, 1]`);

// Etat final : 90 s de silence apres la derniere reponse.
const out = engine.output();
const settled = out.accent === null && engine.rig.performance.expression === 'neutral'
  && Math.abs(engine.conversation.head.rx) < 1e-4 && !out.speaking;
check('etat final : rien de perime apres 90 s de silence', settled,
  `visage ${engine.rig.performance.expression}, accent ${out.accent || '-'}, parle ${out.speaking.toFixed(2)}, `
  + `appui ${(engine.conversation.head.rx * DEG).toFixed(2)}°, intention ${director.intent && director.intent.intent || '-'}`);

// Cout
const mb = (heapAfter - heapBefore) / 1048576;
check('cout : chaque image reste bon marche, la memoire ne grossit pas',
  m.totalUs / m.frames < 80 && mb < 40,
  `${(m.totalUs / m.frames).toFixed(1)} us/image en moyenne, pire ${m.worstUs.toFixed(0)} us ; `
  + `${m.pushes} poussees (${(m.pushes / minutes).toFixed(1)}/min) ; tas ${mb >= 0 ? '+' : ''}${mb.toFixed(1)} Mo`);

// ── rapport ──────────────────────────────────────────────────────────────────

console.log(`\n  avatar/ naturel — ${minutes.toFixed(1)} min simulees, ${session.turns} tours, graine ${SEED}\n`);
const width = Math.max(...results.map(([n]) => n.length));
for (const [name, ok, detail] of results) console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name.padEnd(width)}  ${detail}`);
const failed = results.filter(([, ok]) => !ok).length;
console.log(`\n  ${results.length - failed}/${results.length} passed`);
console.log('  Ces mesures disent ce qui ne se repete pas. Elles ne disent pas que c\'est naturel :');
console.log('  ca, c\'est au labo, a l\'oeil.\n');
process.exit(failed ? 1 : 0);
