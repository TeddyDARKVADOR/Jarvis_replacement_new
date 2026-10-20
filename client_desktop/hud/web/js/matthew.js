/**
 * matthew.js — l'onglet MATTHEW du panneau ESPRIT, et le badge « qui répond » de la barre du haut.
 *
 * Tout vient de matthew/hud.py par `rpc('matthew.*')` (mode local : le modèle, ses données et
 * sa vitrine sont sur ce PC). En ligne (Oracle), l'opération n'existe pas : le badge reste
 * caché et l'onglet le dit. Les seuls gestes possibles : corriger la dernière réponse, ne pas
 * apprendre le dernier échange, publier la vitrine, l'ouvrir.
 */
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (x) => (x == null ? '—' : `${Math.round(x * 100)} %`);
const BRAIN_CLASS = { Gemini: 'gemini', Matthew: 'matthew', 'hors-ligne': 'offline' };

let last = null;

function when(stamp) {
  if (!stamp) return '—';
  const m = String(stamp).match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  return m ? `${m[3]}/${m[2]} ${m[4]}:${m[5]}` : String(stamp);
}

function tile(k, v, sub, cls = '') {
  return `<div class="ut"><div class="ut-k">${k}</div><div class="ut-v ${cls}">${esc(v)}</div><div class="ut-s">${esc(sub)}</div></div>`;
}

function render(r) {
  const c = r.champion;
  const tok = r.tokens_today || {};
  $('mt-tiles').innerHTML = [
    tile('QUI RÉPOND', r.who.brain.toUpperCase(), r.who.detail),
    tile('CHAMPION', c && c.score != null ? c.score.toFixed(2) : 'AUCUN', c ? `depuis ${when(c.since)} · ${c.base}` : 'pas encore entraîné'),
    tile('BON OUTIL', pct(c && c.tool_name), `refus justes ${pct(c && c.irrelevance)}`),
    tile('COGNITION', pct(c && c.cognition), r.cognition_by_matthew ? 'Matthew fait les tâches de fond' : 'Matthew en retrait (banc insuffisant)'),
    tile('OUTILS APPRIS', `${r.tools_covered}`, `${r.synthetic} exemples vérifiés · ${r.corrections} corrections de vous`),
    tile('TOKENS DU JOUR', (tok.matthew || 0).toLocaleString('fr-FR'), `école ${(tok.teacher || 0).toLocaleString('fr-FR')}`),
  ].join('');
  const L = r.live;
  $('mt-live').innerHTML = L
    ? `<div class="ml-row"><span class="ml-t up">EN DIRECT</span><span>${esc(L.phase)} — époque ${L.epoch != null ? Math.min(Math.ceil(L.epoch), L.epochs) : '?'}/${L.epochs}</span><span>perte ${L.losses && L.losses.length ? L.losses[L.losses.length - 1] : '—'}</span><span class="ml-why">depuis ${esc(L.since)}</span></div>`
    : `<div class="mind-empty">Pas d'entraînement en cours. ${r.last_cycle ? `Dernier cycle : ${when(r.last_cycle.at)} — ${esc(r.last_cycle.outcome)}.` : ''}</div>`;
  $('mt-weak').innerHTML = (r.failures || []).map((f) => {
    const got = Array.isArray(f.got) ? f.got : null;
    const why = got && got.includes(f.target) ? 'bon outil, arguments différents'
      : got ? `a choisi ${got.join(', ') || 'aucun outil'}` : `a répondu : ${String(f.got || '').slice(0, 60)}`;
    return `<div class="ml-row"><span class="ml-t down">${esc(f.target)}</span><span>« ${esc(f.user)} »</span><span>${esc(why)}</span><span class="ml-why"></span></div>`;
  }).join('')
    || '<div class="mind-empty">Aucun échec au dernier examen.</div>';
}

export async function loadMatthew(ctx, status) {
  const r = await ctx.rpc('matthew.status', {});
  last = r;
  if (r.error) {
    $('mt-tiles').innerHTML = tile('MATTHEW', 'INDISPONIBLE', r.error);
    status(r.error, 'err');
    return;
  }
  render(r);
  status(r.flag_pending ? 'Une mise à jour de la vitrine attend sa publication.' : 'À jour.', 'ok');
}

export function wireMatthew(ctx, status) {
  const report = (r) => status(r.error || r.message || 'Fait.', r.error || r.ok === false ? 'err' : 'ok');
  $('mt-actions').addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-mt]');
    if (!b) return;
    const act = b.dataset.mt;
    ctx.sound.play('click');
    if (act === 'refresh') return loadMatthew(ctx, status);
    if (act === 'open') { if (ctx.call) ctx.call('openUrl', last?.vitrine_url || 'https://teddydarkvador.github.io/matthew-vitrine/'); return; }
    if (act === 'forget') report(await ctx.rpc('matthew.forget_last', {}));
    else if (act === 'publish') report(await ctx.rpc('matthew.publish', {}));
  });
  async function correct() {
    const text = $('mt-correct').value.trim();
    if (!text) { status('Écrivez la bonne réponse dans le champ.', 'err'); return; }
    ctx.sound.play('click');
    const r = await ctx.rpc('matthew.correct', { text });
    if (r.ok) $('mt-correct').value = '';
    report(r);
  }
  $('mt-correct-btn').addEventListener('click', correct);
  $('mt-correct').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); correct(); } });
}

/** Le badge de la barre du haut : qui répond, rafraîchi toutes les 10 s. Caché en ligne. */
export function pollBrain(rpc) {
  const chip = $('brain-chip');
  if (!chip) return;
  async function tick() {
    const r = await rpc('matthew.status', {});
    if (r.error || !r.who) { chip.hidden = true; return; }
    chip.hidden = false;
    chip.className = `chip brain ${BRAIN_CLASS[r.who.brain] || ''}`;
    $('brain-text').textContent = r.who.brain.toUpperCase();
    chip.title = `${r.who.detail}${r.cognition_by_matthew ? ' · tâches de fond : Matthew' : ''}`;
  }
  tick();
  setInterval(tick, 10000);
}
