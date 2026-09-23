/**
 * main.js — boot, wire, and one render loop. The panel's host glue, nothing more.
 *
 * WHAT LIVES WHERE
 *   engine.js   the avatar engine: a Performance in, a face out. No three.js,
 *               no DOM, no transport — the same object the lab drives.
 *   stage.js    the scene: lights, camera, framing. Shared with the lab.
 *   bridge.js   the transport: `window.JARVIS.perform()` / postMessage.
 *   main.js     this file: read the manifest, build the body, connect the
 *               three, run the loop. The only file that knows it is in a panel.
 *
 *   That split is what a phone reuses: a WebView loads the same page, the
 *   same engine and stage run, and only the host on the other side of the
 *   bridge changes.
 *
 * THE ORDER OF EVENTS, AND WHY IT IS THIS ORDER
 *   1. read the manifest        what body, what look, what gestures
 *   2. open the bridge          before the body: a performance arriving while
 *                               the .glb downloads is kept, not dropped
 *   3. build the body           glTF if a file is named, procedural otherwise
 *   4. build the engine         on that body; the bridge now feeds it
 *
 * WHY A FAILED MODEL FALLS BACK INSTEAD OF FAILING
 *   A 404 on a .glb, a corrupt download, a file whose morph targets are named
 *   in Japanese — each should cost JARVIS a plainer face for one session,
 *   never his face entirely. The procedural body is always constructible.
 */

import { Bridge } from './bridge.js';
import { ProceduralBody } from './body_procedural.js';
import { loadGltfBody } from './body_gltf.js';
import { ARKIT_NAMES } from './arkit.js';
import { AvatarEngine } from './engine.js';
import { createStage } from './stage.js';
import { buildProfile, formatProfile } from './profile.js';

const BASE = new URL('../', import.meta.url);

const EMPTY_MANIFEST = { model: { file: '' }, look: {}, gestures: {}, camera: {}, rig: {} };

/**
 * The manifest, from whoever can supply it.
 *
 * `window.JARVIS_MANIFEST` when the host injected it — the desktop panel and,
 * later, the phone: both already parse `avatar/manifest.json`, so injecting
 * the object they hold means the page and the director cannot disagree about
 * what is installed. `fetch` only when opened over http:// — the standalone
 * demo path. Never under `jarvis://`, where QtWebEngine keeps none of the
 * scheme's flags and `fetch` fails or takes the process down; see
 * `client_desktop/ui/avatar_scheme.py`.
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
    const started = performance.now();
    try {
      const body = await loadGltfBody(manifest, BASE, renderer);
      const profile = buildProfile(body, {
        gltf: body.gltf, manifest, file: manifest.model.file,
        loadMs: performance.now() - started,
      });
      console.info('[avatar] modele charge\n' + formatProfile(profile));
      if (profile.arkit === 0) {
        // Un mesh sans aucune forme reconnue peut bouger la tete mais n'a pas
        // de visage. C'est presque toujours un prefixe a declarer dans
        // morphAliases, et sans ce message le seul symptome est « il ne
        // sourit jamais ».
        console.warn('[avatar] AUCUN blendshape ARKit reconnu — voir morphAliases '
                   + 'dans avatar/manifest.json. Le visage restera fige.');
      }
      return { body, profile };
    } catch (err) {
      console.error('[avatar] modele non charge, repli sur le corps procedural :', err);
    }
  }
  const body = new ProceduralBody(manifest.look || {});
  return { body, profile: buildProfile(body, { manifest }) };
}

async function boot() {
  const manifest = await readManifest();
  const canvas = document.getElementById('stage');
  const stage = createStage(canvas, { look: manifest.look, camera: manifest.camera });
  const { renderer, scene, camera } = stage;

  // Le pont AVANT le corps : le panneau pousse une performance des que sa page
  // se dit chargee, bien avant qu'un .glb soit arrive. Voir bridge.js.
  const bridge = new Bridge();

  const { body, profile } = await buildBody(manifest, renderer);
  scene.add(body.object3D);

  // Enregistre depuis la premiere image — une seance ne se rejoue que depuis
  // son debut, avec sa graine — mais seulement quand l'hote le demande
  // (`JARVIS_AVATAR_DEBUG=1` cote client, ou `?record=1`). Un panneau ouvert
  // toute la journee n'a pas a garder des Mo d'entrees que personne ne lira ;
  // le labo, lui, enregistre toujours.
  const record = !!window.JARVIS_RECORD || new URLSearchParams(location.search).get('record') === '1';
  const engine = new AvatarEngine(body, { record });
  if (record) engine.recorder.sampleEvery = 60;

  const label = document.getElementById('label');
  const setLabel = (text) => {
    if (!label) return;
    label.textContent = text;
    label.classList.toggle('on', !!text);
  };

  bridge.setVisemeSink((name, weight) => engine.viseme(name, weight));
  bridge.setListenSink((level) => engine.listen(level));
  bridge.attach(
    (perf) => {
      engine.perform(perf);
      if (perf.reason !== 'demo') setLabel('');
    },
    (level) => engine.speak(level),
  );

  // Le corps demarre vivant plutot qu'en T-pose figee : la premiere
  // performance peut arriver plusieurs secondes apres le chargement.
  if (!engine.decision) {
    engine.perform({ expression: 'neutral', intensity: 0.1, gesture: 'idle',
                     gaze: 'user', posture: 'relaxed', state: 'ACTIVE' });
  }

  const params = new URLSearchParams(location.search);
  if (params.get('demo') === '1') {
    const playable = [...new Set(
      Object.keys(body.clips || {}).concat(profile.gestures),
    )];
    bridge.startDemo(playable, setLabel);
  }

  // ── framing ─────────────────────────────────────────────────────────────
  const framed = stage.frame(body, (manifest.camera || {}).frame);
  console.info(`[avatar] cadrage : ${framed.kind}/${framed.mode}, boite `
    + `${framed.size.x.toFixed(2)}x${framed.size.y.toFixed(2)}x${framed.size.z.toFixed(2)}, `
    + `yeux y=${framed.eyeY.toFixed(3)}`);

  const resize = () => stage.resize(window.innerWidth, window.innerHeight);
  window.addEventListener('resize', resize, { passive: true });
  resize();

  // ── the loop ────────────────────────────────────────────────────────────

  let last = performance.now();
  const frameStats = { fps: 0, frameMs: 0, renderMs: 0 };

  function frame() {
    const now = performance.now();
    // Plafonne a 100 ms dans le moteur : un onglet qui revient d'un
    // arriere-plan throttle livre un dt enorme.
    const dt = (now - last) / 1000;
    last = now;

    engine.update(dt);
    const renderStart = performance.now();
    renderer.render(scene, camera);
    const end = performance.now();

    const a = 0.05;
    if (dt > 0) frameStats.fps += (1 / dt - frameStats.fps) * a;
    frameStats.frameMs += ((end - now) - frameStats.frameMs) * a;
    frameStats.renderMs += ((end - renderStart) - frameStats.renderMs) * a;
  }

  // ── what the host and the probes may ask ────────────────────────────────

  /** One PNG of the body right now. Rendered and read in the same task. */
  window.JARVIS.snapshot = () => {
    renderer.render(scene, camera);
    return canvas.toDataURL('image/png');
  };

  /** What the body turned out to be — the MEASURED profile, not a promise. */
  window.JARVIS.status = () => ({
    model: manifest.model.file || 'procedural',
    procedural: !manifest.model.file || profile.format === 'procedural',
    morphs: typeof profile.arkit === 'number' ? profile.arkit : 0,
    missing: profile.arkitMissing.length,
    parts: profile.limbs,
    bones: profile.skeleton,
    clips: profile.clips,
    embedded: body.embedded || [],
    frame: framed.mode,
    profile,
  });

  window.JARVIS.profile = () => profile;
  window.JARVIS.profileText = () => formatProfile(profile);

  /** FPS, temps d'image, et le cout de chaque sous-systeme du moteur. */
  window.JARVIS.metrics = () => Object.assign({}, frameStats, engine.metrics);

  /** Les dernieres decisions, telles que le moteur les a jouees. */
  window.JARVIS.trace = () => engine.history.slice();

  /** Ce que le corps a recu a cette image — la sortie effective. */
  window.JARVIS.output = () => engine.output();

  /** Pourquoi cette forme a cette valeur. */
  window.JARVIS.explain = (name) => engine.explain(name);

  /** La seance depuis le demarrage, rejouable a l'identique dans le labo. */
  window.JARVIS.recording = () => engine.recorder.export();

  /**
   * Fige le corps sur un jeu de formes, et rien d'autre. Seul moyen de
   * repondre a « quelle forme fait CA » : en marche, chaque couche reecrit
   * tout a chaque image. `setAnimated(true)` rend la main.
   */
  window.JARVIS.freeze = (shapes, visemes) => {
    renderer.setAnimationLoop(null);
    for (const name of ARKIT_NAMES) body.setMorph(name, 0);
    for (const name in (shapes || {})) body.setMorph(name, shapes[name]);
    if (body.setViseme) for (const name in (visemes || {})) body.setViseme(name, visemes[name]);
    if (body.update) body.update(0);
    engine.rig.invalidate();
    renderer.render(scene, camera);
  };

  /** Le moteur de gestes, pour regler une pose de repos sans relancer la page. */
  window.JARVIS.armRest = (shoulder) => {
    engine.gestures.armRest.shoulder = shoulder;
    engine.gestures._restArms();
    renderer.render(scene, camera);
  };

  /**
   * Stop the frame callback entirely while the panel is minimised.
   * `setAnimationLoop(null)` unhooks from the compositor; the first frame back
   * starts a fresh clock rather than carrying the whole pause as its dt.
   */
  window.JARVIS.setAnimated = (on) => {
    if (!on) { renderer.setAnimationLoop(null); return; }
    last = performance.now();
    engine.rig.invalidate();
    renderer.setAnimationLoop(frame);
  };

  // Exposes pour le labo et les sondes : lire la scene est la seule facon de
  // repondre a « pourquoi ce modele rend-il blanc ».
  window.__scene = scene;
  window.__stage = stage;
  window.__engine = engine;
  window.__gestures = engine.gestures;
  window.__body = body;
  window.__rig = engine.rig;

  renderer.setAnimationLoop(frame);
  document.body.classList.add('ready');
}

boot().catch((err) => {
  console.error('[avatar] demarrage impossible :', err);
  document.body.classList.add('failed');
});
