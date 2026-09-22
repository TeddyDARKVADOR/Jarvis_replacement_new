/**
 * lab.js — the workbench. Same renderer as the panel, everything exposed.
 *
 * THE ONE RULE
 *   Import what `main.js` imports. The lab builds the same `GltfBody`, the same
 *   `Rig`, the same `Gestures`, the same `LipSync`, and resolves expressions
 *   with the same table. A lab with its own rendering path would be a lab that
 *   can be green while the product is broken — and the whole reason to have one
 *   is to trust it when the panel misbehaves.
 *
 * WHAT IT ADDS, AND ONLY THIS
 *   - a file picker and a drop target, so a model can be judged before it is
 *     installed
 *   - one slider per ARKit shape, because "does this model have a working
 *     browInnerUp" is a question no amount of reading the manifest answers
 *   - the twelve expressions and the gesture vocabulary as buttons
 *   - a text box holding the exact JSON the director puts on the wire
 *   - a line saying what is executing right now
 *
 * WHY MODELS ARE READ FROM A FILE AND NEVER INSTALLED
 *   Dropping a file here changes nothing on disk. `avatar/manifest.json` is
 *   written by `presence.install_model`, deliberately, because that is the step
 *   that has to also derive the aliases and the rig parts. The lab is for
 *   looking; installing is a decision.
 */

import * as THREE from 'three';
import { RoomEnvironment } from '../vendor/environments/RoomEnvironment.js';
import { GltfBody, buildLoader } from './body_gltf.js';
import { ARKIT_NAMES } from './arkit.js';
import { VrmBody, isVrm } from './body_vrm.js';
import { ProceduralBody } from './body_procedural.js';
import { Rig } from './rig.js';
import { LipSync } from './lipsync.js';
import { Gestures } from './gestures.js';
import { EXPRESSIONS, GAZES, face } from './expressions.js';

/** The gesture vocabulary, mirrored from `presence/model.py`. Checked by the
 *  selftest, like every other table that exists on both sides. */
const GESTURES = [
  'idle', 'look_at_user', 'look_away', 'look_around', 'nod', 'shake_head',
  'tilt_head', 'lean_in', 'lean_back', 'blink_slow', 'sigh',
  'shrug', 'turn', 'bow', 'stretch',
  'wave', 'point', 'present', 'explain', 'think', 'thumbs_up', 'cross_arms',
  'facepalm', 'salute', 'type', 'count_off',
  'stand', 'sit', 'walk', 'step_aside',
];

const FRAME_FRACTIONS = {
  humanoid: { face: 0.16, bust: 0.38, full: 1.0 },
  bust: { face: 0.42, bust: 0.80, full: 1.0 },
  head: { face: 1.0, bust: 1.0, full: 1.0 },
};

const $ = (id) => document.getElementById(id);

/** Exposed on purpose: the lab's own state has to be readable from the
 *  console, and from the test bench that drives this page. */
const app = window.__lab = {
  body: null,
  rig: null,
  lipsync: null,
  gestures: null,
  manual: Object.create(null),   // ce que les curseurs forcent
  decision: {
    expression: 'neutral', intensity: 0.6, gaze: 'user',
    gesture: 'idle', posture: 'attentive', speech_level: 0,
  },
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
let framing = { target: new THREE.Vector3(), half: 0.4, z: 0 };

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

// ── loading ──────────────────────────────────────────────────────────────────

function mount(body, label) {
  if (app.body) scene.remove(app.body.object3D);
  app.body = body;
  scene.add(body.object3D);

  app.rig = new Rig((name, weight) => {
    // Un curseur pousse ecrase la couche expression : c'est ce que l'on veut
    // d'un curseur, et c'est la seule facon d'isoler une forme.
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
  const body = app.body;
  const bounds = new THREE.Box3().setFromObject(body.object3D);
  const size = bounds.getSize(new THREE.Vector3());
  const parts = body.detectedParts ? [...body.detectedParts] : ['head', 'torso'];
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
  const label = file.name;
  $('report').textContent = `lecture de ${label}…`;
  const started = performance.now();
  try {
    const buffer = await file.arrayBuffer();
    const loader = buildLoader(renderer);
    const gltf = await new Promise((res, rej) => loader.parse(buffer, '', res, rej));
    const manifest = { model: { file: label, scale: 1, position: [0, 0, 0], morphAliases: {} }, rig: {} };
    const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);
    if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);
    // Les clips du modele deviennent jouables tels quels dans le labo : c'est
    // la facon la plus rapide de voir ce qu'un asset Mixamo contient vraiment.
    for (const clip of gltf.animations || []) body.addClip(clip.name, clip);
    mount(body, `${label} — ${Math.round(performance.now() - started)} ms`);
  } catch (err) {
    $('report').innerHTML = `<span class="bad">echec : ${escapeHtml(err.message)}</span>`;
    console.error(err);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

// ── report ───────────────────────────────────────────────────────────────────

function report(body, label) {
  const r = body.report ? body.report() : null;
  if (!r) {
    $('report').textContent = 'corps procedural (aucun modele)';
    return;
  }
  const found = r.morphsFound;
  const cls = found >= 40 ? 'ok' : found > 0 ? '' : 'bad';
  const lines = [
    label,
    '',
    `<span class="${cls}">ARKit reconnus : ${found}/52</span>`,
    `membres          : ${[...body.detectedParts].join(', ')}`,
    `os               : ${r.bones.join(', ') || 'aucun'}`,
    `animations       : ${(body.embedded || []).length}`,
  ];
  if (r.morphsMissing.length && found > 0) {
    lines.push('', `manquants (${r.morphsMissing.length}) :`,
      r.morphsMissing.slice(0, 12).join(', ') + (r.morphsMissing.length > 12 ? ' …' : ''));
  }
  if (found === 0) {
    lines.push('', '<span class="bad">aucun blendshape ARKit : le visage restera fige.</span>',
      'Ouvrir la console pour la liste des morphs du modele,',
      'puis les declarer dans model.morphAliases.');
  }
  $('report').innerHTML = lines.join('\n');
}

// ── sliders, one per shape ───────────────────────────────────────────────────

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
      <label class="${has ? 'live' : ''}" for="s-${name}">${name}${has ? '' : ' — absent'}</label>
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

// ── the model's own clips ────────────────────────────────────────────────────

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
    // Les noms Mixamo sont interminables ; on garde la fin, qui est le nom utile.
    button.textContent = name.length > 22 ? '…' + name.slice(-20) : name;
    button.title = name;
    button.addEventListener('click', () => {
      app.gestures.play(name);
      say(`clip · ${name}`, 'animation du modèle, jouée telle quelle');
    });
    host.appendChild(button);
  }
}

// ── the decision ─────────────────────────────────────────────────────────────

function apply() {
  const d = app.decision;
  const blendshapes = d.blendshapes || face(d.expression, d.intensity, d.gaze);

  app.rig.setExpression(blendshapes, d.gaze);
  app.rig.setSlowBlink(d.expression === 'tired' || d.posture === 'dormant');
  app.gestures.setPosture(d.posture || 'attentive');
  app.gestures.setGaze(d.gaze || 'user');
  app.gestures.play(d.gesture || 'idle');
  app.lipsync.setLevel(d.speech_level || 0);

  $('json').value = JSON.stringify({
    expression: d.expression,
    intensity: Number(d.intensity.toFixed ? d.intensity.toFixed(2) : d.intensity),
    gesture: d.gesture,
    gaze: d.gaze,
    posture: d.posture,
    speech_level: d.speech_level,
  }, null, 1).replace(/\n\s*/g, ' ');

  say(`${d.expression} ${Number(d.intensity).toFixed(2)} · ${d.gesture}`,
      `regard ${d.gaze} · posture ${d.posture} · ${Object.keys(blendshapes).length} formes actives`);
  markActive();
}

function say(title, why) {
  $('now-title').textContent = title;
  $('now-why').textContent = why || '';
}

function markActive() {
  const d = app.decision;
  for (const [host, value] of [['expressions', d.expression], ['gazes', d.gaze], ['gestures', d.gesture]]) {
    for (const button of $(host).children) {
      button.classList.toggle('on', button.dataset.value === value);
    }
  }
}

function buttons(host, values, field) {
  const node = $(host);
  for (const value of values) {
    const button = document.createElement('button');
    button.textContent = value;
    button.dataset.value = value;
    button.addEventListener('click', () => {
      app.decision[field] = value;
      delete app.decision.blendshapes;   // le choix par mot reprend la main
      apply();
    });
    node.appendChild(button);
  }
}

buttons('expressions', EXPRESSIONS, 'expression');
buttons('gazes', GAZES, 'gaze');
buttons('gestures', GESTURES, 'gesture');

$('intensity').addEventListener('input', (e) => {
  app.decision.intensity = Number(e.target.value);
  delete app.decision.blendshapes;
  $('intensity-out').textContent = app.decision.intensity.toFixed(2);
  apply();
});

$('speech').addEventListener('input', (e) => {
  app.decision.speech_level = Number(e.target.value);
  $('speech-out').textContent = app.decision.speech_level.toFixed(2);
  app.lipsync.setLevel(app.decision.speech_level);
});

$('babble').addEventListener('click', (e) => {
  if (app.babble) {
    clearInterval(app.babble); app.babble = null;
    e.target.classList.remove('on');
    app.lipsync.setLevel(0);
    return;
  }
  e.target.classList.add('on');
  let t = 0;
  app.babble = setInterval(() => {
    t += 0.06;
    app.lipsync.setLevel(Math.max(0, Math.sin(t * 7) * Math.sin(t * 1.7)) * 0.8);
  }, 60);
});

$('send').addEventListener('click', () => {
  try {
    const parsed = JSON.parse($('json').value);
    app.decision = Object.assign({
      expression: 'neutral', intensity: 0.5, gaze: 'user',
      gesture: 'idle', posture: 'attentive', speech_level: 0,
    }, parsed);
    apply();
  } catch (err) {
    say('JSON invalide', err.message);
  }
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

// ── file input ───────────────────────────────────────────────────────────────

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

// ── boot ─────────────────────────────────────────────────────────────────────

/**
 * Start on the installed model when one can be read, and on the procedural
 * body otherwise. Reading it is best-effort on purpose: the lab's job starts
 * the moment a file is dropped on it, and refusing to open because
 * `manifest.json` is missing would make it useless in exactly the situation
 * where someone is trying to work out what to install.
 */
async function boot() {
  $('intensity-out').textContent = Number($('intensity').value).toFixed(2);
  document.querySelector('[data-frame="face"]').classList.add('on');

  let manifest = null;
  if (window.JARVIS_MANIFEST) {
    manifest = window.JARVIS_MANIFEST;
  } else {
    // XHR et pas fetch : sous `jarvis://` fetch echoue, et le labo doit
    // pouvoir montrer le modele DEJA installe — c'est la premiere chose qu'on
    // lui demande apres une installation.
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
      const url = new URL(`models/${manifest.model.file}`, new URL('../', import.meta.url)).href;
      const loader = buildLoader(renderer);
      const gltf = await loader.loadAsync(url);
      const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);
      if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);
      for (const clip of gltf.animations || []) body.addClip(clip.name, clip);
      mount(body, `${manifest.model.file} (installé)`);
      renderer.setAnimationLoop(frame);
      return;
    } catch (err) {
      console.warn('[labo] modele installe illisible :', err.message);
    }
  }

  mount(new ProceduralBody({}), 'corps procédural — déposer un modèle pour commencer');
  renderer.setAnimationLoop(frame);
}

const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1);
  app.lipsync.update(dt);
  app.rig.update(dt);
  app.gestures.update(dt);
  if (app.body.update) app.body.update(dt);
  renderer.render(scene, camera);
}

boot();
