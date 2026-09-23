/**
 * face_runner.mjs — le visage, juge sur des situations qu'on lui donne.
 *
 *     node eval_lab/js/face_runner.mjs <cases.json>
 *
 * Aucun invariant n'est ecrit ici : runCase et violations viennent de
 * avatar/checks/scenario_matrix.mjs, les memes que la matrice execute. Une
 * seconde copie des invariants serait une seconde verite, et les deux
 * finiraient par ne plus juger la meme chose.
 *
 * Entree : un tableau JSON de { i, intent, state, speech, gaze, urgent, interrupted }.
 * Sortie : une ligne JSON par situation, dans l'ordre, sur stdout.
 *   { i, ok, violations: [{kind, detail}], expression, gesture, gesture_played }
 *   { i, ok: false, error }   quand le moteur a leve — c'est une panne de
 *                             JARVIS, pas du labo, et elle est rapportee comme telle.
 */
import { readFileSync } from 'node:fs';
import { runCase, violations } from '../../avatar/checks/scenario_matrix.mjs';

const cases = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const lines = [];
for (const c of cases) {
  try {
    const o = runCase(c);
    const v = violations(c, o).map(([kind, detail]) => ({ kind, detail }));
    lines.push(JSON.stringify({
      i: c.i, ok: true, violations: v,
      expression: o.perf.expression, gesture: o.decision.gesture,
      gesture_played: o.decision.gesture_played, accent_after: o.accentAfter || null,
    }));
  } catch (err) {
    lines.push(JSON.stringify({ i: c.i, ok: false, error: `${err && err.name}: ${err && err.message}` }));
  }
}
process.stdout.write(lines.join('\n') + '\n');
