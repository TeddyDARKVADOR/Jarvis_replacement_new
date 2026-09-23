/**
 * body_gltf.js — a real model, wearing the same interface as the fake one.
 *
 * WHAT IT HAS TO ABSORB
 *   Every humanoid on every marketplace names things differently, and none of
 *   them is wrong. Ready Player Me prefixes its morph targets with the mesh
 *   name. Some exports use `browInnerUp`, others `brow_inner_up`, others the
 *   old `Brow_Inner_Up`. ARKit's 52 might be 52 of 128 (Aven), 52 of 165
 *   (Inori), or a MetaHuman's 251 mapped down. Bones are `Head` in Mixamo,
 *   `mixamorigHead` after an FBX round trip, `Head_01` in some store assets.
 *
 *   None of that may reach `rig.js`, `gestures.js` or Python. It is absorbed
 *   here, in three passes, and the manifest only has to carry what the passes
 *   cannot guess.
 *
 * HOW A NAME IS RESOLVED
 *   Every name — the model's and ARKit's — is put through `normalise()` below,
 *   which collapses case, separators, exporter prefixes and the `_L` / `Left`
 *   side conventions onto one key. `Wolf3D_Head.browInnerUp`, `brow_inner_up`,
 *   `Brow Inner Up` and `browDown_L` all land where they should.
 *
 *   The manifest's `morphAliases` is consulted FIRST, before the automatic
 *   match, because it is the explicit override for a mesh that calls a shape
 *   something genuinely different — and an override the automatic rule can
 *   silently beat is not an override.
 *
 *   A shape found by none of them is simply absent, and absent is fine: the rig
 *   writes to it, nothing happens, the expression is a little flatter. That is
 *   a *degradation*, not a failure, and it is why a four-shape robot mesh works
 *   here at all.
 *
 * WHY THE RIG PARTS ARE DETECTED AND NOT DECLARED
 *   `manifest.rig.parts` decides what `presence/catalog.py` will offer JARVIS,
 *   and a human editing that by hand will get it wrong — they will write
 *   `["head","torso","arms","legs"]` for a bust because it sounds complete.
 *   So the loader reports what it actually found, `main.js` prints it, and the
 *   manifest is corrected from evidence.
 */

import * as THREE from 'three';
import { GLTFLoader } from '../vendor/loaders/GLTFLoader.js';
import { KTX2Loader } from '../vendor/loaders/KTX2Loader.js';
import { DRACOLoader } from '../vendor/loaders/DRACOLoader.js';
import { MeshoptDecoder } from '../vendor/libs/meshopt_decoder.module.js';
import { useXhrLoading } from './xhr_loader.js';
import { VrmBody, enableVrm, isVrm } from './body_vrm.js';
import { ARKIT_NAMES } from './arkit.js';

/** Bone name candidates, in order of preference. Lowercased, separators gone. */
/**
 * Miroir de `BONE_HINTS` (presence/inspect.py) : chaque entree du Python est
 * ici, et `presence/selftest.py` le verifie. Celles en plus sont les noms que
 * three.js a assainis (`mixamorig:Head` -> `mixamorigHead`). La convention
 * Biped de 3ds Max (`Bip01_Head`) est la parce que le visage masculin
 * Rocketbox la porte : sans elle, ni ses yeux a os ni ses bras n'etaient
 * trouves, et l'inspecteur annoncait une tete absente.
 */
/** Plus grand gain de calibration accepte : au-dela, une forme extrapolee se deforme. */
export const MAX_MORPH_GAIN = 2.5;

const BONE_HINTS = {
  head:     ['head', 'mixamorighead', 'bip01head', 'bip001head', 'headjoint'],
  neck:     ['neck', 'mixamorigneck', 'bip01neck', 'bip001neck'],
  spine:    ['spine2', 'spine1', 'spine', 'mixamorigspine2', 'mixamorigspine1',
             'mixamorigspine', 'chest', 'upperchest',
             'bip01spine2', 'bip01spine1', 'bip01spine', 'bip001spine2', 'bip001spine1', 'bip001spine'],
  root:     ['hips', 'mixamorighips', 'root', 'armature', 'bip01pelvis', 'bip001pelvis'],
  eyeLeft:  ['lefteye', 'eyeleft', 'mixamoriglefteye', 'eyel', 'bip01leye', 'bip001leye'],
  eyeRight: ['righteye', 'eyeright', 'mixamorigrighteye', 'eyer', 'bip01reye', 'bip001reye'],

  // Les bras, pour la pose de repos. Un modele livre en T-pose sans clip
  // d'attente ressemble a un epouvantail, et c'est l'etat par defaut de tout
  // humanoide telecharge : Mixamo et VRoid exportent la pose de bind.
  armLeftUpper:  ['leftarm', 'leftupperarm', 'mixamorigleftarm', 'upperarmleft', 'bip01lupperarm', 'bip001lupperarm'],
  armRightUpper: ['rightarm', 'rightupperarm', 'mixamorigrightarm', 'upperarmright', 'bip01rupperarm', 'bip001rupperarm'],
  armLeftLower:  ['leftforearm', 'leftlowerarm', 'mixamorigleftforearm', 'bip01lforearm', 'bip001lforearm'],
  armRightLower: ['rightforearm', 'rightlowerarm', 'mixamorigrightforearm', 'bip01rforearm', 'bip001rforearm'],
};

/** What the presence of a bone proves about the body. */
const PART_HINTS = {
  arms: ['leftarm', 'rightarm', 'leftforearm', 'rightforearm', 'lefthand', 'righthand',
         'leftupperarm', 'rightupperarm', 'mixamorigleftarm', 'mixamorigrightarm',
         'bip01lupperarm', 'bip01rupperarm', 'bip01lforearm', 'bip01rforearm',
         'bip001lupperarm', 'bip001rupperarm', 'bip001lforearm', 'bip001rforearm'],
  legs: ['leftupleg', 'rightupleg', 'leftleg', 'rightleg', 'leftfoot', 'rightfoot',
         'leftupperleg', 'rightupperleg', 'mixamorigleftupleg', 'mixamorigrightupleg',
         'bip01lthigh', 'bip01rthigh', 'bip01lcalf', 'bip01rcalf',
         'bip001lthigh', 'bip001rthigh', 'bip001lcalf', 'bip001rcalf'],
  torso: ['spine', 'spine1', 'spine2', 'chest', 'upperchest', 'hips', 'mixamorigspine',
          'bip01spine', 'bip01spine1', 'bip01spine2', 'bip01pelvis',
          'bip001spine', 'bip001spine1', 'bip001spine2', 'bip001pelvis'],
};

/**
 * A trailing side marker, in the two spellings the world actually uses.
 *
 * `browDown_L` and `browDownLeft` are the same shape. A matcher that does not
 * know this throws away 36 of facecap.glb's 52 blendshapes — a real, textured,
 * fully ARKit-rigged human head, discarded over an underscore.
 */
const SIDE_SEPARATED = /[._\s-]([lr])$/;
const SIDE_WORD = /(left|right)$/;

/**
 * The Oculus viseme set, as Ready Player Me and most "game ready" exports name
 * it: `viseme_aa`, `viseme_PP`... Normalised, so `Viseme_AA` and `viseme.aa`
 * land on the same key. `presence/inspect.py` detects the same fifteen.
 */
export const OCULUS_VISEMES = ['sil', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH', 'SS',
  'nn', 'RR', 'aa', 'E', 'I', 'O', 'U'];

/**
 * The one spelling of a name, whatever the exporter called it.
 *
 * Three things are removed, in this order, and the order matters:
 *
 *   1. the exporter's prefix   `Wolf3D_Head.browInnerUp` -> `browInnerUp`
 *                              `mixamorig:Head`          -> `Head`
 *   2. the side marker         `_L` / `.R` / `Left` / `Right`, put back at the
 *                              end in one spelling so both conventions land on
 *                              the same key
 *   3. every separator         `brow_inner_up` -> `browinnerup`
 *
 * Doing (3) before (2) is the bug that costs 36 blendshapes: once the
 * underscore in `browDown_L` is gone, the `l` is the last letter of a word and
 * there is nothing left to recognise.
 *
 * `presence/inspect.py` implements exactly this, and `presence/selftest.py`
 * checks the two against the same table of awkward names — because a renderer
 * that resolves a shape the inspector said was missing, or misses one it
 * promised, is a bug nobody would find by looking at either file alone.
 */
function normalise(name) {
  // Le point est ambigu : il separe un prefixe d'export
  // (`Wolf3D_Head.browInnerUp`) ET un marqueur de cote (`mouthSmile.L`,
  // convention Blender). Couper aveuglement au dernier point reduit la
  // seconde forme a "L". On recolle donc le dernier segment quand il
  // n'est qu'un cote.
  const segments = String(name).trim().split(/[.:]/).filter(Boolean);
  const last = segments[segments.length - 1] || '';
  const tail = (segments.length > 1 && /^[lr]$/i.test(last)
    ? `${segments[segments.length - 2]}_${last}`
    : last).toLowerCase();

  let base = tail;
  let side = '';
  const separated = SIDE_SEPARATED.exec(base);
  if (separated) {
    side = separated[1] === 'l' ? 'left' : 'right';
    base = base.slice(0, separated.index);
  } else {
    const word = SIDE_WORD.exec(base);
    if (word) {
      side = word[1];
      base = base.slice(0, word.index);
    }
  }
  return base.replace(/[^a-z0-9]/g, '') + side;
}

export class GltfBody {
  constructor(gltf, manifest) {
    this.gltf = gltf;
    this.scene = gltf.scene;
    this.manifest = manifest;

    const model = manifest.model || {};
    this.scene.scale.setScalar(Number(model.scale) || 1);
    /** `model.morphGain` : { nomARKit: gain }, la calibration visuelle du modele. */
    this.morphGain = Object.create(null);
    for (const [shape, g] of Object.entries(model.morphGain || {})) {
      const n = Number(g);
      if (Number.isFinite(n) && n > 0) this.morphGain[shape] = Math.min(MAX_MORPH_GAIN, n);
    }
    const p = model.position || [0, 0, 0];
    this.scene.position.set(p[0] || 0, p[1] || 0, p[2] || 0);

    this.morphTargets = new Map();   // nom ARKit -> [{mesh, index}, ...]
    this.visemeTargets = new Map();  // nom Oculus -> [{mesh, index}, ...]
    this.nodes = {};
    this.detectedParts = new Set(['head']);
    this.clips = Object.create(null);
    this.mixer = new THREE.AnimationMixer(this.scene);

    this._mapMorphs();
    this._mapBones();
    this._setupGaze();
  }

  get object3D() { return this.scene; }

  /** Same signature as ProceduralBody.setMorph. That is the entire contract. */
  setMorph(name, weight) {
    // Les regards sont interceptes AVANT les morphs quand ce modele pilote ses
    // yeux par des os. Voir `_setupGaze` : c'est la seule chose que cet
    // adaptateur traduit autrement qu'en ecrivant une forme.
    if (this.gazeByBone && name in this.gaze) {
      this.gaze[name] = weight;
      return;
    }
    const targets = this.morphTargets.get(name);
    if (!targets) return;
    // La calibration de CE modele : un auteur a sculpte un sourire six fois
    // plus petit qu'un autre (Rocketbox : 4 mm aux coins, contre 27 mm), et le
    // meme `happy 0.7` doit se voir sur les deux. Le moteur ecrit toujours
    // dans [0, 1] ; le gain est applique ici, au bord du fichier, et nulle
    // part ailleurs. Borne : au-dela de 2.5, une forme extrapolee se deforme.
    const gain = this.morphGain[name];
    const w = gain ? Math.min(MAX_MORPH_GAIN, weight * gain) : weight;
    for (const { mesh, index } of targets) {
      mesh.morphTargetInfluences[index] = w;
    }
  }

  /**
   * One NATIVE viseme — an Oculus name, sculpted by the model's author.
   *
   * Only called when `capabilities().visemes` says `oculus`: the rig then
   * writes these instead of approximating the mouth from ARKit shapes.
   */
  setViseme(name, weight) {
    const targets = this.visemeTargets.get(name);
    if (!targets) return;
    for (const { mesh, index } of targets) {
      mesh.morphTargetInfluences[index] = weight;
    }
  }

  /** Register one gesture clip, loaded separately. */
  addClip(gesture, clip) {
    this.clips[gesture] = clip;
  }

  /**
   * One frame. Applies the bone gaze, when this model needs one.
   *
   * Called after `rig.update()` by design — the rig has written every shape by
   * then, so the eight `eyeLook*` weights are complete and can be resolved into
   * two rotations in one go.
   */
  update() {
    if (this.gazeByBone) this._applyBoneGaze();
    // Le mixer est avance par gestures.js, qui possede dt.
  }

  // ── le regard, quand les yeux sont des os ────────────────────────────────

  /**
   * Decide comment ce modele regarde, et le decider une fois.
   *
   * POURQUOI C'EST NECESSAIRE
   *   `rig.js` parle ARKit et rien d'autre : il ecrit `eyeLookInLeft` et huit
   *   formes voisines, point. Beaucoup de modeles humanoides — Ready Player Me
   *   en particulier — n'ont PAS ces huit formes : leurs yeux sont des os,
   *   `LeftEye` et `RightEye`, qu'on fait tourner.
   *
   *   Sans traduction ici, le regard de JARVIS ne bougerait que la tete sur ces
   *   modeles-la. Pas d'erreur, pas de message : des yeux qui fixent droit
   *   devant pendant que la tete se tourne, ce qui est precisement l'effet
   *   « mannequin » que tout le reste du systeme essaie d'eviter.
   *
   * POURQUOI ICI ET PAS DANS LE RIG
   *   C'est la definition meme de l'adaptateur. Le moteur comportemental
   *   connait des capacites abstraites — expression, regard, posture, geste,
   *   lip-sync — et ne doit jamais savoir que CE modele-ci a des os a la place
   *   de deux formes. Changer de modele ne doit toucher que ce fichier.
   *
   * POURQUOI LES SIGNES SONT REGLABLES
   *   L'axe qui fait tourner un oeil depend de l'orientation dans laquelle le
   *   rig a ete construit. La convention par defaut ci-dessous couvre les
   *   modeles orientes vers +Z, ce qui est le cas de glTF par specification et
   *   de Ready Player Me en pratique. `manifest.rig.gaze` est la sortie de
   *   secours pour le reste — meme raison que `rig.armRest`, et le labo est ou
   *   on le verifie en trois clics.
   */
  _setupGaze() {
    this.gaze = {
      eyeLookInLeft: 0, eyeLookOutLeft: 0, eyeLookUpLeft: 0, eyeLookDownLeft: 0,
      eyeLookInRight: 0, eyeLookOutRight: 0, eyeLookUpRight: 0, eyeLookDownRight: 0,
    };

    const hasMorphs = Object.keys(this.gaze).some((n) => this.morphTargets.has(n));
    const hasBones = !!(this.nodes.eyeLeft || this.nodes.eyeRight);

    // Les formes gagnent quand elles existent : elles sont plus fines, et
    // l'auteur du modele les a reglees lui-meme.
    this.gazeByBone = !hasMorphs && hasBones;

    const tuning = (this.manifest.rig && this.manifest.rig.gaze) || {};
    //: Amplitude a poids 1. ~26 degres : au-dela un oeil humain ne va pas, et
    //: on voit le blanc.
    this.gazeReach = tuning.reach !== undefined ? tuning.reach : 0.45;
    this.gazeSignY = tuning.signY !== undefined ? tuning.signY : 1;
    this.gazeSignX = tuning.signX !== undefined ? tuning.signX : 1;

    this.eyeRest = new Map();
    for (const side of ['eyeLeft', 'eyeRight']) {
      const node = this.nodes[side];
      if (node) this.eyeRest.set(side, { x: node.rotation.x, y: node.rotation.y });
    }
  }

  _applyBoneGaze() {
    const g = this.gaze;
    // Modele face a +Z : une rotation positive autour de Y vise +X, une
    // rotation positive autour de X vise vers le bas.
    //
    // « In » est anatomique : l'oeil gauche qui rentre va vers le nez, donc
    // vers +X ; l'oeil droit qui rentre va vers -X. C'est pour ca que les deux
    // yeux ne prennent pas le meme signe.
    const pairs = [
      ['eyeLeft', (g.eyeLookInLeft - g.eyeLookOutLeft),
        (g.eyeLookDownLeft - g.eyeLookUpLeft)],
      ['eyeRight', (g.eyeLookOutRight - g.eyeLookInRight),
        (g.eyeLookDownRight - g.eyeLookUpRight)],
    ];

    for (const [side, horizontal, vertical] of pairs) {
      const node = this.nodes[side];
      if (!node) continue;
      const rest = this.eyeRest.get(side);
      node.rotation.y = rest.y + horizontal * this.gazeReach * this.gazeSignY;
      node.rotation.x = rest.x + vertical * this.gazeReach * this.gazeSignX;
    }
  }

  // ── mapping ─────────────────────────────────────────────────────────────

  _mapMorphs() {
    const model = this.manifest.model || {};
    const aliases = model.morphAliases || {};

    /**
     * Des maillages dont on ignore les formes, nommes par le manifeste.
     *
     * POURQUOI C'EST NECESSAIRE
     *   Un modele peut porter une forme correctement nommee et mal transferee.
     *   Sur l'avatar MPFB installe ici, le maillage `tongue01` a son propre
     *   `jawOpen` — mais il ne fait pas suivre la langue a la machoire, il la
     *   pousse HORS de la bouche. Le moteur ecrit `jawOpen` pour la parole, et
     *   JARVIS tire la langue a chaque phrase.
     *
     *   Rien dans le systeme n'est fautif : le nom est bon, la forme existe,
     *   elle deforme bien quelque chose. C'est l'asset qui est mal fait, et un
     *   defaut d'asset se corrige dans le manifeste — jamais dans le moteur,
     *   qui ne doit pas connaitre l'existence d'un maillage appele `tongue01`.
     *
     *   Les dents, elles, ont aussi `jawOpen` et le font correctement. D'ou une
     *   liste et non une regle.
     */
    const muted = new Set(
      (model.muteMeshes || []).map((n) => normalise(n)),
    );
    // L'alias est ecrit "ARKit -> nom dans le mesh" dans le manifeste, parce
    // que c'est le sens dans lequel un humain le lit. On l'inverse ici.
    const aliasByMeshName = new Map();
    for (const arkit in aliases) {
      aliasByMeshName.set(normalise(aliases[arkit]), arkit);
    }

    // Table ARKit normalisee -> nom canonique, construite une fois.
    const canonical = new Map();
    for (const name of ARKIT_NAMES) canonical.set(normalise(name), name);
    const visemes = new Map();
    for (const name of OCULUS_VISEMES) visemes.set(normalise(`viseme_${name}`), name);

    /**
     * Un maillage est-il dans la liste du manifeste ?
     *
     * Comparaison par SUFFIXE, et ce n'est pas de la souplesse gratuite :
     * three.js supprime les points des noms — ils sont reserves dans sa syntaxe
     * de liaison d'animation. Le noeud glTF `Human.tongue01` arrive donc comme
     * `Humantongue01`, sans separateur, et `normalise` n'a plus rien ou couper.
     * Une egalite stricte ne trouve jamais rien, silencieusement.
     *
     * Le suffixe est sur ici parce que la liste est ECRITE A LA MAIN : c'est un
     * choix explicite sur un modele precis, pas une heuristique appliquee a
     * tout. Un nom trop court y attraperait trop de choses — le rapport dit
     * donc ce qui a ete mis en sourdine, pour que ce soit verifiable.
     */
    const isMuted = (name) => {
      const key = normalise(name);
      for (const wanted of muted) {
        if (key === wanted || key.endsWith(wanted)) return true;
      }
      return false;
    };

    this.muted = [];
    this.scene.traverse((node) => {
      const dict = node.morphTargetDictionary;
      if (!dict || !node.morphTargetInfluences) return;
      if (isMuted(node.name)) {
        // Garde une trace : un maillage mis en sourdine sans que personne ne le
        // sache est la prochaine heure perdue a chercher pourquoi une forme
        // n'a aucun effet.
        this.muted.push(node.name);
        return;
      }

      for (const raw in dict) {
        const index = dict[raw];
        const key = normalise(raw);
        // Le manifeste passe en premier : c'est le recours explicite, et un
        // recours qu'une correspondance automatique peut ecraser n'en est pas un.
        const arkit = aliasByMeshName.get(key) || canonical.get(key);

        if (!arkit) {
          const viseme = visemes.get(key);
          if (viseme) {
            if (!this.visemeTargets.has(viseme)) this.visemeTargets.set(viseme, []);
            this.visemeTargets.get(viseme).push({ mesh: node, index });
          }
          continue;
        }
        if (!this.morphTargets.has(arkit)) this.morphTargets.set(arkit, []);
        this.morphTargets.get(arkit).push({ mesh: node, index });
      }
    });
  }

  _mapBones() {
    const byName = new Map();
    this.scene.traverse((node) => {
      if (node.name) byName.set(normalise(node.name), node);
    });

    const declared = (this.manifest.rig && this.manifest.rig.bones) || {};

    for (const key in BONE_HINTS) {
      // Le manifeste gagne toujours : c'est le recours quand la detection se
      // trompe, et un recours qu'on peut ignorer n'en est pas un.
      const forced = declared[key];
      if (forced && byName.has(normalise(forced))) {
        this.nodes[key] = byName.get(normalise(forced));
        continue;
      }
      for (const hint of BONE_HINTS[key]) {
        if (byName.has(hint)) { this.nodes[key] = byName.get(hint); break; }
      }
    }
    if (!this.nodes.root) this.nodes.root = this.scene;

    for (const part in PART_HINTS) {
      if (PART_HINTS[part].some((hint) => byName.has(hint))) this.detectedParts.add(part);
    }

    // Une tete seule sans os de tete — un scan, un buste exporte sans
    // squelette. Le catalogue lui offre `nod`, et sans ceci `nod` ne
    // tournerait rien. Pour un modele qui n'EST qu'une tete, faire tourner le
    // modele entier est exactement tourner la tete. Les canaux du corps ne
    // sont alors ecrits nulle part : la racine et la tete seraient le meme
    // objet, et le report du poids ecraserait le lacet de la tete.
    const bodyless = this.detectedParts.size === 1;
    if (!this.nodes.head && !this.nodes.neck && bodyless) {
      this.nodes.head = this.scene;
      delete this.nodes.root;
      this.headIsModel = true;
    }
  }

  /**
   * What this model can do, in the only vocabulary the engine knows.
   *
   * C'est le contrat de l'adaptateur, dans les deux sens : le moteur demande
   * « sais-tu regarder ? » et non « as-tu un os LeftEye ? ». Changer de modele
   * change les reponses, jamais les questions.
   *
   * `gazeBy` dit COMMENT, pour une seule raison : c'est la premiere chose
   * qu'on veut savoir quand un regard ne bouge pas, et la seule que ni la
   * liste des os ni celle des formes ne donne directement.
   */
  capabilities() {
    const mouth = ['jawOpen', 'mouthFunnel', 'mouthPucker', 'mouthClose'];
    // Les visemes natifs ne comptent que s'ils couvrent l'essentiel : une
    // bouche ouverte, arrondie et fermee. Deux formes isolees ne font pas un
    // jeu de visemes, et les piloter a la place de l'approximation ARKit
    // donnerait une bouche plus pauvre.
    const native = ['aa', 'O', 'PP'].every((v) => this.visemeTargets.has(v));
    const arkitMouth = mouth.some((n) => this.morphTargets.has(n));
    // Une TETE, c'est un os qui tourne. Un modele sans os de tete ni de cou
    // a peut-etre un visage, mais `nod` ne ferait rien : le dire ici est ce
    // qui evite d'annoncer un hochement que personne ne verra.
    const hasHead = !!(this.nodes.head || this.nodes.neck);
    return {
      expression: this.morphTargets.size > 0,
      gaze: this.gazeByBone || Object.keys(this.gaze).some((n) => this.morphTargets.has(n)),
      gazeBy: this.gazeByBone ? 'os' : (this.morphTargets.has('eyeLookInLeft') ? 'formes' : 'aucun'),
      lipsync: arkitMouth || native,
      visemes: native ? 'oculus' : (arkitMouth ? 'arkit' : 'none'),
      head: hasHead,
      gesture: hasHead,
      posture: this.detectedParts.has('torso') && !!this.nodes.spine,
    };
  }

  /** What `main.js` prints so the manifest can be corrected from evidence. */
  report() {
    return {
      morphsFound: this.morphTargets.size,
      visemesFound: [...this.visemeTargets.keys()],
      morphsMissing: ARKIT_NAMES.filter((n) => !this.morphTargets.has(n)),
      bones: Object.keys(this.nodes).filter((k) => this.nodes[k]),
      parts: [...this.detectedParts],
      clips: Object.keys(this.clips),
      capabilities: this.capabilities(),
    };
  }
}

/**
 * Load the model and every installed gesture clip.
 *
 * A clip that fails to load is skipped with a warning and the gesture becomes
 * uninstalled — which `presence/catalog.py` would already have concluded from
 * the missing file, and which the fallback chain then handles. One broken
 * download must never cost the whole body.
 */
/**
 * A GLTFLoader that can read what asset stores actually sell.
 *
 * KTX2/Basis for textures, DRACO and meshopt for geometry are not exotic: they
 * are what
 * "game ready" means in practice, and facecap.glb — a plain three.js sample —
 * already uses KTX2. A loader without them reports
 * "setKTX2Loader must be called before loading KTX2 textures" and the model
 * simply does not appear, which reads as a missing file.
 *
 * Both transcoders are vendored under `avatar/vendor/libs/`, for the same
 * reason three.js itself is: a face that needs a CDN is a face missing on a bad
 * network day.
 */
export function buildLoader(renderer, { vrm = true } = {}) {
  // Avant toute construction de loader : c'est ce qui rend le transport XHR
  // au lieu de fetch, pour ces loaders comme pour tous les autres.
  useXhrLoading();

  const loader = new GLTFLoader();

  const ktx2 = new KTX2Loader()
    .setTranscoderPath(new URL('../vendor/libs/basis/', import.meta.url).href);
  if (renderer) ktx2.detectSupport(renderer);
  loader.setKTX2Loader(ktx2);

  const draco = new DRACOLoader()
    .setDecoderPath(new URL('../vendor/libs/draco/gltf/', import.meta.url).href);
  loader.setDRACOLoader(draco);

  // EXT_meshopt_compression : la troisieme compression courante, apres KTX2
  // (textures) et DRACO (geometrie). facecap.glb l'utilise, et sans elle le
  // message est "setMeshoptDecoder must be called" et le modele n'apparait pas.
  loader.setMeshoptDecoder(MeshoptDecoder);

  // Le greffon VRM est inoffensif sur un .glb ordinaire : il ne fait
  // quelque chose que si le fichier porte l'extension VRMC_vrm. Le brancher
  // toujours evite d'avoir a deviner le format depuis l'extension, qui ment
  // (des VRM circulent en .glb).
  if (vrm) enableVrm(loader);

  return loader;
}

/**
 * Lire un modele, et survivre a un VRM non conforme.
 *
 * Le greffon VRM refuse EN BLOC un fichier auquel manque un os que la
 * specification exige — un buste exporte sans jambes, par exemple. Tout le
 * modele etait alors perdu pour une contrainte qui ne concerne que le
 * squelette humanoide. Le fichier reste un glTF parfaitement lisible : on le
 * relit sans le greffon, et on garde le maillage, la tete et les formes.
 *
 * @param {(loader) => Promise} read   comment lire (URL ou tampon)
 */
export async function readModel(renderer, read) {
  try {
    return await read(buildLoader(renderer));
  } catch (err) {
    if (!/VRM/i.test(String(err && err.message))) throw err;
    console.warn(`[avatar] VRM non conforme (${err.message}) — relu en glTF simple, `
               + 'sans squelette VRM ni expressions VRM');
    const gltf = await read(buildLoader(renderer, { vrm: false }));
    gltf.userData.vrmRejected = err.message;
    return gltf;
  }
}

/**
 * Load the model and every installed gesture clip.
 *
 * A clip that fails to load is skipped with a warning and the gesture becomes
 * uninstalled — which `presence/catalog.py` would already have concluded from
 * the missing file, and which the fallback chain then handles. One broken
 * download must never cost the whole body.
 *
 * `renderer` is needed only so KTX2Loader can ask the GPU which compressed
 * texture formats it supports; without it every KTX2 texture is transcoded to
 * uncompressed RGBA, which works and wastes memory.
 */
export async function loadGltfBody(manifest, baseUrl, renderer) {
  const loader = buildLoader(renderer);
  const url = new URL(`models/${manifest.model.file}`, baseUrl).href;
  const gltf = await readModel(renderer, (l) => l.loadAsync(url));
  // Le format decide de la classe, pas l'extension du fichier.
  const body = isVrm(gltf) ? new VrmBody(gltf, manifest) : new GltfBody(gltf, manifest);

  // Les animations embarquees dans le modele comptent : un Mixamo exporte en
  // GLB porte souvent ses clips, et les ignorer obligerait a reinstaller ce
  // qui est deja la.
  if (!body.embedded) body.embedded = (gltf.animations || []).map((c) => c.name);

  const gestures = manifest.gestures || {};
  await Promise.all(Object.keys(gestures).map(async (name) => {
    const spec = gestures[name] || {};
    try {
      // Deux facons d'installer un geste : un fichier dedie, ou le nom d'un
      // clip deja present dans le modele.
      if (spec.animation) {
        const clip = (gltf.animations || []).find((c) => c.name === spec.animation);
        if (clip) body.addClip(name, clip);
        else console.warn(`[avatar] geste "${name}" : clip "${spec.animation}" absent du modele`);
        return;
      }
      if (!spec.clip) return;
      const asset = await loader.loadAsync(new URL(`gestures/${spec.clip}`, baseUrl).href);
      const clip = asset.animations[spec.index || 0];
      if (clip) body.addClip(name, clip);
      else console.warn(`[avatar] geste "${name}" : aucune animation dans ${spec.clip}`);
    } catch (err) {
      console.warn(`[avatar] geste "${name}" non charge :`, err.message);
    }
  }));

  return body;
}
