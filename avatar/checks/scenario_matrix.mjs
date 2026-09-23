/**
 * scenario_matrix.mjs — chaque intention, dans chaque situation, a la sortie.
 *
 *     node avatar/checks/scenario_matrix.mjs
 *
 * POURQUOI UNE MATRICE
 *   Les autres controles suivent des cas choisis. Les pannes trouvees jusqu'ici
 *   etaient toutes AILLEURS que dans les cas choisis : un regard juste sur x et
 *   faux sur y, une aversion comptee et annulee a l'image suivante, un accent
 *   qui survivait a la decision d'apres. Ici on ne choisit pas : dix-sept
 *   intentions (et aucune) × quatre etats × parole ou silence × trois regards ×
 *   urgence ou pas × interrompue ou pas — 1 632 situations — et pour chacune
 *   des invariants mesures sur ce que le corps a RECU.
 *
 *   Puis la question qu'aucun cas isole ne pose : dans une situation donnee,
 *   deux intentions finissent-elles par se jouer pareil ? Une intention qui ne
 *   se distingue pas de sa voisine, dans ce contexte-la, n'est qu'un nom.
 *
 * LES INVARIANTS
 *   I1  aucune valeur invalide ; I2 rien sous la nuque en mode visage ;
 *   I3  un regard ECRIT est tenu, sur les deux axes ; I4 le visage decide
 *   arrive (sa forme la plus forte atteint la moitie de sa cible) ; I5 la
 *   parole ouvre la bouche, le silence ne la fait pas parler ; I6 une
 *   interruption l'emporte : le visage qui interrompt est la, l'ancien
 *   accent n'y est plus ; I7 le geste rapporte n'est ni gele ni absent.
 */

import { AvatarEngine } from '../js/engine.js';
import { NullBody } from '../js/body_null.js';
import { Director, parseDirective } from '../js/director.js';
import { Catalogue } from '../js/catalog.js';
import { INTENTS } from '../js/affect.js';
import { EYE_REACH_RAD } from '../js/gaze.js';

const DT = 1 / 60;
const DEG = 180 / Math.PI;

const intents = [null, ...Object.keys(INTENTS)];
const states = ['LISTENING', 'THINKING', 'SPEAKING', 'CONFIRM'];
const speeches = [false, true];
const gazes = [null, 'user', 'screen'];
const urgencies = [false, true];
const interruptions = [false, true];

function voice(t) {
  // Des syllabes a ~4.5/s, une sur quatre appuyee.
  const i = Math.floor(t * 4.5);
  const d = t - i / 4.5;
  const peak = i % 4 === 0 ? 0.8 : 0.42;
  return d < 0.06 ? peak * d / 0.06 : Math.max(0, peak * (1 - (d - 0.06) / 0.11));
}

function runCase(c) {
  const body = new NullBody({ parts: ['head', 'torso', 'arms', 'legs'], visemes: 'oculus',
                              manifest: { rig: { motion: 'face' } } });
  const e = new AvatarEngine(body, { seed: 7 });
  const d = new Director(new Catalogue(['head', 'torso', 'arms', 'legs'], 'face', []));
  e.perform(d.resolve(c.state, { now: 0 }));
  for (let i = 0; i < 20; i++) e.update(DT);

  const request = {};
  if (c.intent) request.intent = c.intent;
  if (c.gaze) request.gaze = c.gaze;
  if (c.urgent) request.urgency = 0.9;
  let perf = null;
  if (Object.keys(request).length) {
    d.setIntent(parseDirective(request), 0.35);
    perf = d.resolve(c.state, { now: 0.35 });
  } else {
    perf = d.resolve(c.state, { now: 0.35 });
  }
  const decision = e.perform(perf);

  const out = { invalid: 0, body: 0, peakTarget: 0, eyes: [], world: [], jaw: 0, visemes: 0, head: [],
                shapes: {}, accentAfter: null, interruptFace: 0 };
  const lead = Object.entries(perf.blendshapes).filter(([n]) => !n.startsWith('eyeLook'))
    .sort((a, b) => b[1] - a[1])[0];
  let second = null;
  for (let i = 0; i < 150; i++) {
    const t = i * DT;
    if (c.speech && i % 2 === 0) e.speak(voice(t));
    if (c.interrupted && i === 24) {
      d.setIntent(parseDirective({ intent: 'warn' }), 0.75);
      second = d.resolve(c.state, { now: 0.75 });
      e.perform(second);
    }
    e.update(DT);
    for (const n in body.morphs) {
      const v = body.morphs[n];
      if (!Number.isFinite(v) || v < 0 || v > 1) out.invalid += 1;
    }
    const s = e.gestures.smoothed;
    out.body = Math.max(out.body, Math.abs(s.spineRx), Math.abs(s.spineRy), Math.abs(s.rootY),
                        Math.abs(s.rootZ), Math.abs(s.rootRy));
    if (lead && (!c.interrupted || i < 24)) out.peakTarget = Math.max(out.peakTarget, (body.morphs[lead[0]] || 0) / lead[1]);
    out.jaw = Math.max(out.jaw, body.morphs.jawOpen || 0);
    out.visemes = Math.max(out.visemes, ...Object.values(body.visemes).map((v) => v || 0), 0);
    // Le regard DANS LE MONDE : l'oeil dans la tete, plus la tete. C'est lui
    // qui doit etre sur l'utilisateur — un oeil centre dans une tete
    // detournee ne regarde personne.
    if (i >= 90) {
      const eye = e.rig.gazeCtl.out;
      out.eyes.push([eye.x, eye.y]);
      out.world.push([eye.x * EYE_REACH_RAD + s.headRy, eye.y * EYE_REACH_RAD - s.headRx]);
    }
    if (i % 10 === 0) out.head.push([s.headRx, s.headRy, s.headRz]);
  }
  out.accentAfter = e.accents.name;
  out.shapes = Object.assign({}, body.morphs);
  if (second) {
    const top = Object.entries(second.blendshapes).filter(([n]) => !n.startsWith('eyeLook'))
      .sort((a, b) => b[1] - a[1])[0];
    out.interruptFace = top ? (body.morphs[top[0]] || 0) / top[1] : 1;
    out.secondAccent = second.accent || null;
  }
  out.decision = decision;
  out.perf = perf;
  return out;
}

const cases = [];
for (const intent of intents) for (const state of states) for (const speech of speeches)
  for (const gaze of gazes) for (const urgent of urgencies) for (const interrupted of interruptions) {
    cases.push({ intent, state, speech, gaze, urgent, interrupted });
  }

const failures = new Map();
function fail(kind, c, detail) {
  if (!failures.has(kind)) failures.set(kind, []);
  failures.get(kind).push(`${c.intent || '-'}/${c.state}/${c.speech ? 'parle' : 'silence'}/`
    + `${c.gaze || 'derive'}${c.urgent ? '/urgent' : ''}${c.interrupted ? '/interrompu' : ''} : ${detail}`);
}

const started = process.hrtime.bigint();
const signatures = new Map();     // contexte -> intention -> vecteur de sortie
for (const c of cases) {
  const o = runCase(c);
  if (o.invalid) fail('I1 valeurs invalides', c, `${o.invalid}`);
  if (o.body > 1e-6) fail('I2 le corps bouge en mode visage', c, `${(o.body * DEG).toFixed(2)}°`);
  if (c.gaze === 'user' && !c.interrupted) {
    // 3° : les micro-saccades (~1.3°), la derive du repos compensee a 75 %, et
    // les inclinaisons de posture que les yeux ne rattrapent pas.
    const worst = Math.max(...o.world.map(([x, y]) => Math.max(Math.abs(x), Math.abs(y)))) * DEG;
    if (worst > 3) fail('I3 regard ecrit non tenu', c, `regard a ${worst.toFixed(1)}° de l'utilisateur`);
  }
  if (c.gaze === 'screen' && !c.interrupted) {
    const low = Math.min(...o.eyes.map(([x]) => x));
    if (low < 0.3) fail('I3 regard ecrit non tenu', c, `screen, yeux x=${low.toFixed(2)}`);
  }
  if (o.peakTarget && o.peakTarget < 0.5 && o.perf.expression !== 'neutral') {
    fail('I4 le visage decide n\'arrive pas', c, `${o.perf.expression} a ${(o.peakTarget * 100).toFixed(0)} %`);
  }
  if (c.speech && o.visemes < 0.1) fail('I5 la parole n\'ouvre pas la bouche', c, `visemes ${o.visemes.toFixed(2)}`);
  if (!c.speech && o.visemes > 0.01) fail('I5 le silence parle', c, `visemes ${o.visemes.toFixed(2)}`);
  if (c.interrupted) {
    if (o.interruptFace < 0.5) fail('I6 l\'interruption ne l\'emporte pas', c, `warn a ${(o.interruptFace * 100).toFixed(0)} %`);
    if (o.accentAfter && o.accentAfter !== o.secondAccent) fail('I6 un accent survit a l\'interruption', c, o.accentAfter);
  }
  if (['frozen', 'absent', 'none'].includes(o.decision.gesture_played)) {
    fail('I7 geste rapporte gele ou absent', c, `${o.decision.gesture} ${o.decision.gesture_played}`);
  }
  if (!c.interrupted && !c.urgent) {
    const ctx = `${c.state}/${c.speech ? 'parle' : 'silence'}/${c.gaze || 'derive'}`;
    if (!signatures.has(ctx)) signatures.set(ctx, new Map());
    // La signature : les formes (hors yeux), la tete echantillonnee, les yeux.
    const v = [];
    for (const n of Object.keys(o.shapes).sort()) if (!n.startsWith('eyeLook') && !n.startsWith('eyeBlink')) v.push([n, o.shapes[n]]);
    signatures.get(ctx).set(c.intent || '-', { shapes: Object.fromEntries(v), head: o.head, eyes: o.eyes[o.eyes.length - 1] });
  }
}
const ms = Number(process.hrtime.bigint() - started) / 1e6;

// ── les intentions indiscernables, par contexte ──────────────────────────────

function distance(a, b) {
  let sum = 0;
  const names = new Set([...Object.keys(a.shapes), ...Object.keys(b.shapes)]);
  for (const n of names) sum += ((a.shapes[n] || 0) - (b.shapes[n] || 0)) ** 2;
  for (let i = 0; i < a.head.length; i++) {
    for (let k = 0; k < 3; k++) sum += ((a.head[i][k] - b.head[i][k]) * 2) ** 2;   // 1 rad ~ 2 de poids
  }
  sum += (a.eyes[0] - b.eyes[0]) ** 2 + (a.eyes[1] - b.eyes[1]) ** 2;
  return Math.sqrt(sum);
}

const THRESHOLD = 0.08;
const collapsed = [];
let closest = { d: Infinity };
for (const [ctx, table] of signatures) {
  const names = [...table.keys()].filter((n) => n !== '-');
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      const dd = distance(table.get(names[i]), table.get(names[j]));
      if (dd < closest.d) closest = { d: dd, pair: `${names[i]}/${names[j]}`, ctx };
      if (dd < THRESHOLD) collapsed.push(`${ctx} : ${names[i]} ≈ ${names[j]} (${dd.toFixed(3)})`);
    }
  }
}

// ── rapport ──────────────────────────────────────────────────────────────────

console.log(`\n  avatar/ matrice — ${cases.length} situations, ${(ms / 1000).toFixed(1)} s\n`);
const kinds = ['I1 valeurs invalides', 'I2 le corps bouge en mode visage', 'I3 regard ecrit non tenu',
  'I4 le visage decide n\'arrive pas', 'I5 la parole n\'ouvre pas la bouche', 'I5 le silence parle',
  'I6 l\'interruption ne l\'emporte pas', 'I6 un accent survit a l\'interruption',
  'I7 geste rapporte gele ou absent'];
let bad = 0;
for (const kind of kinds) {
  const list = failures.get(kind) || [];
  bad += list.length;
  console.log(`  [${list.length ? 'FAIL' : 'PASS'}] ${kind.padEnd(40)} ${list.length ? `${list.length} cas` : '0 cas'}`);
  for (const line of list.slice(0, 6)) console.log(`         ${line}`);
}
console.log(`  [${collapsed.length ? 'WARN' : 'PASS'}] ${'intentions indiscernables'.padEnd(40)} `
  + `${collapsed.length} paires sous ${THRESHOLD} dans ${signatures.size} contextes ; la plus proche `
  + `${closest.pair} a ${closest.d.toFixed(3)} (${closest.ctx})`);
for (const line of collapsed.slice(0, 12)) console.log(`         ${line}`);
console.log(`\n  ${bad ? `${bad} violation(s)` : 'Aucune violation'} sur ${cases.length} situations.\n`);
process.exit(bad ? 1 : 0);
