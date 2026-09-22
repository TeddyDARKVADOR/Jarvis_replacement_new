/**
 * performance.js — le TEMPS d'un visage. Pas son contenu.
 *
 * TROIS NIVEAUX, ET CE FICHIER EST CELUI DU MILIEU
 *   TARGET       ce que `presence/` a decide : 52 coefficients, un nom
 *   PERFORMANCE  comment y aller — qui part quand, a quelle vitesse, combien
 *                de temps on tient, comment on relache            <- ICI
 *   OUTPUT       ce que `rig.js` ecrit reellement dans le modele, apres
 *                les autres couches (parole, accents, repos, paupieres)
 *
 *   Ce fichier ne change AUCUNE valeur cible. Il decide seulement la
 *   trajectoire de chacune.
 *
 * LE PROBLEME QUE CE FICHIER EXISTE POUR REGLER
 *   `presence/` decide un visage juste : 52 coefficients corrects, douze
 *   expressions distinctes, verifiees. Et le resultat peut malgre tout se lire
 *   comme un interrupteur, pour deux raisons independantes :
 *
 *   * les 52 arrivent TOUS EN MEME TEMPS. L'ironie monte dans les yeux avant
 *     d'atteindre la bouche ; la concentration commence par les sourcils ; la
 *     surprise, elle, arrive d'un bloc. `SIGNATURES` porte ces decalages.
 *
 *   * ils arrivent tous A LA MEME VITESSE. Une surprise monte en une fraction
 *     de seconde et ne tient pas ; une pensee s'installe lentement ; l'ironie
 *     arrive progressivement et repart plus lentement encore. `ENVELOPES`
 *     porte ces rythmes — montee, maintien naturel, niveau residuel, relache.
 *
 * POURQUOI UN RESSORT ET PLUS UNE EXPONENTIELLE
 *   `w += (cible - w) * k` part a sa vitesse MAXIMALE a l'instant ou la cible
 *   change : c'est un coin dans la courbe, et l'oeil le voit comme un
 *   tressaillement au debut de chaque expression. Un ressort critiquement
 *   amorti part a vitesse nulle, accelere, puis se pose sans depasser — la
 *   courbe en S d'un muscle. Et la vitesse est conservee quand la cible change
 *   en cours de route, donc une expression interrompue repart sans cassure.
 *
 *   La mise a jour est la solution EXACTE du ressort sur `dt`, pas une
 *   integration : elle est independante de la cadence, comme l'exponentielle
 *   qu'elle remplace. Un onglet throttle a 4 Hz arrive au meme endroit, au
 *   meme moment, qu'un ecran a 144 Hz.
 *
 * LES TROIS FREQUENCES, ET CELLE-CI EST LA DEUXIEME
 *   intention      secondes            presence/, par le fil
 *   expression     centaines de ms     ICI
 *   micro-animation par image          rig.js, gaze.js, idle.js
 *
 * POURQUOI LES GROUPES SONT DERIVES DU NOM
 *   Les 52 formes ARKit sont nommees par region : `browInnerUp`, `mouthSmile_L`,
 *   `cheekSquintRight`. Le prefixe EST le groupe, donc une liste ecrite a la
 *   main serait une liste a maintenir pour une information deja presente.
 */

/** Le groupe d'une forme, lu dans son nom. `mouthSmile_L` -> `mouth`. */
export function groupOf(name) {
  const match = /^[a-z]+/.exec(String(name));
  return match ? match[0] : 'other';
}

/**
 * Le decalage de chaque groupe, par expression, en secondes.
 *
 * Zero absent = zero : un groupe qu'une signature ne nomme pas part tout de
 * suite. Les valeurs sont petites a dessein — au-dela de ~0.25 s le visage ne
 * se compose plus, il se decompose, et on voit deux expressions successives au
 * lieu d'une seule qui s'installe.
 *
 *   amused     l'ironie monte dans les yeux, la bouche suit. Sans ce retard,
 *              l'ironie se lit comme de la joie.
 *   thinking   les sourcils menent, la bouche reste presque neutre et arrive en
 *              dernier — une bouche qui pense en meme temps que le front donne
 *              une grimace de concentration, pas de la reflexion.
 *   surprised  presque rien. Une surprise QUI SE COMPOSE n'est pas une
 *              surprise ; c'est la seule expression ou le « snap » est juste.
 *   sad        la bouche tombe la premiere, le reste suit. L'inverse d'amused.
 *   tired      tout traine, sans ordre particulier.
 */
export const SIGNATURES = {
  neutral:   {},
  amused:    { mouth: 0.13, cheek: 0.15, nose: 0.13 },
  happy:     { mouth: 0.05, cheek: 0.07 },
  proud:     { mouth: 0.09, cheek: 0.10 },
  surprised: { brow: 0.00, eye: 0.01, mouth: 0.03, jaw: 0.03 },
  concerned: { mouth: 0.10, jaw: 0.10, cheek: 0.08 },
  serious:   { mouth: 0.04, jaw: 0.04 },
  thinking:  { eye: 0.05, mouth: 0.16, jaw: 0.16, cheek: 0.12 },
  confused:  { eye: 0.04, mouth: 0.14, jaw: 0.14 },
  sad:       { brow: 0.08, eye: 0.10, cheek: 0.12 },
  angry:     { mouth: 0.05, nose: 0.06, jaw: 0.05 },
  tired:     { brow: 0.06, eye: 0.08, mouth: 0.12, jaw: 0.12, cheek: 0.10 },
};

/** Le plus long decalage de la table. Sert aux controles, et de garde-fou. */
export const MAX_DELAY_S = 0.25;

/**
 * Le rythme de chaque visage.
 *
 *   attack    secondes pour parcourir 90 % du chemin en montant
 *   release   idem en redescendant — vers une nouvelle cible ou vers le repos
 *   hold      combien de temps le visage tient a pleine intensite avant de
 *             retomber de lui-meme. Absent = tenu jusqu'a la decision suivante.
 *   residual  ce qui reste apres cette retombee naturelle, en fraction
 *
 * D'OU VIENNENT CES NOMBRES
 *   Les deux extremes sont documentes. La surprise est la plus breve des
 *   emotions : montee en moins de 0.2 s, rarement tenue au-dela d'une seconde
 *   (Ekman). Un sourire spontane monte en un demi-seconde environ et repart
 *   plus lentement qu'il n'est venu (Schmidt et al., 2006). La tristesse est
 *   lente dans les deux sens. Le reste se place entre ces bornes.
 *
 *   Le defaut n'est pas invente : c'est l'ancien lissage de `rig.js`,
 *   converti — montee a tau 0.075 s (90 % en 0.17 s), descente a tau 0.22 s
 *   (90 % en 0.51 s). Une expression sans ligne ici se comporte donc
 *   exactement comme avant ce fichier, en courbe en S au lieu d'un coin.
 *
 *   Ce sont des durees, pas des amplitudes : elles ne dependent pas du
 *   modele. Ce qui depend du modele — la course d'une forme, le nom qu'il lui
 *   donne — est dans son profil, jamais ici.
 */
export const ENVELOPES = {
  neutral:   { attack: 0.30, release: 0.55 },
  amused:    { attack: 0.45, release: 0.95 },
  happy:     { attack: 0.35, release: 0.85 },
  proud:     { attack: 0.40, release: 0.75 },
  surprised: { attack: 0.12, release: 0.35, hold: 0.80, residual: 0.35 },
  concerned: { attack: 0.30, release: 0.65 },
  serious:   { attack: 0.25, release: 0.50 },
  thinking:  { attack: 0.55, release: 0.65 },
  confused:  { attack: 0.35, release: 0.55 },
  sad:       { attack: 0.60, release: 1.00 },
  angry:     { attack: 0.20, release: 0.60 },
  tired:     { attack: 0.80, release: 0.80 },
};

/** Le comportement d'avant ce fichier, en courbe en S. Voir ENVELOPES. */
export const DEFAULT_ENVELOPE = { attack: 0.17, release: 0.51 };

/**
 * 90 % du chemin en `seconds` -> pulsation du ressort critique.
 *
 * Depuis le repos, l'erreur d'un ressort critique vaut (1 + wt) e^(-wt) ; elle
 * passe sous 10 % pour wt = 3.89. D'ou w = 3.89 / t90.
 */
export function omegaFor(seconds) {
  return 3.8897 / Math.max(0.02, seconds);
}

/**
 * Un pas EXACT d'un ressort critiquement amorti. Modifie `s` sur place.
 *
 * `s = { x, v }`. Exact sur `dt` quel qu'il soit : la cadence ne change pas la
 * trajectoire, seulement le nombre de points ou on l'echantillonne.
 */
export function springStep(s, goal, omega, dt) {
  const e = s.x - goal;
  const k = Math.exp(-omega * dt);
  const tmp = (s.v + omega * e) * dt;
  s.x = goal + (e + tmp) * k;
  s.v = (s.v - omega * tmp) * k;
  return s.x;
}

export class FacialPerformance {
  constructor() {
    /** Ce que le directeur a demande en dernier, par forme. */
    this.pending = Object.create(null);
    /** Vers quoi chaque ressort va MAINTENANT (la cible, une fois partie). */
    this.goal = Object.create(null);
    /** L'etat de chaque ressort : { x, v }. */
    this.spring = Object.create(null);
    /** Ce qui est ecrit, par forme — lu par `rig.js`. Reutilise, jamais realloue. */
    this.live = Object.create(null);
    /** Quand chaque forme a le droit de partir, en temps absolu. */
    this.startAt = Object.create(null);

    this.t = 0;
    this.expression = 'neutral';
    this.envelope = DEFAULT_ENVELOPE;
    /** Niveau applique a la cible : 1, puis `residual` apres la retombee. */
    this.level = 1;
    /** Retombee naturelle (ENVELOPES.hold), en temps absolu. */
    this.decayAt = Infinity;
    /** `hold_s` du fil : relache complete, en temps absolu. */
    this.releaseAt = Infinity;
    this.released = false;
    /** Ou en est ce visage, pour le labo et le journal. */
    this.phase = 'idle';
  }

  /**
   * Un nouveau visage. Rien ne bouge dans cette fonction.
   *
   * @param {object} blendshapes  les coefficients resolus, deja assainis
   * @param {string} expression   son nom, pour choisir signature et rythme.
   *                              Absent = pas de decalage et le rythme par
   *                              defaut — ce qu'attend un appelant qui pousse
   *                              une forme a la main.
   * @param {number} holdS        `Performance.hold_s`. > 0 = tenir ce visage
   *                              ce temps-la puis revenir au repos.
   */
  setTarget(blendshapes, expression, holdS) {
    const shapes = blendshapes || Object.create(null);
    const name = String(expression || '').toLowerCase();
    const signature = SIGNATURES[name] || {};

    this.expression = name || 'neutral';
    this.envelope = ENVELOPES[name] || DEFAULT_ENVELOPE;
    this.pending = Object.assign(Object.create(null), shapes);
    this.startAt = Object.create(null);
    this.level = 1;

    // Toute forme concernee, y compris celles qui RETOMBENT a zero : une forme
    // que la nouvelle expression ne nomme pas doit redescendre, et elle a droit
    // au meme decalage que si elle montait. Sans ca, un visage se defait d'un
    // bloc et se recompose en decale, ce qui se voit plus que l'inverse.
    for (const shape in this.spring) this._schedule(shape, signature);
    for (const shape in shapes) this._schedule(shape, signature);

    const onset = Math.min(longestDelay(signature), MAX_DELAY_S);
    this.decayAt = Number.isFinite(this.envelope.hold)
      ? this.t + onset + this.envelope.attack + this.envelope.hold
      : Infinity;

    const hold = Number(holdS);
    this.released = false;
    this.releaseAt = (Number.isFinite(hold) && hold > 0) ? this.t + onset + hold : Infinity;
    this.phase = 'attack';
  }

  _schedule(shape, signature) {
    const delay = signature[groupOf(shape)] || 0;
    this.startAt[shape] = this.t + Math.min(delay, MAX_DELAY_S);
    if (!this.spring[shape]) this.spring[shape] = { x: 0, v: 0 };
    if (this.goal[shape] === undefined) this.goal[shape] = 0;
  }

  /**
   * Une image. Rend l'etat de la couche expression, lisse, dans `this.live`.
   *
   * Une forme dont le decalage n'est pas ecoule garde sa cible PRECEDENTE —
   * pas zero, pas la nouvelle : elle continue ce qu'elle faisait, puis part.
   */
  update(dt) {
    this.t += dt;
    const t = this.t;

    // `hold_s` — le visage reflexe qui ne doit pas durer (WAKING, ERROR).
    if (!this.released && t >= this.releaseAt) {
      this.released = true;
      this.pending = Object.create(null);
      for (const shape in this.spring) this.startAt[shape] = t;
      this.phase = 'release';
    }
    // La retombee naturelle : une surprise ne se tient pas.
    if (!this.released && this.level === 1 && t >= this.decayAt) {
      this.level = this.envelope.residual !== undefined ? this.envelope.residual : 1;
      for (const shape in this.spring) this.startAt[shape] = t;
      this.phase = 'decay';
    }

    const up = omegaFor(this.envelope.attack);
    const down = omegaFor(this.envelope.release);
    let moving = false;

    for (const shape in this.spring) {
      if (t >= this.startAt[shape]) {
        const want = this.pending[shape];
        this.goal[shape] = want === undefined ? 0 : want * this.level;
      }
      const s = this.spring[shape];
      const goal = this.goal[shape];
      springStep(s, goal, goal >= s.x ? up : down, dt);
      // Un ressort peut deborder d'un cheveu quand sa cible change en route.
      // Une forme hors de [0, 1] n'a pas de sens pour un mesh.
      if (s.x < 0) { s.x = 0; if (s.v < 0) s.v = 0; }
      else if (s.x > 1) { s.x = 1; if (s.v > 0) s.v = 0; }

      if (goal === 0 && t >= this.startAt[shape] && s.x < 1e-4 && Math.abs(s.v) < 1e-3) {
        // Arrive a zero et au repos : la forme sort de la couche. Sans ce
        // menage, chaque forme jamais touchee resterait a iterer pour
        // toujours. Seulement une fois PARTIE : avant son decalage, une forme
        // qui va monter est a zero aussi — et la jeter ici l'empechait de
        // jamais arriver (le sourire d'`amused`, qui attend 0.13 s).
        delete this.spring[shape];
        delete this.goal[shape];
        delete this.startAt[shape];
        delete this.live[shape];
        continue;
      }
      this.live[shape] = s.x;
      if (Math.abs(goal - s.x) > 0.01) moving = true;
    }

    if (!moving && this.phase === 'attack') this.phase = 'hold';
    if (!moving && (this.phase === 'release' || this.phase === 'decay')) {
      this.phase = this.released ? 'idle' : 'hold';
    }
    return this.live;
  }

  /** La plus forte valeur de la couche : ce que le repos doit respecter. */
  strength() {
    let top = 0;
    for (const shape in this.live) {
      if (shape.startsWith('eyeLook')) continue;
      if (this.live[shape] > top) top = this.live[shape];
    }
    return top;
  }
}

/** Le plus long decalage d'une signature, pour dater la fin de la montee. */
function longestDelay(signature) {
  let longest = 0;
  for (const group in signature) longest = Math.max(longest, signature[group]);
  return longest;
}
