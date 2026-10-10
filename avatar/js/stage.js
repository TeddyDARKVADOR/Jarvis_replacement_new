/**
 * stage.js — la scene : lumiere, camera, cadrage. Partagee par le panneau et le labo.
 *
 * POURQUOI UN FICHIER A PART
 *   `main.js` et `lab.js` avaient chacun leur copie de l'eclairage et du
 *   cadrage, et une copie qu'on regle d'un cote seulement est un labo qui ne
 *   montre plus ce que le panneau affiche. C'est aussi la moitie « rendu » de
 *   ce qu'un telephone reutilisera telle quelle, a cote de `engine.js`.
 *
 * LE CADRAGE, CALCULE ET PAS ECRIT
 *   Chaque modele arrive dans ses unites, a son origine. On dit a la camera
 *   QUOI regarder (`face`, `portrait`, `bust`, `full`) et elle trouve ou c'est
 *   dans la geometrie chargee. Voir FRAME_FRACTIONS.
 *
 * LA CAMERA A HAUTEUR DES YEUX — CE QUI FAIT LE CONTACT VISUEL
 *   La camera etait placee au centre de la zone cadree et regardait tout
 *   droit. En cadrage `bust`, ce centre est la poitrine : JARVIS regardant
 *   « l'utilisateur » regardait droit devant, donc au-dessus de la camera — il
 *   ne regardait jamais la personne en face. Mesure sur le modele installe.
 *
 *   La camera est donc a la hauteur des YEUX du modele, et le cadrage est
 *   obtenu par un decentrement optique (la chambre photographique, pas une
 *   inclinaison) : les verticales restent droites, et un regard droit devant
 *   arrive exactement dans l'objectif.
 */

import * as THREE from 'three';
import { RoomEnvironment } from '../vendor/environments/RoomEnvironment.js';

/** How much air to leave around whatever is being framed. */
export const FRAME_MARGIN = 1.22;

/**
 * La part de la hauteur du modele que chaque cadrage garde, mesuree depuis le
 * haut — et elle depend de ce qu'EST le modele.
 *
 *   portrait  tete et epaules. Le cadrage d'un visage qu'on doit LIRE a 300 px
 *             de large : en `bust`, un humanoide debout montre sa taille et
 *             ses bras en pose A, et le visage n'occupe plus qu'un dixieme du
 *             panneau. C'est le defaut d'un humanoide en mode visage.
 */
export const FRAME_FRACTIONS = {
  //          face   portrait  bust   full
  humanoid: { face: 0.16, portrait: 0.20, bust: 0.38, full: 1.0 },
  bust:     { face: 0.42, portrait: 0.62, bust: 0.80, full: 1.0 },
  head:     { face: 1.0,  portrait: 1.0,  bust: 1.0,  full: 1.0 },
};

/** Which row of the table this model belongs to. */
export function bodyKind(parts) {
  const has = (p) => parts.includes(p);
  if (has('legs')) return 'humanoid';
  if (has('torso') || has('arms')) return 'bust';
  return 'head';
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {object} [options]
 * @param {object} [options.look]    le bloc `look` du manifeste
 * @param {object} [options.camera]  le bloc `camera` du manifeste
 */
export function createStage(canvas, options = {}) {
  const look = options.look || {};
  const cam = options.camera || {};

  const renderer = new THREE.WebGLRenderer({
    canvas, antialias: true, alpha: true, powerPreference: 'low-power',
  });
  renderer.setClearColor(0x000000, 0);
  // Above 2 the extra pixels are invisible and the GPU cost is not.
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));

  // ── lighting ──────────────────────────────────────────────────────────
  //
  // A photoscanned head is a PBR material, and a PBR material without an
  // environment is plastic. `RoomEnvironment` is a procedural box of emissive
  // panels — no HDR file to ship, generated once into a cubemap.
  //
  // EXPOSURE, AND WHY IT IS THIS LOW
  //   Skin is a pale, low-contrast texture. Lit by an environment AND lamps
  //   and tone-mapped at 1.0, it clips to white and reads as clay. The face
  //   keeps its texture at 0.72 and loses it above about 0.9 — measured.
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = Number(look.exposure) || 0.72;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.45;

  // La cle donne le relief (une pommette a besoin d'une ombre), le liseré
  // detache du fond, la douce remplit l'ombre de la cle cote camera sans
  // l'effacer — un visage entierement dans l'ombre d'un cote se lit comme
  // hostile, ce qui n'est pas un ton qu'on a choisi.
  const key = new THREE.DirectionalLight(0xfff4ea, 1.15);
  key.position.set(1.0, 1.4, 2.0);
  const fill = new THREE.DirectionalLight(0xdfe8ff, 0.28);
  fill.position.set(-1.4, 0.3, 1.6);
  const rim = new THREE.DirectionalLight(new THREE.Color(look.colour || '#7DD3FC'), 0.9);
  rim.position.set(-1.6, 0.6, -1.2);
  scene.add(key, fill, rim, new THREE.AmbientLight(0xffffff, 0.10));

  const camera = new THREE.PerspectiveCamera(cam.fov || 24, 1, 0.01, 60);

  const framing = {
    target: new THREE.Vector3(),
    half: 0.4,
    front: 0,
    top: 0,
    eyeY: null,
    kind: 'head',
    mode: 'face',
  };

  /** Cadrer ce corps, dans ce mode. Relu a chaque changement de modele. */
  function frame(body, requested) {
    const object = body.object3D;
    object.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(object);
    const size = bounds.getSize(new THREE.Vector3());

    const parts = body.detectedParts ? [...body.detectedParts] : ['head', 'torso'];
    const kind = bodyKind(parts);
    const fractions = FRAME_FRACTIONS[kind];
    const mode = fractions[requested] !== undefined ? requested
      : (fractions[cam.frame] !== undefined ? cam.frame : 'face');
    const keep = fractions[mode];

    // Mesure depuis le HAUT : la tete est en haut de tout humanoide.
    const focusHeight = Math.max(size.y * keep, 1e-4);
    framing.target.set(
      (bounds.min.x + bounds.max.x) / 2,
      bounds.max.y - focusHeight / 2,
      (bounds.min.z + bounds.max.z) / 2,
    );
    // La largeur a garder dans le cadre. Un portrait accepte de rogner le bout
    // des epaules — c'est ce que fait tout portrait photographique — et c'est
    // ce qui garde un visage lisible dans une colonne de 300 px, ou c'est la
    // largeur, pas la hauteur, qui fixe la distance.
    const widthFactor = mode === 'portrait' ? 0.85 : (mode === 'face' ? 1.0 : 1.2);
    const focusWidth = Math.min(size.x, focusHeight * widthFactor);
    framing.half = Math.max(focusHeight, focusWidth) / 2 * FRAME_MARGIN;
    framing.front = bounds.max.z;
    framing.top = bounds.max.y;
    framing.eyeY = eyeHeight(body, bounds, framing.target.y, focusHeight);
    // Un portrait se centre sur la TETE, pas sur la boite du corps : des bras
    // en pose A, meme un peu asymetriques, deplacent le milieu de la boite, et
    // le visage glissait vers un bord (mesure : +0,29 de la largeur, male).
    const headX = headCentreX(body);
    if ((mode === 'face' || mode === 'portrait') && kind !== 'head' && headX !== null) {
      framing.target.x = headX;
    }
    framing.kind = kind;
    framing.mode = mode;
    return { kind, mode, size, eyeY: framing.eyeY, target: framing.target.clone() };
  }

  /** Taille du canevas -> distance, et decentrement pour garder les yeux au niveau. */
  function resize(width, height) {
    const w = Math.max(1, width);
    const h = Math.max(1, height);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;

    // La distance qui garde `half` visible verticalement, et horizontalement
    // quand la fenetre est plus etroite que haute.
    const vFov = camera.fov * Math.PI / 180;
    let distance = framing.half / Math.tan(vFov / 2);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
    distance = Math.max(distance, framing.half / Math.tan(hFov / 2));

    const eyeY = framing.eyeY !== null ? framing.eyeY : framing.target.y;
    camera.position.set(framing.target.x, eyeY, framing.front + distance);
    camera.lookAt(framing.target.x, eyeY, framing.front);
    camera.updateProjectionMatrix();

    // Le decentrement : deplacer le cadre sans tourner l'objectif — terme
    // (haut+bas)/(haut-bas) de la matrice de projection. Avec un decalage s,
    // un point a hauteur h s'affiche en y = (h - yeux) / (d tan) - s.
    const halfSpan = distance * Math.tan(vFov / 2);
    let shift = (framing.target.y - eyeY) / halfSpan;
    if ((framing.mode === 'face' || framing.mode === 'portrait') && framing.kind !== 'head') {
      // Un portrait met les yeux au tiers superieur de l'IMAGE, quelle que soit
      // sa forme. Centrer la zone cadree le faisait aussi dans un panneau
      // large, mais dans une colonne etroite c'est la largeur des epaules qui
      // fixe la distance : la zone flottait au milieu d'une image trop haute,
      // avec une bande vide au-dessus de la tete. Le sommet du crane reste
      // dans le cadre, avec une marge.
      const crown = (framing.top - eyeY) / halfSpan;
      shift = -Math.max(0, Math.min(1 / 3, 0.9 - crown));
    }
    camera.projectionMatrix.elements[9] = shift;
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    framing.distance = distance;
    framing.shift = shift;
  }

  return { renderer, scene, camera, framing, frame, resize, lights: { key, fill, rim } };
}

/** Le milieu horizontal du visage : les yeux, sinon l'os de tete, sinon null. */
function headCentreX(body) {
  const nodes = body.nodes || {};
  const at = new THREE.Vector3();
  const eyes = [nodes.eyeLeft, nodes.eyeRight].filter(Boolean);
  if (eyes.length) {
    let x = 0;
    for (const node of eyes) { node.getWorldPosition(at); x += at.x; }
    return x / eyes.length;
  }
  if (nodes.head && !body.headIsModel && nodes.head !== body.object3D) {
    nodes.head.getWorldPosition(at);
    return at.x;
  }
  return null;
}

/**
 * La hauteur des yeux du modele, en coordonnees du monde.
 *
 * Les os des yeux quand il y en a. Sinon l'os de tete, qui est a la base du
 * crane : les yeux sont a peu pres au tiers de la distance jusqu'au sommet.
 * Sinon — tete procedurale, scan sans os — un peu au-dessus du centre cadre.
 */
function eyeHeight(body, bounds, centre, focusHeight) {
  const nodes = body.nodes || {};
  const at = new THREE.Vector3();
  const eyes = [nodes.eyeLeft, nodes.eyeRight].filter(Boolean);
  if (eyes.length) {
    let y = 0;
    for (const node of eyes) { node.getWorldPosition(at); y += at.y; }
    return y / eyes.length;
  }
  if (nodes.head && !body.headIsModel && nodes.head !== body.object3D) {
    nodes.head.getWorldPosition(at);
    return at.y + (bounds.max.y - at.y) * 0.4;
  }
  return centre + focusHeight * 0.08;
}
