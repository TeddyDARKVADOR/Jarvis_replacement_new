/**
 * body_procedural.js — a face with no mesh, so there is a face on day one.
 *
 * WHY THIS EXISTS
 *   Everything else in `avatar/` is built to drive a downloaded humanoid with
 *   52 ARKit morph targets. That model costs money or an afternoon, and until
 *   it is there the entire pipeline — director, catalogue, rig, lip sync,
 *   gestures — would have nothing to move and no way to be judged.
 *
 *   So this is a JARVIS drawn out of primitives that answers to the *same 52
 *   names*. `rig.js` cannot tell the difference, `gestures.js` cannot tell the
 *   difference, and neither can Python. Install Aven, James, a Ready Player Me
 *   avatar or a MetaHuman export and this file stops being used — nothing above
 *   it changes, which is the only honest test that the seam is real.
 *
 * WHAT IT DELIBERATELY IS NOT
 *   Not a human face. A low-polygon human is worse than no human — the uncanny
 *   valley is a cliff, and a wireframe sitting in the corner of a workstation
 *   all day has to be readable at 220 px and restful at three in the morning.
 *   So it is a holographic construct: a shell, two eyes, two brows, a mouth
 *   made of light. That reads *more* clearly than a cheap face, not less,
 *   because every element is doing one job.
 *
 * HOW 52 NAMES BECOME SEVEN MOVING PARTS
 *   Most of the 52 have no analogue here — there are no cheeks to puff and no
 *   tongue. `setMorph` accepts every name and quietly ignores what it cannot
 *   draw, which is exactly what a real mesh missing a shape does too. The
 *   twenty-odd it *does* implement are the ones that carry the twelve
 *   expressions: brows, lids, gaze, jaw, smile, frown, funnel, press. Test it —
 *   `?demo=1` cycles all twelve — and they are distinguishable.
 */

import * as THREE from 'three';

const MOUTH_SEGMENTS = 13;
const BROW_SEGMENTS = 5;

/** Additive, unlit, depth-writing off: the hologram look, and the reason this
 *  reads on any background including the transparent one the panel uses. */
function glow(colour, opacity = 1) {
  return new THREE.MeshBasicMaterial({
    color: new THREE.Color(colour),
    transparent: true,
    opacity,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    toneMapped: false,
  });
}

export class ProceduralBody {
  /**
   * @param {object} look  the `look` block of manifest.json
   */
  constructor(look = {}) {
    const main = look.colour || '#7DD3FC';
    const dim = look.dim || '#1E4C63';
    const accent = look.accent || '#C4B5FD';

    this.colours = { main, dim, accent };
    this.morphs = Object.create(null);   // nom ARKit -> poids courant

    // ── la hierarchie que gestures.js pilote ─────────────────────────────
    // Les noms sont ceux qu'un rig humanoide porterait, pour que le meme code
    // de gestes marche sur les deux corps sans condition.
    this.root = new THREE.Group();
    this.spine = new THREE.Group();
    this.head = new THREE.Group();

    this.root.add(this.spine);
    this.spine.add(this.head);
    this.head.position.y = 0.30;

    this.nodes = { root: this.root, spine: this.spine, head: this.head, neck: this.head };

    this._buildBust(main, dim);
    this._buildSkull(main, dim);
    this._buildBrows(main);
    this._buildEyes(main, accent);
    this._buildMouth(main);

    this.t = 0;
  }

  get object3D() { return this.root; }

  /** The rig writes here. Unknown names are accepted and ignored — see header. */
  setMorph(name, weight) {
    this.morphs[name] = weight;
  }

  // ── construction ────────────────────────────────────────────────────────

  _buildSkull(main, dim) {
    // Coque en fil de fer : la silhouette. Une sphere pleine ferait une boule,
    // un icosaedre subdivise deux fois fait une tete.
    const shell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.30, 2),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(main), wireframe: true,
        transparent: true, opacity: 0.16,
        blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
      }),
    );
    shell.scale.set(0.86, 1.06, 0.92);
    this.head.add(shell);
    this.shell = shell;

    // Halo interne : donne du volume sans dessiner un visage.
    const halo = new THREE.Mesh(new THREE.SphereGeometry(0.235, 24, 18), glow(dim, 0.34));
    halo.scale.set(0.9, 1.04, 0.9);
    this.head.add(halo);
    this.halo = halo;

    // Anneau frontal — l'element qui rappelle le coeur 2D deja a l'ecran.
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.255, 0.004, 8, 64), glow(main, 0.5));
    band.rotation.x = Math.PI / 2;
    band.position.y = 0.10;
    this.head.add(band);
    this.band = band;
  }

  _buildBust(main, dim) {
    // Epaules : deux arcs, pas un torse. Un buste plein volerait la lisibilite
    // du visage, qui est ce que l'utilisateur lit.
    const shoulders = new THREE.Mesh(
      new THREE.TorusGeometry(0.34, 0.006, 8, 48, Math.PI),
      glow(main, 0.38),
    );
    shoulders.rotation.set(Math.PI / 2, 0, Math.PI);
    shoulders.position.y = -0.16;
    shoulders.scale.set(1, 1, 0.62);
    this.spine.add(shoulders);

    const collar = new THREE.Mesh(new THREE.TorusGeometry(0.13, 0.004, 8, 40), glow(dim, 0.55));
    collar.rotation.x = Math.PI / 2;
    collar.position.y = -0.02;
    this.spine.add(collar);

    // Le coeur, sous la gorge : l'ecot au reacteur d'arc de MARK LIII.
    const core = new THREE.Mesh(new THREE.CircleGeometry(0.022, 24), glow(main, 0.85));
    core.position.set(0, -0.09, 0.05);
    this.spine.add(core);
    this.core = core;
  }

  _buildBrows(main) {
    this.brows = [];
    for (const side of [-1, 1]) {
      const group = new THREE.Group();
      const segments = [];
      for (let i = 0; i < BROW_SEGMENTS; i++) {
        const seg = new THREE.Mesh(new THREE.BoxGeometry(0.019, 0.007, 0.007), glow(main, 0.8));
        // i = 0 est le segment interne (cote nez) : c'est lui que browInnerUp
        // leve, et c'est cette asymetrie interne/externe qui distingue la
        // tristesse de la colere.
        const x = side * (0.045 + i * 0.022);
        seg.position.set(x, 0, 0.215);
        group.add(seg);
        segments.push(seg);
      }
      group.position.y = 0.075;
      this.head.add(group);
      this.brows.push({ side, group, segments });
    }
  }

  _buildEyes(main, accent) {
    this.eyes = [];
    for (const side of [-1, 1]) {
      const group = new THREE.Group();
      group.position.set(side * 0.082, 0.015, 0.20);

      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.030, 0.0035, 8, 32), glow(main, 0.75));
      group.add(ring);

      const iris = new THREE.Mesh(new THREE.CircleGeometry(0.017, 20), glow(accent, 0.9));
      iris.position.z = 0.002;
      group.add(iris);

      const pupil = new THREE.Mesh(new THREE.CircleGeometry(0.0065, 16), glow('#FFFFFF', 1));
      pupil.position.z = 0.004;
      group.add(pupil);

      // La paupiere est un disque opaque au meme blending : en additif, "fermer"
      // ne peut pas se peindre en noir, alors on ecrase l'oeil verticalement et
      // on le voile. C'est la seule facon honnete de fermer un oeil de lumiere.
      this.head.add(group);
      this.eyes.push({ side, group, ring, iris, pupil });
    }
  }

  _buildMouth(main) {
    this.mouth = { segments: [], group: new THREE.Group() };
    for (let i = 0; i < MOUTH_SEGMENTS; i++) {
      const seg = new THREE.Mesh(new THREE.BoxGeometry(0.0125, 0.006, 0.006), glow(main, 0.8));
      this.mouth.segments.push(seg);
      this.mouth.group.add(seg);
    }
    this.mouth.group.position.set(0, -0.085, 0.205);
    this.head.add(this.mouth.group);
  }

  // ── the 52 names, applied ───────────────────────────────────────────────

  update(dt) {
    this.t += dt;
    const w = (name) => this.morphs[name] || 0;

    this._applyBrows(w);
    this._applyEyes(w);
    this._applyMouth(w);

    // Le coeur pulse avec la voix : jawOpen est deja la meilleure mesure de
    // "il parle en ce moment" disponible ici, et elle est exacte par
    // construction puisque c'est lipsync.js qui l'ecrit.
    const voice = Math.max(w('jawOpen'), w('mouthFunnel') * 0.6);
    const breath = 0.72 + Math.sin(this.t * 1.5) * 0.10;
    this.core.scale.setScalar(breath + voice * 0.85);
    this.core.material.opacity = 0.55 + voice * 0.45;
    this.band.material.opacity = 0.34 + voice * 0.30;
    this.halo.material.opacity = 0.26 + voice * 0.22;
  }

  _applyBrows(w) {
    for (const brow of this.brows) {
      const isLeft = brow.side < 0;
      const inner = w('browInnerUp');
      const outer = w(isLeft ? 'browOuterUpLeft' : 'browOuterUpRight');
      const down = w(isLeft ? 'browDownLeft' : 'browDownRight');

      brow.segments.forEach((seg, i) => {
        // t = 0 au segment interne, 1 a l'externe. Interpoler le long du
        // sourcil est ce qui permet a browInnerUp et browOuterUp de produire
        // deux formes differentes et pas deux hauteurs.
        const t = i / (BROW_SEGMENTS - 1);
        const lift = inner * (1 - t) * 0.030 + outer * t * 0.030 - down * 0.024;
        seg.position.y = lift;
        seg.rotation.z = brow.side * (outer - inner) * 0.55;
        seg.material.opacity = 0.55 + Math.min(1, inner + outer + down) * 0.35;
      });
    }
  }

  _applyEyes(w) {
    for (const eye of this.eyes) {
      const isLeft = eye.side < 0;
      const blink = w(isLeft ? 'eyeBlinkLeft' : 'eyeBlinkRight');
      const wide = w(isLeft ? 'eyeWideLeft' : 'eyeWideRight');
      const squint = w(isLeft ? 'eyeSquintLeft' : 'eyeSquintRight');

      const openness = Math.max(0.04, 1 - blink) * (1 + wide * 0.30 - squint * 0.40);
      eye.group.scale.y = openness;
      eye.group.scale.x = 1 + wide * 0.10;

      // ARKit est anatomique : `In` va vers le nez. Pour l'oeil gauche
      // (side = -1) cela veut dire +x, pour le droit -x.
      const lookIn = w(isLeft ? 'eyeLookInLeft' : 'eyeLookInRight');
      const lookOut = w(isLeft ? 'eyeLookOutLeft' : 'eyeLookOutRight');
      const lookUp = w(isLeft ? 'eyeLookUpLeft' : 'eyeLookUpRight');
      const lookDown = w(isLeft ? 'eyeLookDownLeft' : 'eyeLookDownRight');

      const dx = (lookIn - lookOut) * -eye.side * 0.013;
      const dy = (lookUp - lookDown) * 0.011;
      eye.iris.position.set(dx, dy, 0.002);
      eye.pupil.position.set(dx, dy, 0.004);

      const visible = 1 - blink;
      eye.iris.material.opacity = 0.35 + visible * 0.55;
      eye.pupil.material.opacity = visible;
      eye.ring.material.opacity = 0.30 + visible * 0.45 + wide * 0.15;
    }
  }

  _applyMouth(w) {
    const open = w('jawOpen');
    const smile = (w('mouthSmileLeft') + w('mouthSmileRight')) / 2;
    const smileL = w('mouthSmileLeft');
    const smileR = w('mouthSmileRight');
    const frown = (w('mouthFrownLeft') + w('mouthFrownRight')) / 2;
    const funnel = w('mouthFunnel');
    const pucker = w('mouthPucker');
    const press = (w('mouthPressLeft') + w('mouthPressRight')) / 2;
    const stretch = (w('mouthStretchLeft') + w('mouthStretchRight')) / 2;
    const close = w('mouthClose');
    const shift = w('mouthLeft') - w('mouthRight');

    const width = 0.115 * (1 + stretch * 0.28 - pucker * 0.45 - funnel * 0.22);

    this.mouth.segments.forEach((seg, i) => {
      const t = i / (MOUTH_SEGMENTS - 1);          // 0 = gauche, 1 = droite
      const u = t * 2 - 1;                          // -1 .. +1

      // Le sourire est un arc, pas une translation. Les coins montent, le
      // centre reste : c'est l'inverse de la moue.
      const corner = u < 0 ? smileL : smileR;
      const curve = (corner * 0.026 - frown * 0.022) * (u * u);

      // La bouche s'ouvre en amande : le centre descend plus que les coins.
      const opening = open * 0.045 * (1 - u * u * 0.72);

      seg.position.x = u * width + shift * 0.012;
      seg.position.y = curve - opening * 0.5;
      seg.scale.y = 1 + opening * 26 - press * 0.35 - close * 0.30;
      seg.scale.x = 1 - pucker * 0.35;
      seg.rotation.z = -u * (smile - frown) * 0.5;
      seg.material.opacity = 0.45 + Math.min(1, smile + frown + open + press + funnel) * 0.45;
    });
  }
}
