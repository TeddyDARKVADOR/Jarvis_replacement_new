/**
 * lab.js — l'etabli. Meme moteur que le panneau, tout expose.
 *
 * LA REGLE
 *   Importer ce qu'importe `main.js`. Le labo construit le meme `GltfBody`, le
 *   meme `Rig`, les memes `Gestures`, le meme `Idle`, et derive un comportement
 *   avec la meme table que Python. Un labo avec son propre chemin de rendu
 *   serait un labo qui peut etre vert pendant que le produit est casse — et la
 *   seule raison d'en avoir un est de pouvoir s'y fier quand le panneau se
 *   comporte mal.
 *
 * CE QU'IL MONTRE, ET QUI EST LE POINT
 *   Pas seulement ce que le moteur a execute : ce que JARVIS a DECIDE, et le
 *   chemin de l'un a l'autre.
 *
 *       situation   ce qu'on lui a decrit
 *       etat        six nombres continus
 *       decision    visage, regard, posture, rythme — CALCULES depuis l'etat
 *       execution   ce qui a reellement joue, substitutions comprises
 *
 *   La derniere ligne est celle qui se gagne le plus : c'est elle qui dit
 *   « facepalm demande, shake_head joue » quand un clip manque, et sans elle ce
 *   genre de substitution est parfaitement invisible.
 *
 * POURQUOI LES MODELES NE SONT JAMAIS INSTALLES ICI
 *   Deposer un fichier ne change rien sur le disque. `avatar/manifest.json` est
 *   ecrit par `presence.install_model`, qui doit aussi deriver les alias et les
 *   membres du rig. Le labo sert a regarder ; installer est une decision.
 */

import * as THREE from 'three';
import { RoomEnvironment } from '../vendor/environments/RoomEnvironment.js';
import { GltfBody, buildLoader } from './body_gltf.js';
import { VrmBody, isVrm } from './body_vrm.js';
import { ARKIT_NAMES } from './arkit.js';
import { ProceduralBody } from './body_procedural.js';
import { Rig } from './rig.js';
import { LipSync } from './lipsync.js';
import { Gestures } from './gestures.js';
import { EXPRESSIONS, GAZES, face } from './expressions.js';
import { BASELINE, SOCIAL_MODES, deriveFrom } from './affect.js';

/** Le vocabulaire de gestes, miroir de `presence/model.py`. Verifie par le test. */
const GESTURES = [
  'idle', 'look_at_user', 'look_away', 'look_around', 'nod', 'shake_head',
  'tilt_head', 'lean_in', 'lean_back', 'blink_slow', 'sigh',
  'shrug', 'turn', 'bow', 'stretch',
  'wave', 'point', 'present', 'explain', 'think', 'thumbs_up', 'cross_arms',
  'facepalm', 'salute', 'type', 'count_off',
  'stand', 'sit', 'walk', 'step_aside',
];

/** Ce qu'un corps joue sans aucun clip. Miroir de `PROCEDURAL_GESTURES`. */
const PROCEDURAL_GESTURES = new Set([
  'idle', 'look_at_user', 'look_away', 'look_around', 'nod', 'shake_head',
  'tilt_head', 'blink_slow', 'lean_in', 'lean_back', 'sigh', 'shrug',
  'turn', 'bow', 'stretch', 'think',
]);

/** Ce que chaque geste demande au rig. Miroir de `GESTURE_REQUIRES`. */
const GESTURE_NEEDS = {
  idle: 'head', look_at_user: 'head', look_away: 'head', look_around: 'head',
  nod: 'head', shake_head: 'head', tilt_head: 'head', blink_slow: 'head',
  lean_in: 'torso', lean_back: 'torso', sigh: 'torso', shrug: 'torso',
  turn: 'torso', bow: 'torso', stretch: 'torso',
  wave: 'arms', point: 'arms', present: 'arms', explain: 'arms', think: 'arms',
  thumbs_up: 'arms', cross_arms: 'arms', facepalm: 'arms', salute: 'arms',
  type: 'arms', count_off: 'arms',
  stand: 'legs', sit: 'legs', walk: 'legs', step_aside: 'legs',
};

const FRAME_FRACTIONS = {
  humanoid: { face: 0.16, bust: 0.38, full: 1.0 },
  bust: { face: 0.42, bust: 0.80, full: 1.0 },
  head: { face: 1.0, bust: 1.0, full: 1.0 },
};

/** Les cinq axes continus, avec de quoi les lire. */
const AXES = [
  ['valence', -1, 1, 'désagréable → agréable'],
  ['arousal', 0, 1, 'calme → activé'],
  ['attention', 0, 1, 'ailleurs → sur l\'utilisateur'],
  ['confidence', 0, 1, 'hésitant → assuré'],
  ['urgency', 0, 1, 'rien ne presse → il faut agir'],
];

const $ = (id) => document.getElementById(id);

/** Expose a dessein : l'etat du labo doit etre lisible depuis la console et
 *  depuis le banc d'essai qui pilote cette page. */
const app = window.__lab = {
  body: null,
  rig: null,
  lipsync: null,
  gestures: null,
  manual: Object.create(null),          // ce que les curseurs de formes forcent
  affect: Object.assign({}, BASELINE, { social_mode: 'professional' }),
  forced: null,                          // {expression, intensity} ou null
  gaze: null,                            // force, sinon derive
  gesture: 'idle',
  speech: 0,
  situation: '',
  decision: null,                        // la derniere decision derivee
  frame: 'face',
  babble: null,
};

// ── scene ────────────────────────────────────────────────────────────────────

const canvas = $('stage');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setClearColor(0x000000, 0);
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.72;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.45;

const key = new THREE.DirectionalLight(0xffffff, 1.15);
key.position.set(1.0, 1.4, 2.0);
const rim = new THREE.DirectionalLight(0x7DD3FC, 0.9);
rim.position.set(-1.6, 0.6, -1.2);
scene.add(key, rim, new THREE.AmbientLight(0xffffff, 0.12));

const camera = new THREE.PerspectiveCamera(24, 1, 0.01, 60);
const framing = { target: new THREE.Vector3(), half: 0.4, z: 0 };

function resize() {
  const w = Math.max(1, canvas.clientWidth);
  const h = Math.max(1, canvas.clientHeight);
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  const vFov = camera.fov * Math.PI / 180;
  let d = framing.half / Math.tan(vFov / 2);
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
  d = Math.max(d, framing.half / Math.tan(hFov / 2));
  camera.position.set(framing.target.x, framing.target.y, framing.z + d);
  camera.updateProjectionMatrix();
  camera.lookAt(framing.target);
}
new ResizeObserver(resize).observe(canvas);

// ── le corps ─────────────────────────────────────────────────────────────────

function mount(body, label) {
  if (app.body) scene.remove(app.body.object3D);
  app.body = body;
  scene.add(body.object3D);

  app.rig = new Rig((name, weight) => {
    // Un curseur pousse ecrase la couche expression : c'est ce qu'on attend
    // d'un curseur, et la seule facon d'isoler une forme.
    const forced = app.manual[name];
    body.setMorph(name, forced !== undefined ? forced : weight);
  });
  app.lipsync = new LipSync(app.rig);
  app.gestures = new Gestures(body);

  reframe();
  report(body, label);
  buildShapeSliders(body);
  buildClipButtons(body);
  apply();
}

function reframe() {
  const bounds = new THREE.Box3().setFromObject(app.body.object3D);
  const size = bounds.getSize(new THREE.Vector3());
  const parts = app.body.detectedParts ? [...app.body.detectedParts] : ['head', 'torso'];
  const kind = parts.includes('legs') ? 'humanoid'
    : (parts.includes('torso') || parts.includes('arms')) ? 'bust' : 'head';
  const keep = FRAME_FRACTIONS[kind][app.frame];

  const focusHeight = Math.max(size.y * keep, 1e-4);
  framing.target = new THREE.Vector3(
    (bounds.min.x + bounds.max.x) / 2,
    bounds.max.y - focusHeight / 2,
    (bounds.min.z + bounds.max.z) / 2,
  );
  framing.z = bounds.max.z;
  framing.half = Math.max(focusHeight, Math.min(size.x, focusHeight * 1.2)) / 2 * 1.22;
  resize();
}

async function loadFile(file) {
  $('report').textContent = `lecture de ${file.name}…`;
  const started = performance.now();
  try {
    const buffer = await file.arrayBuffer();
    const loader = buildLoader(renderer);
    const gltf = await new Promise((res, rej) => loader.parse(buffer, '', res, rej));
    const manifest = {
      model: { file: file.name, scale: 1, position: [0, 0, 0], morphAliases: {} },
      rig: {},
    };
    const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);
    if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);
    // Les clips du modele deviennent jouables tels quels : c'est la facon la
    // plus rapide de voir ce qu'un asset Mixamo contient vraiment.
    for (const clip of gltf.animations || []) body.addClip(clip.name, clip);
    mount(body, `${file.name} — ${Math.round(performance.now() - started)} ms`);
  } catch (err) {
    $('report').innerHTML = `<span class="bad">échec : ${escapeHtml(err.message)}</span>`;
    console.error(err);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

/**
 * Ce qu'on a choisi de ne pas animer, dit a l'endroit ou on lit ce qu'il y a.
 *
 * Un corps qui a des bras et ne les bouge pas est une decision ; sans cette
 * ligne, c'est une panne, et c'est l'heure suivante passee a chercher pourquoi
 * `wave` ne fait rien sur un modele qui a visiblement des mains.
 */
function frozenNote(body) {
  const rig = (body.manifest && body.manifest.rig) || {};
  if (String(rig.motion || 'full').toLowerCase() !== 'face') return '';
  const frozen = [...(body.detectedParts || [])].filter((p) => p !== 'head');
  if (!frozen.length) return '';
  return `   <span class="bad">au repos : ${frozen.join(', ')}</span>`;
}

function report(body, label) {
  const r = body.report ? body.report() : null;
  if (!r) {
    $('report').textContent = 'corps procédural (aucun modèle)';
    return;
  }
  const found = r.morphsFound;
  const cls = found >= 40 ? 'ok' : found > 0 ? '' : 'bad';
  const lines = [
    label, '',
    `<span class="${cls}">formes pilotables : ${found}/52</span>`,
    `membres    : ${[...body.detectedParts].join(', ')}${frozenNote(body)}`,
    `os         : ${r.bones.join(', ') || 'aucun'}`,
    `animations : ${(body.embedded || []).length}`,
  ];
  if (r.vrm) lines.push(`VRM        : ${r.vrm}`);
  if (r.capabilities) {
    // Ce que le moteur comportemental voit de ce modele — et la seule chose
    // qu'il en voit. `gazeBy` repond a « pourquoi les yeux ne bougent pas ».
    const c = r.capabilities;
    const yes = (v) => (v ? '✓' : '✗');
    lines.push('', `capacites  : ${yes(c.expression)} expression  `
      + `${yes(c.gaze)} regard (${c.gazeBy})  ${yes(c.lipsync)} lip-sync`,
      `             ${yes(c.gesture)} geste  ${yes(c.posture)} posture`);
  }
  if (found === 0) {
    lines.push('', '<span class="bad">aucune forme reconnue : le visage restera figé.</span>',
      'Console → liste des morphs, puis model.morphAliases.');
  } else if (r.morphsMissing.length) {
    lines.push('', `manquantes (${r.morphsMissing.length}) :`,
      r.morphsMissing.slice(0, 10).join(', ') + (r.morphsMissing.length > 10 ? ' …' : ''));
  }
  $('report').innerHTML = lines.join('\n');
}

// ── un curseur par forme ─────────────────────────────────────────────────────

function buildShapeSliders(body) {
  const host = $('shapes');
  host.innerHTML = '';
  const present = new Set(body.morphTargets ? body.morphTargets.keys() : []);

  for (const name of ARKIT_NAMES) {
    const has = present.has(name);
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
      // 0 rend la main a l'expression plutot que de la forcer a zero : sinon un
      // curseur ramene a zero eteint definitivement cette forme.
      if (v === 0) delete app.manual[name];
      else app.manual[name] = v;
    });
    host.appendChild(wrap);
  }
}

$('search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  for (const row of $('shapes').children) {
    row.style.display = !q || row.dataset.name.includes(q) ? '' : 'none';
  }
});

$('reset-shapes').addEventListener('click', () => {
  app.manual = Object.create(null);
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
      app.gestures.play(name);
      $('trace-exec').innerHTML =
        `<span class="ok">▸</span> clip du modèle · <b>${escapeHtml(name)}</b>`;
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
      app.forced = null;
      app.gaze = null;
      apply();
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
    apply();
  });
}

// ── la decision, et sa trace ─────────────────────────────────────────────────

function apply() {
  const derived = deriveFrom(app.affect);
  app.decision = derived;

  const expression = app.forced ? app.forced.expression : derived.expression;
  const intensity = app.forced ? app.forced.intensity : derived.intensity;
  const gaze = app.gaze || derived.gaze;
  const posture = derived.posture;

  const blendshapes = face(expression, intensity, gaze);

  app.rig.setExpression(blendshapes, gaze);
  app.rig.setSlowBlink(expression === 'tired' || posture === 'dormant');
  app.gestures.setPosture(posture);
  app.gestures.setGaze(gaze);
  app.gestures.setAffect({
    tempo: derived.tempo,
    stillness: derived.stillness,
    gazeHold: derived.gaze_hold_s,
  });

  const played = playable(app.gesture);
  app.gestures.play(played);
  app.lipsync.setLevel(app.speech);

  $('json').value = JSON.stringify({
    emotion: { valence: round(app.affect.valence), arousal: round(app.affect.arousal) },
    attention: round(app.affect.attention),
    confidence: round(app.affect.confidence),
    urgency: round(app.affect.urgency),
    socialMode: app.affect.social_mode,
    gesture: app.gesture,
  }, null, 1).replace(/\n\s+/g, ' ');

  drawTrace(derived, { expression, intensity, gaze, posture, played, blendshapes });
  markActive(expression, gaze);
}

const round = (v) => Math.round(v * 100) / 100;

/**
 * Le geste que CE corps peut reellement jouer.
 *
 * Miroir de `Catalogue.can` : un clip installe, ou un geste procedural, et dans
 * les deux cas les os qu'il exige. Sans ca, le labo afficherait un geste demande
 * comme s'il avait joue — exactement le silence que la ligne « exécution »
 * existe pour rompre.
 */
function playable(name) {
  const parts = drivenParts();
  const clips = app.body.clips || {};
  const needs = GESTURE_NEEDS[name] || 'head';
  if (!parts.has(needs)) return 'idle';
  if (clips[name] || PROCEDURAL_GESTURES.has(name)) return name;
  return 'idle';
}

/**
 * Les membres qu'on PILOTE, qui ne sont pas toujours ceux que le modele a.
 *
 * `rig.motion: "face"` tient tout ce qui est sous la nuque au repos. Sans ce
 * filtre, le labo lirait `detectedParts` — donc « ce corps a des bras » — et
 * annoncerait `shrug` comme joue, alors que `gestures.js` remet le canal du
 * buste a zero a l'image suivante. Le labo montrerait un mouvement que le
 * panneau ne fera pas : exactement le silence que la ligne « exécution » existe
 * pour rompre.
 *
 * Miroir de l'intersection que fait `presence/catalog.py`.
 */
function drivenParts() {
  const parts = app.body.detectedParts || new Set(['head', 'torso']);
  const rig = (app.body.manifest && app.body.manifest.rig) || {};
  if (String(rig.motion || 'full').toLowerCase() !== 'face') return parts;
  return new Set(['head']);
}

function drawTrace(derived, played) {
  $('situation-line').textContent = app.situation || '— aucune situation décrite —';

  const a = derived.affect;
  $('trace-affect').innerHTML =
    `valence <span class="num">${a.valence >= 0 ? '+' : ''}${a.valence.toFixed(2)}</span> · ` +
    `arousal <span class="num">${a.arousal.toFixed(2)}</span> · ` +
    `attention <span class="num">${a.attention.toFixed(2)}</span> · ` +
    `confiance <span class="num">${a.confidence.toFixed(2)}</span> · ` +
    `urgence <span class="num">${a.urgency.toFixed(2)}</span> · ` +
    `<span class="off">${a.social_mode}</span>`;

  const source = app.forced
    ? '<span class="sub">visage forcé</span>'
    : '<span class="off">dérivé de l\'état</span>';
  $('trace-decision').innerHTML =
    `<b>${played.expression}</b> <span class="num">${played.intensity.toFixed(2)}</span> · ` +
    `regard <b>${played.gaze}</b> · posture <b>${played.posture}</b> · ` +
    `tempo <span class="num">${derived.tempo.toFixed(2)}</span> · ` +
    `immobilité <span class="num">${derived.stillness.toFixed(2)}</span> &nbsp; ${source}`;

  const shapes = Object.keys(played.blendshapes).length;
  const substitution = played.played !== app.gesture
    ? `<span class="sub">✗ ${app.gesture} injouable → ${played.played}</span>`
    : `<span class="ok">✓</span> geste <b>${played.played}</b>`;
  $('trace-exec').innerHTML =
    `<span class="ok">✓</span> ${shapes} formes · ` +
    `<span class="ok">✓</span> regard · <span class="ok">✓</span> posture · ` +
    `${substitution} · ` +
    (app.speech > 0.01 ? '<span class="ok">✓</span> voix'
                       : '<span class="off">voix silencieuse</span>');
}

function markActive(expression, gaze) {
  for (const button of $('expressions').children) {
    button.classList.toggle('on', !!app.forced && button.dataset.value === expression);
  }
  for (const button of $('gazes').children) {
    button.classList.toggle('on', !!app.gaze && button.dataset.value === gaze);
  }
  for (const button of $('gestures').children) {
    button.classList.toggle('on', button.dataset.value === app.gesture);
  }
}

// ── les boutons ──────────────────────────────────────────────────────────────

function buttons(host, values, onPick) {
  const node = $(host);
  for (const value of values) {
    const button = document.createElement('button');
    button.textContent = value;
    button.dataset.value = value;
    button.addEventListener('click', () => { onPick(value); apply(); });
    node.appendChild(button);
  }
}

buttons('expressions', EXPRESSIONS, (value) => {
  app.forced = { expression: value, intensity: Number($('intensity').value) };
});
buttons('gazes', GAZES, (value) => { app.gaze = value; });
buttons('gestures', GESTURES, (value) => { app.gesture = value; });

$('unforce').addEventListener('click', () => {
  app.forced = null;
  app.gaze = null;
  apply();
});

$('intensity').addEventListener('input', (e) => {
  $('intensity-out').textContent = Number(e.target.value).toFixed(2);
  if (app.forced) {
    app.forced.intensity = Number(e.target.value);
    apply();
  }
});

$('situation').addEventListener('input', (e) => {
  app.situation = e.target.value;
  $('situation-line').textContent = app.situation || '— aucune situation décrite —';
});

$('speech').addEventListener('input', (e) => {
  app.speech = Number(e.target.value);
  $('speech-out').textContent = app.speech.toFixed(2);
  app.lipsync.setLevel(app.speech);
});

$('babble').addEventListener('click', (e) => {
  if (app.babble) {
    clearInterval(app.babble);
    app.babble = null;
    e.target.classList.remove('on');
    app.speech = 0;
    app.lipsync.setLevel(0);
    return;
  }
  e.target.classList.add('on');
  let t = 0;
  app.babble = setInterval(() => {
    t += 0.06;
    app.speech = Math.max(0, Math.sin(t * 7) * Math.sin(t * 1.7)) * 0.8;
    app.lipsync.setLevel(app.speech);
  }, 60);
});

/**
 * Executer une decision brute — les deux formes que JARVIS peut emettre.
 *
 * La forme « etat » remplit les curseurs et rend la main a la derivation ; la
 * forme « visage » force. C'est exactement ce que fait `presence/director.py`,
 * et c'est ce qui rend ce champ utile : on y colle ce que le modele a
 * reellement produit, et on voit ce que le panneau en aurait fait.
 */
$('send').addEventListener('click', () => {
  let parsed;
  try {
    parsed = JSON.parse($('json').value);
  } catch (err) {
    $('trace-exec').innerHTML =
      `<span class="sub">JSON invalide : ${escapeHtml(err.message)}</span>`;
    return;
  }

  const inner = (parsed.emotion && typeof parsed.emotion === 'object') ? parsed.emotion : parsed;
  let touched = false;
  for (const [name] of AXES) {
    const value = inner[name] !== undefined ? inner[name] : parsed[name];
    if (typeof value === 'number') {
      app.affect[name] = value;
      const input = $(`a-${name}`);
      input.value = value;
      input.nextElementSibling.textContent = Number(value).toFixed(2);
      touched = true;
    }
  }
  const mode = parsed.socialMode || parsed.social_mode;
  if (mode && SOCIAL_MODES.includes(mode)) {
    app.affect.social_mode = mode;
    $('social').value = mode;
    touched = true;
  }

  const named = typeof parsed.expression === 'string' ? parsed.expression
    : (typeof parsed.emotion === 'string' ? parsed.emotion : null);
  if (named && EXPRESSIONS.includes(named)) {
    app.forced = {
      expression: named,
      intensity: typeof parsed.intensity === 'number' ? parsed.intensity : 0.5,
    };
  } else if (touched) {
    app.forced = null;
  }

  if (typeof parsed.gaze === 'string' && GAZES.includes(parsed.gaze)) app.gaze = parsed.gaze;
  if (typeof parsed.gesture === 'string' && GESTURES.includes(parsed.gesture)) {
    app.gesture = parsed.gesture;
  }
  if (typeof parsed.reason === 'string') {
    app.situation = parsed.reason;
    $('situation').value = parsed.reason;
  }
  apply();
});

for (const button of document.querySelectorAll('[data-frame]')) {
  button.addEventListener('click', () => {
    app.frame = button.dataset.frame;
    for (const other of document.querySelectorAll('[data-frame]')) {
      other.classList.toggle('on', other === button);
    }
    if (app.body) reframe();
  });
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
 * sinon. La lecture est au mieux, volontairement : le travail du labo commence
 * des qu'on y depose un fichier, et refuser d'ouvrir parce que `manifest.json`
 * manque le rendrait inutile exactement quand on cherche quoi installer.
 */
async function boot() {
  buildAffectSliders();
  $('intensity-out').textContent = Number($('intensity').value).toFixed(2);
  document.querySelector('[data-frame="face"]').classList.add('on');

  let manifest = window.JARVIS_MANIFEST || null;
  if (!manifest) {
    // XHR et pas fetch : sous `jarvis://` fetch echoue, et le labo doit pouvoir
    // montrer le modele DEJA installe — c'est la premiere chose qu'on lui
    // demande apres une installation. Voir avatar/js/xhr_loader.js.
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

  if (manifest && manifest.model && manifest.model.file) {
    try {
      const url = new URL(`models/${manifest.model.file}`,
                          new URL('../', import.meta.url)).href;
      const loader = buildLoader(renderer);
      const gltf = await loader.loadAsync(url);
      const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);
      if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);
      for (const clip of gltf.animations || []) body.addClip(clip.name, clip);
      mount(body, `${manifest.model.file} (installé)`);
      renderer.setAnimationLoop(frame);
      return;
    } catch (err) {
      console.warn('[labo] modèle installé illisible :', err.message);
    }
  }

  mount(new ProceduralBody({}), 'corps procédural — déposer un modèle pour commencer');
  renderer.setAnimationLoop(frame);
}

const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1);
  app.lipsync.update(dt);
  // L'ordre compte : les gestes avancent le repos, qui produit les
  // micro-expressions que le rig doit combiner dans la meme image.
  app.gestures.update(dt);
  app.rig.setMicro(app.gestures.idle.shapes);
  app.rig.update(dt);
  if (app.body.update) app.body.update(dt);
  renderer.render(scene, camera);
}

boot();
