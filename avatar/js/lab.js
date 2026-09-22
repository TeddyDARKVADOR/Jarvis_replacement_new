/**
 * lab.js — l'etabli. Le meme moteur que le panneau, et tout ce qu'il cache.
 *
 * LA REGLE
 *   Le labo ne joue rien lui-meme. Il construit une DEMANDE (ce que JARVIS
 *   enverrait par `set_presence`), la fait lire par `parseDirective` et
 *   resoudre par `Director` — les miroirs exacts de `presence/director.py`,
 *   confrontes au Python par `avatar/checks/director_parity.py` — puis donne
 *   la Performance obtenue au MEME `AvatarEngine` que le panneau.
 *
 *   Ce qu'il affiche ensuite n'est pas ce qu'il a demande : c'est ce que le
 *   moteur rapporte avoir fait. Un geste rabattu s'affiche rabattu, un geste
 *   gele par `rig.motion` s'affiche gele, un accent qui n'a pas joue
 *   s'affiche absent.
 *
 *       DEMANDE          l'objet que JARVIS aurait envoye
 *       RESOLU           ce que `parse()` en a lu : intention, affect, regard
 *       DECISION         la Performance du directeur, et sa trace
 *       SORTIE EFFECTIVE ce que le corps a recu : formes, tete, yeux, geste
 *
 *   L'ancien labo derivait avec une regle a lui, et montrait `✗ wave
 *   injouable → idle` la ou le panneau hochait la tete. C'est la raison de
 *   cette refonte, pas un detail.
 *
 * POURQUOI LES MODELES NE SONT JAMAIS INSTALLES ICI
 *   Deposer un fichier ne change rien sur le disque. Le labo sert a regarder ;
 *   installer est une decision, et c'est `presence.install_model` qui la prend.
 */

import { GltfBody, readModel } from './body_gltf.js';
import { VrmBody, isVrm } from './body_vrm.js';
import { ARKIT_NAMES } from './arkit.js';
import { ProceduralBody } from './body_procedural.js';
import { NullBody } from './body_null.js';
import { EXPRESSIONS, GAZES } from './expressions.js';
import { BASELINE, INTENTS, SOCIAL_MODES } from './affect.js';
import { Catalogue, GESTURES, motionOf } from './catalog.js';
import { Director, parseDirective, REFLEX } from './director.js';
import { AvatarEngine } from './engine.js';
import { replay } from './recorder.js';
import { createStage } from './stage.js';
import { buildProfile, formatProfile } from './profile.js';

/** Les cinq axes continus, avec de quoi les lire. */
const AXES = [
  ['valence', -1, 1, 'désagréable → agréable'],
  ['arousal', 0, 1, 'calme → activé'],
  ['attention', 0, 1, 'ailleurs → sur l\'utilisateur'],
  ['confidence', 0, 1, 'hésitant → assuré'],
  ['urgency', 0, 1, 'rien ne presse → il faut agir'],
];

const $ = (id) => document.getElementById(id);
const round = (v) => Math.round(v * 100) / 100;

/** Expose a dessein : l'etat du labo doit etre lisible depuis la console et
 *  depuis les controles qui pilotent cette page. */
const app = window.__lab = {
  body: null,
  engine: null,
  rig: null,
  director: null,
  manifest: {},
  profile: null,
  manual: Object.create(null),       // ce que les curseurs de formes imposent
  affect: Object.assign({}, BASELINE, { social_mode: 'professional' }),
  request: null,                     // la demande, telle que JARVIS l'enverrait
  directive: null,                   // ce que parseDirective en a lu
  performance: null,                 // ce que le directeur a resolu
  state: 'ACTIVE',
  speech: 0,
  situation: '',
  frame: 'face',
  babble: null,
  t: 0,                              // l'horloge du directeur du labo
  replaying: null,
};

// ── scene ────────────────────────────────────────────────────────────────────

const canvas = $('stage');
const stage = createStage(canvas, {});
const { renderer, scene, camera } = stage;

function resize() {
  stage.resize(canvas.clientWidth, canvas.clientHeight);
}
new ResizeObserver(resize).observe(canvas);

// ── le corps et son moteur ───────────────────────────────────────────────────

/**
 * Monte un corps et un moteur neuf. Le moteur est reconstruit a chaque fois :
 * c'est ce qui rend un enregistrement rejouable depuis sa premiere image.
 */
function mount(body, label, context = {}, { seed, record = true } = {}) {
  if (app.body && app.body !== body && app.body.object3D) scene.remove(app.body.object3D);
  if (app.engine && app.body === body) resetBody(body, app.engine);
  app.body = body;
  if (body.object3D && !body.object3D.parent) scene.add(body.object3D);

  app.engine = new AvatarEngine(body, { seed, record });
  app.rig = app.engine.rig;
  app.gestures = app.engine.gestures;
  app.lipsync = app.engine.lipsync;
  app.manifest = body.manifest || context.manifest || {};

  const catalogue = new Catalogue(
    body.detectedParts ? [...body.detectedParts] : ['head', 'torso'],
    motionOf(app.manifest),
    Object.keys(body.clips || {}),
  );
  app.director = new Director(catalogue);
  app.profile = buildProfile(body, Object.assign({ manifest: app.manifest }, context));

  const framed = stage.frame(body, app.frame);
  app.frame = framed.mode;
  markFrame();
  resize();
  report(label);
  buildShapeSliders(body);
  buildClipButtons(body);
  decide();
}

/**
 * Rendre un corps tel qu'il etait avant le moteur precedent.
 *
 * Un moteur neuf lit la pose de repos dans les os ; s'il la lisait apres un
 * hochement, le « repos » du nouveau serait penche pour toujours. Et le rig
 * n'ecrit que ce qui change : une forme laissee a 0.4 par l'ancien resterait a
 * 0.4 sur le mesh.
 */
function resetBody(body, engine) {
  for (const [key, rest] of engine.gestures.rest) {
    const node = engine.gestures.nodes[key];
    if (!node) continue;
    node.rotation.set(rest.rx, rest.ry, rest.rz);
    node.position.set(rest.px, rest.py, rest.pz);
  }
  for (const name of ARKIT_NAMES) body.setMorph(name, 0);
  if (body.setViseme) for (const name in engine.rig.writtenViseme) body.setViseme(name, 0);
  if (body.update) body.update(0);
}

function reportLine(text, cls = '') {
  return cls ? `<span class="${cls}">${escapeHtml(text)}</span>` : escapeHtml(text);
}

function report(label) {
  const p = app.profile;
  const lines = [reportLine(label), ''];
  const text = formatProfile(p).split('\n');
  for (const line of text) {
    const bad = /✗|AUCUN|0\/52/.test(line);
    const ok = /^FACE\s+(4\d|5[0-2])\/52/.test(line) || /^VISEMES\s+✓/.test(line);
    lines.push(reportLine(line, bad ? 'bad' : ok ? 'ok' : ''));
  }
  if (typeof p.arkit === 'number' && p.arkit === 0) {
    lines.push('', reportLine('aucune forme reconnue : le visage restera figé.', 'bad'),
      'Console → liste des morphs, puis model.morphAliases.');
  } else if (p.arkitMissing.length && p.arkitMissing.length < 52) {
    lines.push('', `manquantes (${p.arkitMissing.length}) :`,
      p.arkitMissing.slice(0, 10).join(', ') + (p.arkitMissing.length > 10 ? ' …' : ''));
  }
  $('report').innerHTML = lines.join('\n');
}

async function loadFile(file) {
  return loadBuffer(file.name, await file.arrayBuffer());
}

async function loadBuffer(name, buffer) {
  const file = { name };
  $('report').textContent = `lecture de ${file.name}…`;
  const started = performance.now();
  try {
    const gltf = await parseModel(buffer);
    const manifest = {
      model: { file: file.name, scale: 1, position: [0, 0, 0], morphAliases: {} },
      rig: { motion: (app.manifest.rig || {}).motion || 'full' },
    };
    const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);
    if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);
    // Les clips du modele deviennent jouables tels quels : c'est la facon la
    // plus rapide de voir ce qu'un asset Mixamo contient vraiment.
    for (const clip of gltf.animations || []) body.addClip(clip.name, clip);
    mount(body, `${file.name} — déposé`, {
      gltf, manifest, file: file.name, loadMs: performance.now() - started,
    });
  } catch (err) {
    $('report').innerHTML = `<span class="bad">échec : ${escapeHtml(err.message)}</span>`;
    console.error(err);
    return { error: err.message };
  }
  return app.profile;
}

function parseModel(buffer) {
  return readModel(renderer,
    (loader) => new Promise((res, rej) => loader.parse(buffer, '', res, rej)));
}

/** Pour les controles de robustesse : un GLB en base64, sans fichier. */
window.__lab.loadBase64 = (name, base64) => {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return loadBuffer(name, bytes.buffer);
};

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

// ── un curseur par forme : la couche EXPLICITE ───────────────────────────────

function buildShapeSliders(body) {
  const host = $('shapes');
  host.innerHTML = '';
  const present = new Set(body.morphTargets ? body.morphTargets.keys()
    : (body.native || ARKIT_NAMES));

  for (const name of ARKIT_NAMES) {
    const has = present.has(name) || body instanceof ProceduralBody;
    const wrap = document.createElement('div');
    wrap.className = 'slider';
    wrap.dataset.name = name.toLowerCase();
    wrap.innerHTML = `
      <label class="${has ? 'live' : ''}" for="s-${name}">${name}${has ? '' : ' — absente'}</label>
      <input type="range" id="s-${name}" min="0" max="1" step="0.01" value="0" ${has ? '' : 'disabled'}>
      <output>0.00</output>`;
    const input = wrap.querySelector('input');
    const out = wrap.querySelector('output');
    input.addEventListener('input', () => {
      const v = Number(input.value);
      out.textContent = v.toFixed(2);
      // 0 rend la main plutot que de forcer a zero : sinon un curseur ramene
      // a zero eteint definitivement cette forme.
      if (v === 0) delete app.manual[name];
      else app.manual[name] = v;
      app.engine.setOverride(Object.keys(app.manual).length ? app.manual : null);
    });
    host.appendChild(wrap);
  }
  app.manual = Object.create(null);
}

$('search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  for (const row of $('shapes').children) {
    row.style.display = !q || row.dataset.name.includes(q) ? '' : 'none';
  }
});

$('reset-shapes').addEventListener('click', () => {
  app.manual = Object.create(null);
  app.engine.setOverride(null);
  for (const input of $('shapes').querySelectorAll('input')) {
    input.value = 0;
    input.nextElementSibling.textContent = '0.00';
  }
});

function buildClipButtons(body) {
  const host = $('clips');
  host.innerHTML = '';
  const names = body.embedded || [];
  if (!names.length) {
    host.innerHTML = '<p class="hint">aucune animation dans ce fichier</p>';
    return;
  }
  for (const name of names) {
    const button = document.createElement('button');
    button.textContent = name.length > 20 ? '…' + name.slice(-18) : name;
    button.title = name;
    button.addEventListener('click', () => {
      const played = app.engine.gestures.play(name);
      $('trace-exec').innerHTML = `clip du modèle · <b>${escapeHtml(name)}</b> · ${played.via}`;
    });
    host.appendChild(button);
  }
}

// ── l'etat interieur ─────────────────────────────────────────────────────────

function buildAffectSliders() {
  const host = $('affect');
  for (const [name, min, max, hint] of AXES) {
    const wrap = document.createElement('div');
    wrap.className = 'slider affect';
    wrap.innerHTML = `
      <label for="a-${name}">${name} <span class="hint">${hint}</span></label>
      <input type="range" id="a-${name}" min="${min}" max="${max}" step="0.01"
             value="${app.affect[name]}">
      <output>${Number(app.affect[name]).toFixed(2)}</output>`;
    const input = wrap.querySelector('input');
    const out = wrap.querySelector('output');
    input.addEventListener('input', () => {
      app.affect[name] = Number(input.value);
      out.textContent = app.affect[name].toFixed(2);
      // Toucher l'etat rend la main a la derivation : c'est tout le propos.
      send(affectRequest());
    });
    host.appendChild(wrap);
  }

  const social = $('social');
  for (const mode of SOCIAL_MODES) {
    const option = document.createElement('option');
    option.value = mode;
    option.textContent = `registre : ${mode}`;
    social.appendChild(option);
  }
  social.value = app.affect.social_mode;
  social.addEventListener('change', () => {
    app.affect.social_mode = social.value;
    send(affectRequest());
  });
}

/** La demande « forme etat », depuis les curseurs. */
function affectRequest() {
  const a = app.affect;
  const request = {
    emotion: { valence: round(a.valence), arousal: round(a.arousal) },
    attention: round(a.attention), confidence: round(a.confidence),
    urgency: round(a.urgency), socialMode: a.social_mode,
  };
  const previous = app.request || {};
  if (previous.gesture) request.gesture = previous.gesture;
  return request;
}

/** Les curseurs suivent l'affect que le directeur a reellement utilise. */
function syncAffectSliders(affect) {
  if (!affect) return;
  for (const [name] of AXES) {
    app.affect[name] = affect[name];
    const input = $(`a-${name}`);
    if (!input) continue;
    input.value = affect[name];
    input.parentElement.querySelector('output').textContent = Number(affect[name]).toFixed(2);
  }
  app.affect.social_mode = affect.social_mode;
  $('social').value = affect.social_mode;
}

// ── la decision : demande -> directive -> Performance -> moteur ──────────────

/** Une demande de JARVIS. Remplace la precedente, exactement comme l'outil. */
function send(request) {
  app.request = request;
  app.directive = parseDirective(request);
  if (app.directive) app.director.setIntent(app.directive, app.t);
  decide();
}

/** Resoudre contre l'etat courant et jouer. Ce que fait `avatar_view.py`. */
function decide() {
  if (!app.director || !app.engine) return;
  const perf = app.director.resolve(app.state, { speechLevel: app.speech, now: app.t });
  app.performance = perf;
  app.engine.perform(perf);
  if (app.request && app.request.intent && perf.affect) syncAffectSliders(perf.affect);
  drawDecision();
  markActive();
}

function drawDecision() {
  const perf = app.performance;
  const d = app.directive;
  $('situation-line').textContent = app.situation || (perf && perf.reason) || '—';

  $('trace-request').textContent = app.request ? JSON.stringify(app.request) : '— réflexe seul —';

  $('trace-resolved').innerHTML = d
    ? [
        d.intent ? `intention <b>${d.intent}</b>` : '<span class="off">aucune intention</span>',
        d.affect ? `affect v${fmt(d.affect.valence)} a${d.affect.arousal.toFixed(2)} `
          + `att${d.affect.attention.toFixed(2)}` : '<span class="off">aucun affect</span>',
        `regard ${d.gaze ? `<b>${d.gaze}</b> (${d.gaze_from})` : '<span class="off">dérivé</span>'}`,
        `geste <b>${d.gesture}</b>`,
      ].join(' · ')
    : (app.request ? '<span class="sub">demande illisible : réflexe seul</span>' : '—');

  if (!perf) return;
  const sub = perf.requested_gesture
    ? `<span class="sub">${perf.requested_gesture} → ${perf.gesture}</span>` : `<b>${perf.gesture}</b>`;
  $('trace-decision').innerHTML =
    `<b>${perf.expression}</b> <span class="num">${perf.intensity.toFixed(2)}</span> · `
    + `regard <b>${perf.gaze}</b> <span class="off">${perf.gaze_source}</span> · `
    + `posture <b>${perf.posture}</b> · geste ${sub}`
    + (perf.accent ? ` · accent <b>${perf.accent}</b>` : '')
    + ` · tempo <span class="num">${perf.tempo.toFixed(2)}</span>`
    + ` · immobilité <span class="num">${perf.stillness.toFixed(2)}</span>`;
  $('trace-lines').textContent = (perf._trace || []).join('\n');
}

const fmt = (v) => (v >= 0 ? '+' : '') + v.toFixed(2);

/** La sortie EFFECTIVE, relue dans le moteur — rafraichie en continu. */
function drawOutput() {
  const engine = app.engine;
  if (!engine) return;
  const out = engine.output();
  const d = engine.decision || {};
  const top = Object.entries(out.shapes)
    .filter(([n]) => !n.startsWith('eyeLook'))
    .sort((a, b) => b[1] - a[1]).slice(0, 5)
    .map(([n, v]) => `${n} ${v.toFixed(2)}`).join(', ') || '—';
  const played = {
    procedural: '<span class="ok">✓ joué</span>',
    clip: '<span class="ok">✓ clip</span>',
    frozen: '<span class="sub">✗ gelé (motion face)</span>',
    none: '<span class="sub">✗ injouable</span>',
    absent: '<span class="sub">✗ aucun os pour le jouer</span>',
  }[d.gesture_played] || `<span class="off">${d.gesture_played || '—'}</span>`;
  const deg = (r) => (r * 180 / Math.PI).toFixed(1);
  $('trace-exec').innerHTML =
    `${d.gesture || '—'} ${played}`
    + (d.accent ? ` · accent ${d.accent} ${d.accent_played ? '<span class="ok">✓</span>' : '<span class="sub">✗</span>'}` : '')
    + ` · ${Object.keys(out.shapes).length} formes · état <b>${out.presence}</b> · ${out.phase}`
    + `<br>tête rx ${deg(out.head.rx)}° ry ${deg(out.head.ry)}° rz ${deg(out.head.rz)}° · `
    + `yeux x ${out.eyes.x.toFixed(2)} y ${out.eyes.y.toFixed(2)} · `
    + (out.speaking > 0.05 ? `<span class="ok">parle ${out.speaking.toFixed(2)}</span>` : '<span class="off">silence</span>')
    + (Object.keys(out.visemes).length ? ` · visèmes natifs ${Object.keys(out.visemes).join(',')}` : '')
    + `<br><span class="off">${top}</span>`;
  const m = engine.metrics;
  $('metrics').textContent = `${fps.toFixed(0)} i/s · image ${frameMs.toFixed(2)} ms · `
    + `moteur ${m.updateMs.toFixed(3)} ms (rig ${m.rigMs.toFixed(3)}, gestes ${m.gesturesMs.toFixed(3)}, `
    + `bouche ${m.lipsyncMs.toFixed(3)}) · ${m.writesPerFrame.toFixed(1)} écritures/image`
    + (app.replaying ? ` · REJEU image ${app.replaying.i}/${app.replaying.recording.dts.length}` : '');
}

function markActive() {
  const r = app.request || {};
  for (const button of $('expressions').children) {
    button.classList.toggle('on', button.dataset.value === r.expression);
  }
  for (const button of $('gazes').children) {
    button.classList.toggle('on', button.dataset.value === r.gaze);
  }
  for (const button of $('gestures').children) {
    button.classList.toggle('on', button.dataset.value === r.gesture);
  }
  for (const button of $('intents').children) {
    button.classList.toggle('on', button.dataset.value === r.intent);
  }
  for (const button of $('states').children) {
    button.classList.toggle('on', button.dataset.value === app.state);
  }
}

// ── les boutons ──────────────────────────────────────────────────────────────

function buttons(host, values, onPick) {
  const node = $(host);
  for (const value of values) {
    const button = document.createElement('button');
    button.textContent = value;
    button.dataset.value = value;
    button.addEventListener('click', () => onPick(value));
    node.appendChild(button);
  }
}

buttons('expressions', EXPRESSIONS, (value) => {
  const previous = app.request || {};
  const request = { expression: value, intensity: Number($('intensity').value) };
  if (previous.gaze) request.gaze = previous.gaze;
  if (previous.gesture) request.gesture = previous.gesture;
  send(request);
});
buttons('gazes', GAZES, (value) => send(Object.assign({}, app.request || affectRequest(), { gaze: value })));
buttons('gestures', GESTURES, (value) => send(Object.assign({}, app.request || affectRequest(), { gesture: value })));

/**
 * Une intention, jouee comme le panneau la jouera : `{"intent": mot}`, lue par
 * le miroir de `parse()`, resolue par le miroir du directeur — rabattement
 * compris. Les curseurs suivent l'etat que l'intention a pose.
 */
buttons('intents', Object.keys(INTENTS), (value) => send({ intent: value }));

buttons('states', [...Object.keys(REFLEX), 'RECONNECTING'], (value) => {
  app.state = value;
  decide();
});

$('unforce').addEventListener('click', () => send(affectRequest()));

$('clear').addEventListener('click', () => {
  app.request = null;
  app.directive = null;
  app.director.clearIntent();
  app.director.affect = null;
  decide();
});

$('intensity').addEventListener('input', (e) => {
  $('intensity-out').textContent = Number(e.target.value).toFixed(2);
  if (app.request && app.request.expression) {
    send(Object.assign({}, app.request, { intensity: Number(e.target.value) }));
  }
});

$('situation').addEventListener('input', (e) => {
  app.situation = e.target.value;
  $('situation-line').textContent = app.situation || '—';
});

function setSpeech(level) {
  app.speech = level;
  $('speech-out').textContent = level.toFixed(2);
  app.engine.speak(level);
}

$('speech').addEventListener('input', (e) => setSpeech(Number(e.target.value)));

$('babble').addEventListener('click', (e) => {
  if (app.babble) {
    clearInterval(app.babble);
    app.babble = null;
    e.target.classList.remove('on');
    setSpeech(0);
    return;
  }
  e.target.classList.add('on');
  let t = 0;
  app.babble = setInterval(() => {
    t += 0.04;
    setSpeech(Math.max(0, Math.sin(t * 7) * Math.sin(t * 1.7)) * 0.8);
  }, 40);
});

/**
 * Executer une demande brute — ce que le modele a reellement produit. Le
 * texte est lu par le miroir de `presence.parse`, donc une demande que le
 * panneau refuserait est refusee ici aussi, et le labo le dit.
 */
$('send').addEventListener('click', () => {
  let parsed;
  try {
    parsed = JSON.parse($('json').value);
  } catch (err) {
    $('trace-resolved').innerHTML = `<span class="sub">JSON invalide : ${escapeHtml(err.message)}</span>`;
    return;
  }
  if (typeof parsed.reason === 'string') {
    app.situation = parsed.reason;
    $('situation').value = parsed.reason;
  }
  send(parsed);
});

for (const button of document.querySelectorAll('[data-frame]')) {
  button.addEventListener('click', () => {
    app.frame = button.dataset.frame;
    if (app.body) {
      stage.frame(app.body, app.frame);
      resize();
    }
    markFrame();
  });
}

function markFrame() {
  for (const other of document.querySelectorAll('[data-frame]')) {
    other.classList.toggle('on', other.dataset.frame === app.frame);
  }
}

// ── scenarios : le temps, l'interruption, la parole ──────────────────────────
//
// Chacun rejoue une situation que les boutons seuls ne produisent pas — parce
// qu'elle depend du TEMPS. Ils passent par `send()` et `setSpeech()`, donc par
// le meme chemin que tout le reste.

const SCENARIOS = {
  'phrase courte': [[0, () => { app.state = 'SPEAKING'; decide(); }],
    ...syllables(0.1, 1.2), [1.5, () => { app.state = 'LISTENING'; decide(); }]],
  'phrase longue': [[0, () => { app.state = 'SPEAKING'; decide(); }],
    ...syllables(0.1, 5.0), [5.4, () => { app.state = 'LISTENING'; decide(); }]],
  'interruption': [[0, () => { app.state = 'SPEAKING'; decide(); }],
    ...syllables(0.1, 1.4), [1.45, () => { setSpeech(0); app.state = 'LISTENING'; decide(); }]],
  'émotion en parlant': [[0, () => { app.state = 'SPEAKING'; send({ intent: 'amuse' }); }],
    ...syllables(0.1, 3.0), [1.5, () => send({ intent: 'warn' })],
    [3.2, () => { app.state = 'LISTENING'; decide(); }]],
  'intention sur intention': [[0, () => send({ intent: 'think' })],
    [0.6, () => send({ intent: 'greet' })], [1.2, () => send({ intent: 'agree' })]],
  'regard explicite en réflexion': [[0, () => { app.state = 'THINKING'; send({ intent: 'think', gaze: 'user' }); }]],
  'urgence': [[0, () => send({ intent: 'amuse', gesture: 'look_around' })],
    [0.5, () => send({ intent: 'warn' })]],
  'réveil puis veille': [[0, () => { app.request = null; app.director.clearIntent(); app.director.affect = null; app.state = 'WAKING'; decide(); }],
    [2.5, () => { app.state = 'SLEEPING'; decide(); }], [5, () => { app.state = 'ACTIVE'; decide(); }]],
};

function syllables(from, to) {
  const out = [];
  for (let t = from; t < to; t += 0.04) {
    out.push([t, () => setSpeech(Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3 + 0.5)) * 0.8)]);
  }
  out.push([to, () => setSpeech(0)]);
  return out;
}

let scenarioTimers = [];
function runScenario(name) {
  for (const timer of scenarioTimers) clearTimeout(timer);
  scenarioTimers = SCENARIOS[name].map(([t, fn]) => setTimeout(fn, t * 1000));
}
buttons('scenarios', Object.keys(SCENARIOS), runScenario);

// ── enregistrer et rejouer ───────────────────────────────────────────────────

$('record').addEventListener('click', () => {
  // Un moteur neuf, une graine neuve : un enregistrement se rejoue depuis sa
  // premiere image, jamais depuis le milieu.
  mount(app.body, 'enregistrement en cours', { manifest: app.manifest }, { record: true });
  $('replay-status').textContent = `enregistrement — graine ${app.engine.rng.seed}`;
});

$('export').addEventListener('click', () => {
  const data = JSON.stringify(app.engine.recorder.export());
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([data], { type: 'application/json' }));
  link.download = `jarvis-seance-${Date.now()}.json`;
  link.click();
});

const recordingPicker = document.createElement('input');
recordingPicker.type = 'file';
recordingPicker.accept = '.json';
recordingPicker.addEventListener('change', async () => {
  const file = recordingPicker.files[0];
  if (!file) return;
  playRecording(JSON.parse(await file.text()));
});
$('import').addEventListener('click', () => recordingPicker.click());

/**
 * Rejouer : d'abord sans rendu, pour PROUVER que le rejeu retombe exactement
 * sur ce qui avait ete enregistre ; puis a l'ecran, image par image.
 */
function playRecording(recording) {
  const check = new AvatarEngine(nullLike(app.body), { seed: recording.seed });
  const result = replay(recording, check);
  mount(app.body, 'rejeu', { manifest: app.manifest }, { seed: recording.seed, record: false });
  app.replaying = { recording, i: 0, byFrame: groupByFrame(recording.events) };
  $('replay-status').textContent = result.mismatches.length
    ? `rejeu : ${result.mismatches.length} écart(s) — ${result.mismatches[0].diff}`
    : `rejeu exact : ${result.frames} images, 0 écart`;
}
window.__lab.playRecording = playRecording;

/** Un corps sans rendu qui ressemble a celui charge, pour rejouer a cote. */
function nullLike(body) {
  const caps = body.capabilities ? body.capabilities() : {};
  return new NullBody({
    parts: body.detectedParts ? [...body.detectedParts] : ['head', 'torso'],
    visemes: caps.visemes || 'arkit',
    head: caps.head !== false,
    manifest: body.manifest || app.manifest,
  });
}

function groupByFrame(events) {
  const map = new Map();
  for (const e of events) {
    if (!map.has(e.frame)) map.set(e.frame, []);
    map.get(e.frame).push(e);
  }
  return map;
}

// ── fichiers ─────────────────────────────────────────────────────────────────

const picker = document.createElement('input');
picker.type = 'file';
picker.accept = '.glb,.gltf,.vrm';
picker.addEventListener('change', () => picker.files[0] && loadFile(picker.files[0]));
$('pick').addEventListener('click', () => picker.click());

const drop = $('drop');
for (const type of ['dragenter', 'dragover']) {
  window.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add('on'); });
}
for (const type of ['dragleave', 'drop']) {
  window.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove('on'); });
}
window.addEventListener('drop', (e) => {
  const file = e.dataTransfer && e.dataTransfer.files[0];
  if (file) loadFile(file);
});

// ── demarrage ────────────────────────────────────────────────────────────────

/**
 * Demarre sur le modele installe quand il est lisible, sur le corps procedural
 * sinon. XHR et pas fetch : sous `jarvis://` fetch echoue.
 */
async function boot() {
  buildAffectSliders();
  $('intensity-out').textContent = Number($('intensity').value).toFixed(2);

  let manifest = window.JARVIS_MANIFEST || null;
  if (!manifest) {
    manifest = await new Promise((resolve) => {
      try {
        const url = new URL('manifest.json', new URL('../', import.meta.url)).href;
        const request = new XMLHttpRequest();
        request.open('GET', url, true);
        request.onload = () => {
          try { resolve(JSON.parse(request.responseText)); } catch { resolve(null); }
        };
        request.onerror = () => resolve(null);
        request.send();
      } catch { resolve(null); }
    });
  }
  app.manifest = manifest || {};
  app.frame = ((manifest || {}).camera || {}).frame || 'face';

  if (manifest && manifest.model && manifest.model.file) {
    const started = performance.now();
    try {
      const url = new URL(`models/${manifest.model.file}`, new URL('../', import.meta.url)).href;
      const gltf = await readModel(renderer, (loader) => loader.loadAsync(url));
      const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);
      if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);
      for (const clip of gltf.animations || []) body.addClip(clip.name, clip);
      mount(body, `${manifest.model.file} (installé)`, {
        gltf, manifest, file: manifest.model.file, loadMs: performance.now() - started,
      });
      renderer.setAnimationLoop(frame);
      return;
    } catch (err) {
      console.warn('[labo] modèle installé illisible :', err.message);
    }
  }
  mount(new ProceduralBody({}), 'corps procédural — déposer un modèle pour commencer',
        { manifest: app.manifest });
  renderer.setAnimationLoop(frame);
}

let last = performance.now();
let fps = 0;
let frameMs = 0;
let lastDraw = 0;

function frame() {
  const now = performance.now();
  let dt = Math.min((now - last) / 1000, 0.1);
  last = now;

  const r = app.replaying;
  if (r) {
    // Le rejeu impose ses pas de temps et ses entrees, image par image.
    if (r.i >= r.recording.dts.length) {
      app.replaying = null;
      $('replay-status').textContent += ' — terminé';
    } else {
      for (const event of r.byFrame.get(r.i) || []) {
        if (event.kind === 'perform') app.engine.perform(event.data);
        else if (event.kind === 'speak') app.engine.speak(event.data);
        else if (event.kind === 'viseme') app.engine.viseme(event.data.name, event.data.weight);
        else if (event.kind === 'override') app.engine.setOverride(event.data);
      }
      dt = r.recording.dts[r.i];
      r.i += 1;
    }
  }

  app.t += dt;
  app.engine.update(dt);
  renderer.render(scene, camera);

  const end = performance.now();
  const spent = end - now;
  fps += ((dt > 0 ? 1 / Math.max(dt, 1e-3) : fps) - fps) * 0.05;
  frameMs += (spent - frameMs) * 0.05;
  if (end - lastDraw > 150) { drawOutput(); lastDraw = end; }
}

window.__lab.decide = decide;
window.__lab.send = send;
boot();
