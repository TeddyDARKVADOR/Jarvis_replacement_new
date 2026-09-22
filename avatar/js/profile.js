/**
 * profile.js — ce que le modele charge sait REELLEMENT faire.
 *
 * POURQUOI UN PROFIL ET PAS LA LISTE DES OS
 *   Posseder un os `LeftArm` ne veut pas dire pouvoir saluer : il faut un clip,
 *   et que `rig.motion` accepte de bouger les bras. Posseder `viseme_aa` ne
 *   veut pas dire avoir des visemes : il en faut assez pour ouvrir, arrondir
 *   et fermer une bouche. Un rapport qui liste ce qui EXISTE dans le fichier
 *   annonce des capacites theoriques ; celui-ci annonce ce que le moteur va
 *   effectivement piloter, parce qu'il pose les questions au corps et au
 *   catalogue — les memes que le moteur leur pose.
 *
 * CE QUE LE CERVEAU EN SAIT
 *   Rien. Le profil est affiche (labo, hote, journal) ; `presence/` lit le
 *   manifeste, qui dit la meme chose en moins de mots. Changer de visage change
 *   ce rapport, jamais le code qui decide.
 */

import { Catalogue, motionOf } from './catalog.js';

const COMPRESSION = {
  KHR_draco_mesh_compression: 'DRACO',
  EXT_meshopt_compression: 'meshopt',
  KHR_texture_basisu: 'KTX2',
  KHR_mesh_quantization: 'quantization',
};

/**
 * @param {object} body       le corps construit (gltf, vrm, procedural)
 * @param {object} [context]
 * @param {object} [context.gltf]      le glTF parse, pour le format et la compression
 * @param {object} [context.manifest]
 * @param {string} [context.file]
 * @param {number} [context.loadMs]
 */
export function buildProfile(body, context = {}) {
  const manifest = context.manifest || body.manifest || {};
  const caps = typeof body.capabilities === 'function' ? body.capabilities() : {};
  const report = typeof body.report === 'function' ? body.report() : null;
  const json = (context.gltf && context.gltf.parser && context.gltf.parser.json) || {};
  const used = json.extensionsUsed || [];

  let format = 'procedural';
  if (body.vrm) {
    format = used.includes('VRMC_vrm') ? 'VRM 1.0' : 'VRM 0.x';
  } else if (report) {
    format = /\.gltf$/i.test(context.file || '') ? 'glTF' : 'GLB';
    if (context.gltf && context.gltf.userData && context.gltf.userData.vrmRejected) {
      format += ' (VRM refuse)';
    }
  }

  // Ce que le visage PILOTE, dit dans sa propre unite. Un VRM sans noms ARKit
  // n'a pas « 27 formes ARKit » — il a ses expressions, que le moteur
  // alimente depuis l'ARKit. Annoncer le nombre de formes qui POURRAIENT les
  // nourrir serait une capacite theorique.
  let arkit = report ? report.morphsFound : (format === 'procedural' ? 'primitives' : 0);
  let faceBy = report ? 'arkit' : 'primitives';
  if (body.vrm) {
    faceBy = body.direct ? 'vrm-arkit' : 'vrm';
    if (!body.direct) arkit = 0;
  }

  const parts = body.detectedParts ? [...body.detectedParts] : ['head', 'torso'];
  const motion = motionOf(manifest);
  const catalogue = new Catalogue(parts, motion, Object.keys(body.clips || {}));

  return {
    name: manifest.name || context.file || 'corps procedural',
    file: context.file || (manifest.model && manifest.model.file) || '',
    format,
    compression: used.filter((e) => COMPRESSION[e]).map((e) => COMPRESSION[e]),
    arkit,
    faceBy,
    vrmExpressions: body.vrm && body.available ? body.available.size : 0,
    arkitMissing: report ? report.morphsMissing : [],
    eyes: { ok: !!caps.gaze, by: caps.gazeBy || 'aucun' },
    head: caps.head !== undefined ? !!caps.head : true,
    headIsModel: !!body.headIsModel,
    visemes: caps.visemes || 'none',
    visemesFound: report && report.visemesFound ? report.visemesFound : [],
    lipsync: !!caps.lipsync,
    skeleton: report ? report.bones : Object.keys(body.nodes || {}),
    limbs: parts.sort(),
    driven: [...catalogue.parts].sort(),
    frozen: [...catalogue.frozen].sort(),
    motion,
    animations: (body.embedded || []).length,
    clips: Object.keys(body.clips || {}),
    gestures: catalogue.vocabulary,
    muted: body.muted || [],
    loadMs: context.loadMs,
  };
}

/** Le profil en quelques lignes, comme le labo et le journal l'affichent. */
export function formatProfile(p) {
  const yes = (v) => (v ? '✓' : '✗');
  const face = p.faceBy === 'vrm' ? `VRM · ${p.vrmExpressions} expressions (reduction depuis ARKit)`
    : typeof p.arkit === 'number' ? `${p.arkit}/52 ARKit` : String(p.arkit);
  const visemes = p.visemes === 'oculus' ? `✓ natifs (${p.visemesFound.length} Oculus)`
    : p.visemes === 'vrm' ? '✓ natifs (VRM)'
    : p.visemes === 'arkit' ? '~ approximes depuis ARKit'
    : '✗ aucun';
  const lines = [
    ['MODEL', p.name],
    ['FORMAT', p.format + (p.compression.length ? ` · ${p.compression.join(', ')}` : '')],
    ['FACE', face],
    ['EYES', `${yes(p.eyes.ok)} ${p.eyes.by}`],
    ['HEAD', p.head ? (p.headIsModel ? '✓ (le modele entier)' : '✓') : '✗ — hochements sans effet'],
    ['VISEMES', visemes],
    ['BODY', p.limbs.join('/') + (p.frozen.length ? ` — tenus : ${p.frozen.join(', ')}` : '')],
    ['MOTION', p.motion],
    ['ANIMATIONS', String(p.animations)],
    ['GESTES', `${p.gestures.length} : ${p.gestures.join(', ')}`],
  ];
  if (p.muted.length) lines.push(['MUET', p.muted.join(', ')]);
  if (p.loadMs !== undefined) lines.push(['CHARGE', `${Math.round(p.loadMs)} ms`]);
  return lines.map(([k, v]) => `${k.padEnd(10)} ${v}`).join('\n');
}
