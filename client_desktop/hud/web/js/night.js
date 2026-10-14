/**
 * night.js — l'onglet NUIT du panneau ESPRIT : le rapport du veilleur de nuit.
 *
 * Tout vient de nightshift/hud.py par `rpc('nightshift.latest')` : le dernier
 * rapport jarvis.nightly/1, lu sans jeton sur la branche nightshift-data du
 * dépôt (gardé 30 min en cache). Lecture seule : les boutons relisent le
 * rapport ou ouvrent GitHub, rien d'autre. Paquet nightshift absent → l'onglet
 * le dit, le reste du panneau n'en sait rien.
 */
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const STATE_FR = {
  ok: ['VERT', 'up'], known_failure: ['ÉCHEC CONNU', ''], failing: ['ROUGE', 'down'],
  regression: ['RÉGRESSION', 'down'], error: ['EN ERREUR', 'down'], timeout: ['DÉLAI DÉPASSÉ', 'down'],
  skipped: ['IGNORÉE', ''],
};
const STATUS_FR = { success: 'TOUT VERT', partial_success: 'DU ROUGE', failed: 'RIEN MESURÉ' };
const STALE_MS = 48 * 3600 * 1000;

let last = null;

function when(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })} ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
}
function shortName(n) {
  return String(n || '').replace(' --selftest', '').replace(/[._]selftest$/, '').replace(/^(core|actions)\./, '');
}
function ref(x) {
  const c = String(x.check ?? '');
  return c === '*' ? `${shortName(x.suite)} (suite entière)` : /^\d/.test(c) ? `${shortName(x.suite)} n°${c}` : shortName(x.suite);
}
function row(kind, cls, a, b, why) {
  return `<div class="ml-row"><span class="ml-t ${cls}">${kind}</span><span>${esc(a)}</span><span>${esc(b)}</span><span class="ml-why">${esc(why)}</span></div>`;
}

function renderEmpty(r) {
  $('night-tiles').innerHTML = `<div class="ut"><div class="ut-k">VEILLEUR DE NUIT</div><div class="ut-v">AUCUN RAPPORT</div>
    <div class="ut-s">${esc(r.error || 'pas encore de nuit')}</div></div>`;
  for (const id of ['night-suites', 'night-changes', 'night-lint']) $(id).innerHTML = '<div class="mind-empty">—</div>';
  $('night-limits').innerHTML = '<li>Le premier rapport arrive après la première exécution du workflow « nightly » (onglet Actions du dépôt, bouton Run workflow).</li>';
}

function render(r) {
  const rep = r.report;
  const env = rep.environment || {};
  const dur = rep.duration_s != null ? `${Math.floor(rep.duration_s / 60)} min ${String(Math.round(rep.duration_s % 60)).padStart(2, '0')} s` : '—';
  $('night-tiles').innerHTML = [
    ['DERNIÈRE NUIT', when(rep.started_at), `commit ${rep.commit || '?'}${rep.ref ? ` · ${rep.ref}` : ''}`],
    ['RÉSULTAT', STATUS_FR[rep.status] || rep.status, rep.summary],
    ['SÉRIE VERTE', `${rep.green_streak ?? 0} nuit${(rep.green_streak ?? 0) > 1 ? 's' : ''}`, 'nuits vertes d’affilée, celle-ci comprise'],
    ['MACHINE', env.image || env.os || '—', `Python ${env.python || '?'} · ${dur}`],
  ].map(([k, v, sub]) => `<div class="ut"><div class="ut-k">${k}</div><div class="ut-v">${esc(v)}</div><div class="ut-s">${esc(sub)}</div></div>`).join('');

  const suites = rep.suites || [];
  $('night-suites').innerHTML = suites.length ? `<table class="ut-table"><thead><tr>
      <th>SUITE</th><th>SCORE</th><th>ÉTAT</th><th>DURÉE</th><th>DÉTAIL</th></tr></thead><tbody>${
    suites.map((s) => {
      let [label, cls] = STATE_FR[s.status] || [s.status, ''];
      if (s.status === 'failing') label += (s.failed || []).some((f) => f.class === 'unreferenced') ? ' (SANS RÉFÉRENCE)' : ' (DÉJÀ)';
      const bad = cls === 'down';
      const detail = s.reason || (s.failed || []).map((f) => `n°${f.id}${f.class === 'known' ? ' (connu)' : ''} ${f.label}`).join(' · ');
      return `<tr class="${bad ? 'weak' : ''}"><td class="mt-name">${esc(s.name)}</td>
        <td><b>${s.total != null ? `${s.passed}/${s.total}` : '—'}</b></td>
        <td>${bad ? `<span class="ut-flag">${label}</span>` : label}</td><td>${Number(s.duration_s || 0).toFixed(1)} s</td>
        <td>${esc(detail)}</td></tr>`;
    }).join('')}</tbody></table>` : '<div class="mind-empty">Aucune suite mesurée.</div>';

  const changes = [
    ...(rep.regressions || []).map((x) => row('RÉGRESSION', 'down', ref(x), x.since_commit ? `depuis ${x.since_commit}` : '',
      [x.label, x.suspects && x.suspects.length ? `suspects : ${x.suspects.slice(0, 3).join(' · ')}` : ''].filter(Boolean).join(' — '))),
    ...(rep.repaired || []).map((x) => row('RÉPARÉ', 'up', ref(x), x.was_known ? 'était connu' : '', x.label)),
    ...(rep.preexisting || []).map((x) => row('DÉJÀ ROUGE', '', ref(x), '', x.label)),
    ...(rep.unreferenced || []).map((x) => row('SANS RÉFÉRENCE', 'down', ref(x), 'la veille ne permet pas de dater', x.label)),
  ];
  $('night-changes').innerHTML = changes.join('') || '<div class="mind-empty">Rien n’a changé depuis la nuit précédente.</div>';

  const lint = rep.lint || {};
  const fresh = new Set((lint.new || []).map((f) => `${f.code}|${f.path}|${f.message}`));
  $('night-lint').innerHTML = !lint.available ? `<div class="mind-empty">${esc(lint.reason || 'ruff non lancé')}</div>`
    : (lint.findings || []).map((f) => row(esc(f.code), fresh.has(`${f.code}|${f.path}|${f.message}`) ? 'down' : '',
      `${f.path}:${f.line}`, fresh.has(`${f.code}|${f.path}|${f.message}`) ? 'NOUVELLE' : '', f.message)).join('')
      || '<div class="mind-empty">Aucune remarque.</div>';

  $('night-limits').innerHTML = (rep.limitations || []).map((l) => `<li>${esc(l)}</li>`).join('') || '<li>—</li>';
}

export async function loadNight(ctx, status, refresh = false) {
  const r = await ctx.rpc('nightshift.latest', { refresh });
  last = r;
  if (!r.report) {
    renderEmpty(r);
    if (r.error) status(r.error, 'err');
    return;
  }
  render(r);
  const age = Date.now() - new Date(r.report.finished_at).getTime();
  if (age > STALE_MS) status('Ce rapport a plus de 48 h : le veilleur ne tourne plus ? (onglet Actions du dépôt)', 'err');
  else if (r.error) status(`${r.error} — rapport en cache affiché`, 'err');
  else status(`Rapport lu ${r.from_cache ? 'en cache' : 'à l’instant'}${r.fetched_at ? ` (${when(new Date(r.fetched_at * 1000).toISOString())})` : ''}.`, 'ok');
}

export function wireNight(ctx, status) {
  $('night-actions').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-night]');
    if (!b) return;
    const act = b.dataset.night;
    if (act === 'refresh') { loadNight(ctx, status, true); ctx.sound.play('click'); return; }
    const url = act === 'run' ? last?.report?.run_url : act === 'data' ? last?.data_url : last?.actions_url;
    if (url && ctx.call) { ctx.call('openUrl', url); ctx.sound.play('click'); }
    else status('Lien indisponible : pas encore de rapport.', 'err');
  });
}
