/**
 * recorder.js — enregistrer une seance, et la rejouer a l'identique.
 *
 * LA QUESTION A LAQUELLE CE FICHIER REPOND
 *   « Pourquoi le visage de JARVIS etait-il dans cet etat a 12:42:11 ? »
 *
 *   Sans enregistrement, la reponse demande de rejouer la conversation — donc
 *   Gemini, le reseau, et un hasard qui ne repassera jamais par les memes
 *   clignements. Avec, elle demande de rejouer un fichier.
 *
 * CE QUI EST ENREGISTRE, ET CE QUI NE L'EST PAS
 *   Les ENTREES du moteur, pas son etat : chaque Performance, chaque niveau de
 *   voix, chaque viseme, chaque valeur imposee a la main, avec l'image a
 *   laquelle elle est arrivee — plus le pas de temps de chaque image, et la
 *   graine du hasard. C'est tout ce qu'il faut : le moteur est deterministe
 *   une fois ces trois choses fixees (voir rng.js), donc rejouer les entrees
 *   reproduit chaque coefficient a la derniere decimale.
 *
 *   Plus un echantillon de la SORTIE toutes les quelques images, pour pouvoir
 *   lire un enregistrement sans le rejouer — et pour que le rejeu puisse
 *   prouver qu'il retombe exactement dessus.
 *
 * POURQUOI LES PAS DE TEMPS SONT ENREGISTRES
 *   Une image a 16.4 ms et une a 16.9 ms ne donnent pas le meme ressort a la
 *   dixieme decimale. Rejouer a pas fixe donnerait un visage presque identique,
 *   et « presque » ne permet pas de dire si une difference vient du code ou du
 *   rejeu. Un double survit exactement a JSON, donc le pas enregistre est le
 *   pas rejoue.
 */

export const RECORDING_VERSION = 1;

export class Recorder {
  constructor() {
    this.on = false;
    this.truncated = false;
  }

  /**
   * @param {number} seed   la graine du moteur enregistre — sans elle, rien
   *                        ne se rejoue
   * @param {object} meta   ce qui aide a relire : modele, capacites, date
   */
  start(seed, meta = {}, { sampleEvery = 6, maxFrames = 72000 } = {}) {
    this.on = true;
    this.truncated = false;
    this.seed = seed;
    this.meta = Object.assign({ started: new Date().toISOString() }, meta);
    this.sampleEvery = sampleEvery;
    this.maxFrames = maxFrames;
    this.dts = [];
    this.events = [];
    this.samples = [];
  }

  stop() { this.on = false; }

  input(kind, data) {
    if (!this.on) return;
    this.events.push({
      frame: this.dts.length,
      kind,
      data: data === undefined ? null : JSON.parse(JSON.stringify(data)),
    });
  }

  frame(dt) {
    if (!this.on) return;
    if (this.dts.length >= this.maxFrames) {
      // Vingt minutes a 60 images par seconde. Au-dela on s'arrete, et on le
      // dit, plutot que de grossir sans fin dans un panneau ouvert toute la
      // journee.
      this.on = false;
      this.truncated = true;
      return;
    }
    this.dts.push(dt);
  }

  sample(frame, t, read) {
    if (!this.on || frame % this.sampleEvery !== 0) return;
    this.samples.push({ frame, t, out: read() });
  }

  export() {
    return {
      version: RECORDING_VERSION,
      seed: this.seed,
      meta: this.meta,
      truncated: this.truncated,
      dts: this.dts ? this.dts.slice() : [],
      events: this.events ? this.events.slice() : [],
      samples: this.samples ? this.samples.slice() : [],
    };
  }
}

function applyEvent(engine, event) {
  switch (event.kind) {
    case 'perform': engine.perform(event.data); break;
    case 'speak': engine.speak(event.data); break;
    case 'viseme': engine.viseme(event.data.name, event.data.weight); break;
    case 'override': engine.setOverride(event.data); break;
    default: break;
  }
}

/**
 * Rejouer un enregistrement dans un moteur NEUF construit avec sa graine.
 *
 * @param {object} recording   ce que `Recorder.export()` a produit
 * @param {object} engine      `new AvatarEngine(body, { seed: recording.seed })`
 * @param {object} [options]
 * @param {number} [options.untilT]    s'arreter a ce temps (secondes)
 * @param {(engine, frame) => void} [options.onFrame]
 * @returns {{ frames: number, mismatches: Array }}  les echantillons de sortie
 *          qui ne retombent pas exactement sur ceux de l'enregistrement
 */
export function replay(recording, engine, options = {}) {
  if (!recording || recording.version !== RECORDING_VERSION) {
    throw new Error(`enregistrement illisible (version ${recording && recording.version})`);
  }
  const byFrame = new Map();
  for (const event of recording.events) {
    if (!byFrame.has(event.frame)) byFrame.set(event.frame, []);
    byFrame.get(event.frame).push(event);
  }
  const expected = new Map(recording.samples.map((s) => [s.frame, s.out]));
  const mismatches = [];

  let frames = 0;
  for (let i = 0; i < recording.dts.length; i++) {
    for (const event of byFrame.get(i) || []) applyEvent(engine, event);
    engine.update(recording.dts[i]);
    frames += 1;
    const want = expected.get(engine.frame);
    if (want) {
      const got = engine.output();
      const diff = compare(want, got);
      if (diff) mismatches.push({ frame: engine.frame, t: engine.t, diff });
    }
    if (options.onFrame) options.onFrame(engine, engine.frame);
    if (options.untilT !== undefined && engine.t >= options.untilT) break;
  }
  return { frames, mismatches };
}

/** La premiere difference entre deux sorties, ou null. Exacte : aucun epsilon. */
function compare(a, b) {
  for (const key of ['shapes', 'visemes']) {
    const names = new Set([...Object.keys(a[key] || {}), ...Object.keys(b[key] || {})]);
    for (const name of names) {
      if ((a[key] || {})[name] !== (b[key] || {})[name]) {
        return `${key}.${name}: ${(a[key] || {})[name]} != ${(b[key] || {})[name]}`;
      }
    }
  }
  for (const key of ['head', 'eyes']) {
    for (const axis in a[key]) {
      if (a[key][axis] !== b[key][axis]) return `${key}.${axis}: ${a[key][axis]} != ${b[key][axis]}`;
    }
  }
  if (a.gesture !== b.gesture) return `gesture: ${a.gesture} != ${b.gesture}`;
  if (a.accent !== b.accent) return `accent: ${a.accent} != ${b.accent}`;
  return null;
}
