/**
 * explain.js — pourquoi le visage fait ce qu'il fait, en huit lignes courtes.
 *
 *     WHY        investigate
 *     SITUATION  information demandee a l'ecran
 *     AFFECT     thinking 0.44
 *     GAZE       screen (intent)
 *     HEAD       look_away ×0.93 — turn replie (motion face)
 *     ACCENT     -
 *     VOICE      12 appuis, 1 echappee du regard, 0 hochement d'ecoute
 *     OUTPUT     visage 9 formes · regard · tete · corps tenu
 *
 * LA REGLE
 *   Chaque ligne est lue dans ce que le moteur a FAIT — `engine.decision`,
 *   `engine.output()`, les compteurs de la conversation — jamais dans la
 *   demande. Un geste saute par habituation s'affiche saute ; un accent efface
 *   par la decision suivante s'affiche efface ; un corps gele s'affiche tenu.
 *   Le labo l'affiche, `avatar/checks/situations.mjs` verifie qu'il dit vrai.
 */

const PLAYED = {
  procedural: '',
  clip: ' (clip)',
  frozen: ' — gele (motion face)',
  absent: ' — aucun os pour le jouer',
  none: ' — injouable',
  habituated: ' — saute (habituation : deja fait souvent)',
  'deja en cours': ' (deja en cours)',
};

/**
 * @param {object} perf    la Performance jouee (celle du directeur)
 * @param {object} engine  le moteur qui l'a jouee
 * @returns {Array<[string, string]>}
 */
export function explainDecision(perf, engine) {
  const d = engine.decision || {};
  const out = engine.output();
  const c = engine.conversation.stats;
  const lines = [];

  let why = perf.intent || '';
  if (!why) why = String(perf.reason || '').startsWith('affect:') ? 'un etat interieur (affect)' : `reflexe ${perf.state || '-'}`;
  lines.push(['WHY', why]);
  lines.push(['SITUATION', perf.reason && !perf.reason.startsWith('reflex:') ? perf.reason : '-']);
  lines.push(['AFFECT', `${perf.expression} ${Number(perf.intensity).toFixed(2)}`
    + (engine.rig.performance.phase === 'decay' ? ' — retombe' : '')]);

  lines.push(['GAZE', `${perf.gaze} (${perf.gaze_source})`
    + (engine.rig.gazeCtl.averting ? ' — s\'echappe (debut de phrase)' : '')]);

  const requested = perf.requested_gesture || perf.gesture;
  const scale = Number.isFinite(d.gesture_scale) && d.gesture_scale !== 1 && d.gesture_played === 'procedural'
    ? ` ×${d.gesture_scale.toFixed(2)}` : '';
  let head = `${d.gesture || perf.gesture}${scale}${PLAYED[d.gesture_played] !== undefined ? PLAYED[d.gesture_played] : ''}`;
  if (requested !== perf.gesture) head += ` — ${requested} replie`;
  lines.push(['HEAD', head]);

  let accent = '-';
  if (d.accent) {
    if (engine.accents.name === d.accent) accent = `${d.accent} — en cours`;
    else if (d.accent_played) accent = `${d.accent} — joue`;
    else accent = `${d.accent} — non joue`;
  }
  lines.push(['ACCENT', accent]);

  lines.push(['VOICE', `${c.emphasised} appuis, ${engine.rig.gazeCtl.averts || 0} echappee(s) du regard, `
    + `${c.backchannels} hochement(s) d'ecoute`]);

  const faceShapes = Object.keys(out.shapes).filter((n) => !n.startsWith('eyeLook') && !n.startsWith('eyeBlink')).length;
  const body = out.body;
  const bodyStill = Math.abs(body.spineRx) + Math.abs(body.spineRy) + Math.abs(body.rootRy) < 1e-6;
  const channels = [`visage ${faceShapes} formes`, 'regard', 'tete'];
  if (out.speaking > 0.05) channels.push('bouche (parole)');
  channels.push(bodyStill ? 'corps tenu' : 'corps');
  lines.push(['OUTPUT', channels.join(' · ')]);
  return lines;
}
