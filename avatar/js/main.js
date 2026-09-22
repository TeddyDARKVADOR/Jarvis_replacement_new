/**
 * main.js — boot, wire, and one render loop.
 *
 * THE ORDER OF EVENTS, AND WHY IT IS THIS ORDER
 *   1. read the manifest        what body, what look, what gestures
 *   2. build the body           glTF if a file is named, procedural otherwise
 *   3. build rig + gestures     both read the body through one interface
 *   4. open the bridge          only now: a performance arriving before the
 *                               body exists would be silently dropped, and the
 *                               first performance is the one that says JARVIS
 *                               woke up
 *
 * WHY A FAILED MODEL FALLS BACK INSTEAD OF FAILING
 *   A 404 on a .glb, a corrupt download, a file whose morph targets are named
 *   in Japanese — each of those should cost JARVIS a plainer face for one
 *   session, never his face entirely. The procedural body is always
 *   constructible, so the catch block always has somewhere to go.
 *
 * RESPONSIVE, AND WHY IT IS A CAMERA AND NOT A MEDIA QUERY
 *   This canvas lives in a panel the user drags, resizes, docks to an edge and
 *   will later open on a phone. Its aspect ratio therefore ranges from a tall
 *   column to a wide strip, and no breakpoint list survives that.
 *
 *   So the framing is computed: the camera pulls back by however much the
 *   narrower dimension demands, which keeps the head the same *apparent* size
 *   whatever shape the hole is. A 240x600 column and a 600x240 strip both show
 *   a complete, centred JARVIS. Same reasoning `client_desktop/ui/panel.py`
 *   gives for reflowing off the size it has rather than off a resolution table.
 *
 * WHY THE DEVICE PIXEL RATIO IS CAPPED AT 2
 *   Above 2 the extra pixels are invisible and the GPU cost is not — and this
 *   thing renders for as long as the workstation is on. Same instinct as the
 *   core widget having no particle system.
 */

import * as THREE from 'three';
import { RoomEnvironment } from '../vendor/environments/RoomEnvironment.js';
import { Rig } from './rig.js';
import { LipSync } from './lipsync.js';
import { Gestures } from './gestures.js';
import { Bridge } from './bridge.js';
import { ProceduralBody } from './body_procedural.js';
import { loadGltfBody } from './body_gltf.js';
import { ARKIT_NAMES } from './arkit.js';

const BASE = new URL('../', import.meta.url);

/**
 * How much air to leave around whatever is being framed. 1.0 would touch the
 * edges; a face needs room to lean and turn without clipping its own ear.
 */
const FRAME_MARGIN = 1.22;

/**
 * What fraction of the model's height a framing mode keeps, measured down from
 * the top — and it depends on what the model IS.
 *
 * THE MISTAKE THIS TABLE EXISTS TO AVOID
 *   "The face is the top 16 %" is true of a standing humanoid and nonsense for
 *   a head-only scan, where the top 16 % is the crown of the skull. Framed that
 *   way, facecap.glb fills the panel with forehead — technically the right
 *   fraction of the right box, and useless.
 *
 *   So the fraction is chosen from what the rig detection already found. A body
 *   with legs is a standing figure; a body with a torso and no legs is a bust;
 *   anything else is a head, and a head IS the face.
 */
const FRAME_FRACTIONS = {
  //          face   bust   full
  humanoid: { face: 0.16, bust: 0.38, full: 1.0 },
  bust:     { face: 0.42, bust: 0.80, full: 1.0 },
  head:     { face: 1.0,  bust: 1.0,  full: 1.0 },
};

/** Which row of the table this model belongs to. */
function bodyKind(parts) {
  const has = (p) => parts.includes(p);
  if (has('legs')) return 'humanoid';
  if (has('torso') || has('arms')) return 'bust';
  return 'head';
}

const state = {
  body: null,
  rig: null,
  lipsync: null,
  gestures: null,
  lastGesture: null,
};

const EMPTY_MANIFEST = { model: { file: '' }, look: {}, gestures: {}, camera: {}, rig: {} };

/**
 * The manifest, from whoever can supply it.
 *
 * TWO SOURCES, AND THE ORDER IS NOT A PREFERENCE
 *   `window.JARVIS_MANIFEST`, when the host injected it. That is the desktop
 *   panel and, later, the phone: both already parse `avatar/manifest.json` on
 *   their own side — `presence/catalog.py` has to, in order to tell JARVIS what
 *   he may ask for — so injecting the object they already hold means the page
 *   and the director cannot disagree about what is installed. One read, one
 *   truth. A page that fetched its own copy could load a manifest edited half a
 *   second later and offer a gesture Python had just decided was uninstalled.
 *
 *   `fetch`, when opened directly in a browser over http:// — the standalone
 *   demo path, where there is no host to inject anything.
 *
 * WHY fetch IS NEVER TRIED UNDER `jarvis://`
 *   It does not work, and worse, it is not inert. QtWebEngine 6.7 accepts
 *   `QWebEngineUrlScheme.registerScheme()` and silently keeps none of it: the
 *   page's origin comes out as `jarvis://` with no host and
 *   `isSecureContext === false`, so the scheme is never CORS-enabled and every
 *   `fetch` on it fails — and in some call paths takes the process down with it
 *   (STATUS_STACK_BUFFER_OVERRUN). ES modules and XMLHttpRequest are unaffected,
 *   which is why the renderer loads and three.js can still read a .glb.
 *
 *   So the protocol is checked rather than the failure caught. `client_desktop/
 *   ui/avatar_scheme.py` documents the same finding from the Python side.
 */
async function readManifest() {
  if (window.JARVIS_MANIFEST && typeof window.JARVIS_MANIFEST === 'object') {
    return Object.assign({}, EMPTY_MANIFEST, window.JARVIS_MANIFEST);
  }

  if (!/^https?:$/.test(location.protocol)) {
    console.info('[avatar] aucun manifeste injecte et pas de fetch possible sur '
               + `${location.protocol} — corps procedural`);
    return EMPTY_MANIFEST;
  }

  try {
    const response = await fetch(new URL('manifest.json', BASE).href, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return Object.assign({}, EMPTY_MANIFEST, await response.json());
  } catch (err) {
    console.warn('[avatar] manifeste illisible, corps procedural :', err.message);
    return EMPTY_MANIFEST;
  }
}

async function buildBody(manifest, renderer) {
  if (manifest.model && manifest.model.file) {
    try {
      const body = await loadGltfBody(manifest, BASE, renderer);
      const report = body.report();
      console.info('[avatar] modele charge', report);
      if (report.morphsFound === 0) {
        // Un mesh sans aucune forme reconnue peut bouger la tete mais n'a pas
        // de visage. On le dit, fort : c'est presque toujours un prefixe de
        // morph a declarer dans morphAliases, et sans ce message la seule
        // symptomatique est "il ne sourit jamais".
        console.warn('[avatar] AUCUN blendshape ARKit reconnu — voir morphAliases '
                   + 'dans avatar/manifest.json. Le visage restera fige.');
      }
      return body;
    } catch (err) {
      console.error('[avatar] modele non charge, repli sur le corps procedural :', err);
    }
  }
  return new ProceduralBody(manifest.look || {});
}

async function boot() {
  const manifest = await readManifest();
  const look = manifest.look || {};

  const canvas = document.getElementById('stage');
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    powerPreference: 'low-power',
  });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const cam = manifest.camera || {};
  const camera = new THREE.PerspectiveCamera(cam.fov || 24, 1, 0.01, 60);

  // ── lighting ────────────────────────────────────────────────────────────
  //
  // A photoscanned head is a PBR material, and a PBR material without an
  // environment is plastic: its roughness and metalness have nothing to
  // reflect, so skin comes out flat and dead whatever the lamps do.
  // `RoomEnvironment` is a procedural box of emissive panels — no HDR file to
  // ship, no network, generated once into a cubemap and then free.
  //
  // The two directional lights stay on top of it for shape: an environment
  // lights everything evenly, which reads as overcast, and a face needs a key
  // to have a cheekbone.
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;

  // EXPOSURE, AND WHY IT IS THIS LOW
  //   Skin is a pale, low-contrast texture. Lit by a bright environment AND two
  //   directional lights AND tone-mapped at 1.0, it clips to white and the model
  //   reads as untextured clay — which is exactly what it looked like here
  //   before this number was measured rather than guessed. The face keeps its
  //   texture at 0.72 and loses it above about 0.9.
  renderer.toneMappingExposure = 0.72;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  // L'environnement sert a donner de la matiere aux reflets, pas a eclairer :
  // a pleine intensite il aplatit tout, parce qu'il vient de partout a la fois.
  scene.environmentIntensity = 0.45;

  const key = new THREE.DirectionalLight(0xffffff, 1.15);
  key.position.set(1.0, 1.4, 2.0);
  const rim = new THREE.DirectionalLight(new THREE.Color(look.colour || '#7DD3FC'), 0.9);
  rim.position.set(-1.6, 0.6, -1.2);   // derriere : le lisere qui detache du fond
  scene.add(key, rim, new THREE.AmbientLight(0xffffff, 0.12));

  // Le pont AVANT le corps : le panneau pousse une performance des que sa page
  // se dit chargee, bien avant qu'un .glb soit arrive. Voir bridge.js.
  const bridge = new Bridge();

  const body = await buildBody(manifest, renderer);
  scene.add(body.object3D);
  state.body = body;

  state.rig = new Rig((name, weight) => body.setMorph(name, weight));
  state.lipsync = new LipSync(state.rig);
  state.gestures = new Gestures(body);

  const label = document.getElementById('label');
  const setLabel = (text) => {
    if (!label) return;
    label.textContent = text;
    label.classList.toggle('on', !!text);
  };

  bridge.setVisemeSink((name, weight) => state.lipsync.setViseme(name, weight));
  bridge.attach(
    (perf) => applyPerformance(perf, setLabel),
    (level) => state.lipsync.setLevel(level),
  );

  // Le corps demarre vivant plutot qu'en T-pose figee : la premiere performance
  // peut arriver plusieurs secondes apres le chargement.
  applyPerformance(
    { expression: 'neutral', intensity: 0.1, gesture: 'idle', gaze: 'user', posture: 'relaxed' },
    () => {},
  );

  const params = new URLSearchParams(location.search);
  if (params.get('demo') === '1') {
    const playable = [...new Set(
      Object.keys(body.clips || {}).concat(
        ['idle', 'nod', 'shake_head', 'tilt_head', 'look_away', 'look_around',
         'lean_in', 'lean_back', 'think', 'shrug', 'turn', 'bow', 'stretch', 'sigh'],
      ),
    )];
    bridge.startDemo(playable, setLabel);
  }

  // ── framing ─────────────────────────────────────────────────────────────

  // ── framing, computed from the model and not from the manifest ──────────
  //
  // WHY THE BOUNDING BOX AND NOT COORDINATES
  //   Every model arrives in its own units, at its own origin, facing its own
  //   way. facecap.glb is a head 0.3 units tall sitting near zero; a Mixamo
  //   humanoid is 1.7 units tall standing on the floor; a VRM is metres with
  //   the feet at y=0. Hand-written camera coordinates are therefore correct
  //   for exactly one file, and the first thing that breaks when the user
  //   swaps the model — which is the one operation this whole design exists to
  //   make easy.
  //
  //   So the camera is told WHAT to look at ("face", "bust", "full") and works
  //   out where that is from the geometry it actually loaded.

  const bounds = new THREE.Box3().setFromObject(body.object3D);
  const size = bounds.getSize(new THREE.Vector3());

  const parts = body.detectedParts ? [...body.detectedParts] : ['head', 'torso'];
  const kind = bodyKind(parts);
  const fractions = FRAME_FRACTIONS[kind];
  const mode = fractions[cam.frame] !== undefined ? cam.frame : 'face';
  const keep = fractions[mode];

  // Mesure depuis le HAUT : la tete est en haut de tout humanoide, alors que
  // "le centre" est la taille sur un corps entier et le nez sur un buste.
  const focusHeight = Math.max(size.y * keep, 1e-4);
  const target = new THREE.Vector3(
    (bounds.min.x + bounds.max.x) / 2,
    bounds.max.y - focusHeight / 2,
    (bounds.min.z + bounds.max.z) / 2,
  );
  const focusWidth = Math.min(size.x, focusHeight * 1.2);
  const half = Math.max(focusHeight, focusWidth) / 2 * FRAME_MARGIN;

  console.info(`[avatar] cadrage : ${kind}/${mode}, boite `
    + `${size.x.toFixed(2)}x${size.y.toFixed(2)}x${size.z.toFixed(2)}, `
    + `cible y=${target.y.toFixed(3)}`);

  function resize() {
    const w = Math.max(1, window.innerWidth);
    const h = Math.max(1, window.innerHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;

    // La distance qui garde `half` visible verticalement...
    const vFov = camera.fov * Math.PI / 180;
    let distance = half / Math.tan(vFov / 2);
    // ...et, quand la fenetre est plus etroite que haute, celle qui le garde
    // visible HORIZONTALEMENT. On prend la plus grande des deux : c'est ce qui
    // fait qu'une colonne de 240 px de large ne coupe pas les oreilles.
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
    distance = Math.max(distance, half / Math.tan(hFov / 2));

    camera.position.set(target.x, target.y, bounds.max.z + distance);
    camera.updateProjectionMatrix();
    camera.lookAt(target);
  }

  window.addEventListener('resize', resize, { passive: true });
  resize();

  // ── the loop ────────────────────────────────────────────────────────────

  const clock = new THREE.Clock();

  /**
   * Stop the frame callback entirely while the panel is minimised.
   *
   * `setAnimationLoop(null)` is not the same as skipping work inside the loop:
   * it unhooks from the compositor, so nothing is scheduled at all. A WebGL
   * context ticking over behind a maximised editor is a GPU cost with no
   * viewer, and this window spends most of its life exactly there.
   *
   * `clock.getDelta()` is called on resume and discarded — otherwise the first
   * frame back carries the whole pause as its dt and every smoothing jumps to
   * its target at once.
   */
  /**
   * One PNG of whatever the body looks like right now, as a data URL.
   *
   * Renders synchronously and reads the canvas in the same task, which is the
   * one ordering that works without `preserveDrawingBuffer: true` — the buffer
   * is cleared on composite, not on read, so anything asynchronous gets a
   * transparent image and no error.
   *
   * This exists because the alternative is grabbing the window, and grabbing
   * the window photographs whatever is stacked on top of it. A bug report about
   * a face needs the face.
   */
  window.JARVIS.snapshot = () => {
    renderer.render(scene, camera);
    return canvas.toDataURL('image/png');
  };

  /**
   * What the body actually turned out to be, for anyone who needs to know.
   *
   * The host prints this, the lab displays it, and a bug report that includes
   * it answers the three questions that otherwise take an afternoon: did the
   * model load, how many of its blendshapes were recognised, and which
   * gestures can this rig physically perform.
   */
  window.JARVIS.status = () => ({
    model: manifest.model.file || 'procedural',
    procedural: !manifest.model.file,
    morphs: state.body.report ? state.body.report().morphsFound : 0,
    missing: state.body.report ? state.body.report().morphsMissing.length : 52,
    parts: state.body.detectedParts ? [...state.body.detectedParts] : ['head', 'torso'],
    bones: state.body.nodes ? Object.keys(state.body.nodes).filter((k) => state.body.nodes[k]) : [],
    clips: Object.keys(state.body.clips || {}),
    embedded: state.body.embedded || [],
    frame: mode,
  });

  /**
   * Fige le corps sur un jeu de formes, et rien d'autre.
   *
   * Arrete la boucle, met les 52 coefficients a zero, ecrit ceux qu'on donne,
   * rend une image. C'est le seul moyen de repondre a « quelle forme fait CA » :
   * en marche, le repos, le lip-sync et les micro-expressions reecrivent tout a
   * chaque image, et une capture montre la somme au lieu de la cause.
   *
   * `setAnimated(true)` rend la main.
   */
  window.JARVIS.freeze = (shapes) => {
    renderer.setAnimationLoop(null);
    for (const name of ARKIT_NAMES) state.body.setMorph(name, 0);
    for (const name in (shapes || {})) state.body.setMorph(name, shapes[name]);
    if (state.body.update) state.body.update(0);
    renderer.render(scene, camera);
  };

  /** Le moteur de gestes, pour regler une pose de repos sans relancer la page. */
  window.JARVIS.armRest = (shoulder) => {
    state.gestures.armRest.shoulder = shoulder;
    state.gestures._restArms();
    renderer.render(scene, camera);
  };

  window.JARVIS.setAnimated = (on) => {
    if (!on) { renderer.setAnimationLoop(null); return; }
    clock.getDelta();
    renderer.setAnimationLoop(frame);
  };

  function frame() {
    // Plafonne a 100 ms : un onglet qui revient d'un arriere-plan throttle
    // livre un dt enorme, et un dt enorme fait sauter chaque lissage a sa
    // cible d'un coup — un visage qui claque a chaque fois qu'on revient sur
    // le panneau. Meme probleme que core_widget.py resout avec monotonic().
    const dt = Math.min(clock.getDelta(), 0.1);

    state.lipsync.update(dt);
    // L'ordre compte : les gestes avancent le repos, qui produit les
    // micro-expressions que le rig doit combiner dans la meme image.
    state.gestures.update(dt);
    state.rig.setMicro(state.gestures.idle.shapes);
    state.rig.update(dt);
    if (state.body.update) state.body.update(dt);

    renderer.render(scene, camera);
  }

  // Exposee pour le labo et pour les sondes de diagnostic : lire la scene
  // est la seule facon de repondre a "pourquoi ce modele rend-il blanc".
  window.__scene = scene;
  window.__gestures = state.gestures;
  window.__body = state.body;

  renderer.setAnimationLoop(frame);
  document.body.classList.add('ready');
}

/**
 * One `Performance` from `presence/`, applied.
 *
 * The gesture is replayed only when it *changes*. A director that resends the
 * same performance — which it does, on every state refresh — must not restart
 * the nod each time, or JARVIS nods forever.
 */
function applyPerformance(perf, setLabel) {
  const blendshapes = perf.blendshapes || {};
  state.rig.setExpression(blendshapes, perf.gaze || 'user');
  state.rig.setSlowBlink(perf.expression === 'tired' || perf.posture === 'dormant');

  if (perf.posture) state.gestures.setPosture(perf.posture);
  if (perf.gaze) state.gestures.setGaze(perf.gaze);

  // Les parametres continus derives de l'affect. Ce sont eux qui font qu'entre
  // deux decisions le personnage RESSEMBLE a son etat au lieu de simplement
  // bouger. Voir presence/affect.py et avatar/js/idle.js.
  state.gestures.setAffect({
    tempo: perf.tempo,
    stillness: perf.stillness,
    gazeHold: perf.gaze_hold_s,
  });

  const gesture = perf.gesture || 'idle';
  if (gesture !== state.lastGesture) {
    state.gestures.play(gesture);
    state.lastGesture = gesture;
  }

  if (typeof perf.speech_level === 'number') state.lipsync.setLevel(perf.speech_level);

  if (setLabel && perf.reason === 'demo') return;   // la demo ecrit son propre libelle
  if (setLabel) setLabel('');
}

boot().catch((err) => {
  console.error('[avatar] demarrage impossible :', err);
  document.body.classList.add('failed');
});
