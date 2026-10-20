/**
 * mind.js — le panneau ESPRIT : ce que l'architecture cognitive enregistre.
 *
 * Tout vient de cognition/hud.py par `rpc('cognition.*')` : l'état affectif
 * calculé et ses causes, les objectifs, les souvenirs avec leur provenance, la
 * fiabilité mesurée des outils et la calibration. La page n'invente rien et
 * n'arrondit qu'à l'affichage.
 *
 * Les boutons sont des DÉCISIONS de l'utilisateur : valider un souvenir en
 * fait une vérité (et une mesure de calibration), approuver un objectif
 * l'active. Aucun outil du modèle ne passe par ici (selftest cognition #46).
 */
import { loadNight, wireNight } from './night.js';
import { loadMatthew, wireMatthew } from './matthew.js';

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const day = (ts) => (ts ? String(ts).slice(0, 10) : '');
const hm = (ts) => (ts ? new Date(ts).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) : '');
const pct = (x) => (x == null ? '—' : `${Math.round(x * 100)} %`);
const num = (x, d = 2) => (x == null ? '—' : Number(x).toFixed(d));

const VAR_FR = {
  valence: 'VALENCE', arousal: 'ACTIVATION', assurance: 'ASSURANCE',
  frustration: 'FRUSTRATION', curiosity: 'CURIOSITÉ', warmth: 'CHALEUR',
};
const RULE_FR = {
  tool_ok: 'outil réussi', tool_recovered: 'outil enfin réussi', tool_error: 'outil en échec',
  tool_error_again: 'même outil, encore en échec', recall_empty: 'souvenir introuvable',
  user_thanks: 'vous l’avez remercié', user_correction: 'vous l’avez corrigé',
  session_start: 'début de conversation', goal_done: 'objectif atteint', goal_blocked: 'objectif bloqué',
};
const DIRECTIVE_FR = {
  change_strategy: ['CHANGER D’APPROCHE', 'Échecs répétés : il ne réessaie pas pareil, il change de méthode ou vous demande.'],
  caution: ['PRUDENCE', 'Réussite récente faible : il annonce son incertitude et confirme avant tout ce qui est difficile à défaire.'],
  brief: ['BRIÈVETÉ', 'Beaucoup d’événements à la fois : réponses courtes.'],
  follow_up: ['QUESTION DE SUIVI', 'Quelque chose est resté introuvable : il peut vous poser une question.'],
  warm: ['TON PLUS CHALEUREUX', 'Bonne relation : un ton un peu plus chaleureux, toujours professionnel.'],
};
const GOAL_FR = { proposed: 'PROPOSÉ', active: 'ACTIF', blocked: 'BLOQUÉ', done: 'ATTEINT', cancelled: 'ANNULÉ', expired: 'EXPIRÉ', withdrawn: 'RETIRÉ' };
const SOURCE_FR = {
  user_stated: 'dit par vous', tool_observed: 'observé par un outil', legacy_import: 'ancienne mémoire',
  model_summary: 'résumé de session', model_inferred: 'déduit par le modèle', reflection: 'réflexion',
};
const STATUS_FR = { proposed: 'NON VÉRIFIÉ', active: 'ACTIF', validated: 'VALIDÉ', contested: 'CONTESTÉ' };
const KIND_FR = { episodic: 'ÉPISODE', semantic: 'FAIT', reflective: 'RÉFLEXION', autobiographical: 'HISTOIRE', procedural: 'MÉTHODE', prospective: 'INTENTION' };
const RUN_FR = { running: 'EN COURS', done: 'TERMINÉE', stopped: 'ARRÊTÉE', refused: 'REFUSÉE', needs_user: 'QUESTION POUR VOUS', killed: 'ARRÊT D’URGENCE', error: 'ERREUR' };
const DECISION_FR = { executed: 'exécuté', refused: 'REFUSÉ', internal: '·' };
const VIEWS = [
  ['all', 'TOUS'], ['unverified', 'NON VÉRIFIÉS'], ['contested', 'CONTESTÉS'],
  ['reflective', 'RÉFLEXIONS'], ['episodes', 'ÉPISODES'], ['validated', 'VALIDÉS'],
];

export function createMind(ctx) {
  const box = $('mind');
  let open = false, tab = 'state', view = 'all', last = null, timer = null, busy = false;
  let qTimer = null;
  const available = () => !!(ctx.caps().cognition);

  function status(text, kind = '') {
    const el = $('mind-status');
    el.textContent = text || '';
    el.className = kind;
  }

  // ── ouverture ──
  function show() {
    box.hidden = false; open = true;
    const ok = available();
    $('mind-unavailable').hidden = ok;
    $('mind-body').hidden = !ok;
    $('mind-tabs').hidden = !ok;
    ctx.sound.play('open');
    if (!ok) return;
    selectTab(tab);
    refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 2000);
  }
  function hide() {
    box.hidden = true; open = false;
    clearInterval(timer); timer = null;
    ctx.sound.play('close');
  }
  function selectTab(t) {
    tab = t;
    for (const b of $('mind-tabs').querySelectorAll('button')) b.classList.toggle('sel', b.dataset.tab === t);
    for (const sec of $('mind-body').querySelectorAll('section')) sec.hidden = sec.dataset.pane !== t;
    if (t === 'memories') loadMemories();
    if (t === 'usage') loadUsage();
    if (t === 'night') loadNight(ctx, status);
    if (t === 'matthew') loadMatthew(ctx, status);
  }

  // ── usage : ce que JARVIS a coûté et réussi (cognition/usage.py) ──
  let usageDays = 7;
  const kfmt = (n) => (n == null ? '—'
    : n >= 1e6 ? `${(n / 1e6).toFixed(1).replace('.', ',')} M`
    : n >= 10000 ? `${(n / 1000).toFixed(1).replace('.', ',')} k` : n.toLocaleString('fr-FR'));
  async function loadUsage() {
    const r = await ctx.rpc('cognition.usage', { days: usageDays });
    if (r.error) { status(r.error, 'err'); return; }
    const t = r.totals;
    const change = t.change == null ? 'pas de semaine précédente à comparer'
      : `${t.change > 0 ? '+' : ''}${Math.round(t.change * 100)} % vs la période d'avant`;
    const rem = r.reminders;
    $('usage-tiles').innerHTML = [
      ['CONTEXTE MOYEN / APPEL', `${kfmt(t.avg_input)} tokens`, change],
      ['APPELS LIVE', kfmt(t.live_calls), `${t.sessions} session(s) depuis le ${esc(r.since.slice(8, 10))}/${esc(r.since.slice(5, 7))}`],
      ['COÛT AU TARIF PAYANT', `≥ ${t.usd.toFixed(2)} $`, 'minimum : la part non ventilée est comptée au tarif texte'],
      ['RAPPELS', `${rem.delivered} livré(s)`, `${rem.missed} manqué(s) · ${rem.upcoming} à venir`],
    ].map(([k, v, sub]) => `<div class="ut"><div class="ut-k">${k}</div><div class="ut-v">${esc(v)}</div><div class="ut-s">${esc(sub)}</div></div>`).join('');

    const max = Math.max(1, ...r.days.map((d) => d.avg_input || 0));
    $('usage-chart').innerHTML = r.days.map((d) => {
      const h = d.avg_input ? Math.max(2, (d.avg_input / max) * 100) : 0;
      const label = d.avg_input
        ? `${d.date} : ${kfmt(d.avg_input)} tokens en moyenne (max ${kfmt(d.max_input)}), ${d.calls} appel(s), ≥ ${d.usd.toFixed(3)} $`
        : `${d.date} : aucun appel`;
      return `<div class="uc-col" tabindex="0" aria-label="${esc(label)}" data-tip="${esc(label)}">
        <div class="uc-bar${d.avg_input ? '' : ' empty'}" style="height:${h}%"></div>
        <div class="uc-day">${esc(d.date.slice(8, 10))}/${esc(d.date.slice(5, 7))}</div></div>`;
    }).join('') + '<div class="uc-tip" id="usage-tip" hidden></div>';
    const tip = $('usage-tip');
    for (const col of $('usage-chart').querySelectorAll('.uc-col')) {
      const showTip = () => { tip.textContent = col.dataset.tip; tip.hidden = false;
        tip.style.left = `${col.offsetLeft + col.offsetWidth / 2}px`; };
      col.addEventListener('mouseenter', showTip); col.addEventListener('focus', showTip);
      col.addEventListener('mouseleave', () => { tip.hidden = true; });
      col.addEventListener('blur', () => { tip.hidden = true; });
    }

    $('usage-tools').innerHTML = r.tools.length ? `<table class="ut-table"><thead><tr>
        <th>OUTIL</th><th>APPELS</th><th>RÉUSSIS</th><th>TAUX</th><th>DERNIER ÉCHEC</th></tr></thead><tbody>${
      r.tools.map((x) => {
        const bad = x.rate < 0.8 && x.calls >= 3;
        return `<tr class="${bad ? 'weak' : ''}"><td class="mt-name">${esc(x.tool)}</td><td>${x.calls}</td><td>${x.ok}</td>
          <td><b>${Math.round(x.rate * 100)} %</b>${bad ? ' <span class="ut-flag">⚠ À SURVEILLER</span>' : ''}</td>
          <td>${x.last_fail ? `${esc(day(x.last_fail))} ${esc(hm(x.last_fail))}` : '—'}</td></tr>`;
      }).join('')}</tbody></table>`
      : '<div class="mind-empty">Aucun appel d’outil sur la période.</div>';

    const total = r.sources.reduce((a, [, n]) => a + n, 0) || 1;
    $('usage-sources').innerHTML = r.sources.length
      ? r.sources.map(([name, n]) => `<div class="ml-row"><span class="mt-name">${esc(name)}</span>
          <span>${kfmt(n)} tokens</span><span class="ml-why">${Math.round((n / total) * 100)} %</span></div>`).join('')
      : '<div class="mind-empty">Rien d’enregistré sur la période.</div>';
  }

  // ── vue d'ensemble ──
  async function refresh() {
    if (busy || !open) return;
    busy = true;
    const r = await ctx.rpc('cognition.overview');
    busy = false;
    if (r.error) { status(r.error, 'err'); return; }
    last = r;
    renderState(r); renderGoals(r); renderAutonomy(r); renderSelf(r);
    const pending = r.goals.filter((g) => g.status === 'proposed' || g.status === 'blocked').length;
    badge('mind-goal-badge', pending);
    badge('mind-mem-badge', (r.memory.by_status.proposed || 0) + (r.memory.by_status.contested || 0));
    $('mind-meta').textContent = `constitution v${r.constitution} · session ${r.session}`;
  }
  function badge(id, n) { const b = $(id); b.hidden = !n; b.textContent = n || ''; }

  function renderState(r) {
    $('mind-affect').innerHTML = r.affect.map((a) => {
      const span = a.hi - a.lo;
      const pos = (v) => ((v - a.lo) / span) * 100;
      const zero = a.lo < 0 ? pos(0) : 0;
      const left = Math.min(zero, pos(a.value)), width = Math.abs(pos(a.value) - zero);
      return `<div class="mind-var" title="${esc(a.meaning)}">
        <div class="mv-head"><span>${VAR_FR[a.name] || a.name}</span><b>${num(a.value)}</b></div>
        <div class="mv-bar"><i style="left:${left}%;width:${Math.max(width, .8)}%"></i>
          <u style="left:${pos(a.baseline)}%" title="ligne de base ${a.baseline}"></u></div>
        <div class="mv-meaning">${esc(a.meaning)}</div></div>`;
    }).join('');
    $('mind-directives').innerHTML = r.directives.length
      ? r.directives.map((d) => `<div class="mind-dir"><b>${DIRECTIVE_FR[d.key]?.[0] || d.key}</b> ${esc(DIRECTIVE_FR[d.key]?.[1] || d.text)}</div>`).join('')
      : '<div class="mind-empty">Aucune : son état est dans les marges habituelles.</div>';
    $('mind-causes').innerHTML = r.causes.length
      ? r.causes.map((c) => {
        const up = c.after > c.before;
        return `<div class="ml-row"><span class="ml-t">${esc(day(c.ts))} ${esc(hm(c.ts))}</span>
          <span>${VAR_FR[c.var] || c.var}</span>
          <span class="${up ? 'up' : 'down'}">${num(c.before)} → ${num(c.after)}</span>
          <span class="ml-why">${esc(RULE_FR[c.rule] || c.rule)}</span></div>`;
      }).join('')
      : '<div class="mind-empty">Rien encore : l’état bouge quand un outil réussit ou échoue, quand vous le remerciez ou le corrigez.</div>';
  }

  function renderGoals(r) {
    $('mind-goals').innerHTML = r.goals.length ? r.goals.map((g) => {
      const actions = [];
      if (g.status === 'proposed') actions.push(['approve', 'APPROUVER', 'primary']);
      if (g.status === 'blocked') actions.push(['approve', 'RELANCER', 'primary']);
      if (g.status === 'active') actions.push(['complete', 'ATTEINT', '']);
      actions.push(['cancel', 'ANNULER', 'danger']);
      const auto = (r.autonomy || {});
      const blocker = (auto.goals || {})[g.id];
      const autoBtn = g.status === 'active'
        ? `<button class="mini-btn ${g.autonomy ? 'on' : ''}" data-goal="${esc(g.id)}" data-act="autonomy" data-on="${g.autonomy ? 0 : 1}" title="${g.autonomy ? 'Retirer l’autonomie' : 'Autoriser JARVIS à avancer seul (recherche web uniquement)'}">AUTONOMIE ${g.autonomy ? 'ON' : 'OFF'}</button>`
          + (g.autonomy ? `<button class="mini-btn" data-goal="${esc(g.id)}" data-act="run" title="${esc(blocker || 'Lancer une séance maintenant')}" ${blocker && !blocker.startsWith('dernière séance') ? 'disabled' : ''}>LANCER</button>` : '')
        : '';
      const budget = g.budget_actions ? Math.min(100, (g.spent_actions / g.budget_actions) * 100) : 0;
      return `<div class="mind-card st-${g.status}">
        <div class="mc-head"><span class="mc-status">${GOAL_FR[g.status] || g.status}</span>
          <b>${esc(g.title)}</b><span class="mc-p">P${g.priority}</span></div>
        <div class="mc-line"><span>Pourquoi</span>${esc(g.justification)}</div>
        <div class="mc-line"><span>Réussi quand</span>${esc(g.success_criteria)}</div>
        ${g.closed_reason ? `<div class="mc-line warn"><span>Bloqué</span>${esc(g.closed_reason)}</div>` : ''}
        ${g.status === 'active' && g.autonomy && blocker ? `<div class="mc-line wait"><span>Autonomie</span>en attente : ${esc(blocker)}</div>` : ''}
        <div class="mc-foot">
          <div class="mc-budget" title="${g.spent_actions}/${g.budget_actions} actions"><i style="width:${budget}%"></i></div>
          <span>${g.spent_actions}/${g.budget_actions} actions${g.deadline ? ` · échéance ${esc(g.deadline)}` : ''} · ${g.origin === 'user' ? 'demandé par vous' : 'proposé par JARVIS'}</span>
          <span class="mc-actions">${autoBtn}${actions.map(([a, label, cls]) => `<button class="mini-btn ${cls}" data-goal="${esc(g.id)}" data-act="${a}">${label}</button>`).join('')}</span>
        </div></div>`;
    }).join('') : '<div class="mind-empty">Aucun objectif ouvert. Dites par exemple : « mon objectif, c’est de… ».</div>';
    $('mind-closed').innerHTML = r.closed_goals.map((g) => `<div class="ml-row"><span class="ml-t">${esc(day(g.updated_at))}</span>
      <span>${GOAL_FR[g.status] || g.status}</span><span>${esc(g.title)}</span><span class="ml-why">${esc(g.closed_reason || '')}</span></div>`).join('')
      || '<div class="mind-empty">—</div>';
  }

  function renderAutonomy(r) {
    const a = r.autonomy || {};
    const st = $('mind-auto-state');
    st.textContent = a.enabled ? `ACTIVÉE · ${a.runs_today}/${a.limits?.runs_per_day ?? '?'} séances aujourd’hui` : 'COUPÉE';
    st.className = a.enabled ? 'on' : 'off';
    $('mind-auto-limits').textContent = a.limits
      ? `outils : ${a.limits.tools.join(', ')} · ${a.limits.steps_per_run} étapes par séance · une séance par objectif toutes les ${a.limits.interval_min} min au plus`
      : '';
    const t = $('mind-auto-toggle');
    t.textContent = a.enabled ? 'COUPER' : 'ACTIVER';
    t.classList.toggle('primary', !a.enabled);
    $('mind-auto-kill').disabled = !a.enabled;
    $('mind-runs').innerHTML = (a.runs || []).length ? a.runs.map((run) => `<div class="mind-run st-${run.status}">
        <div class="mr-head"><span class="ml-t">${esc(day(run.started_at))} ${esc(hm(run.started_at))}</span>
          <b>${esc(run.title || '?')}</b><span class="mr-status">${RUN_FR[run.status] || run.status}</span>
          <span class="ml-why">${run.steps} étape(s) · ${esc(run.stop_reason || '')}</span></div>
        ${run.question ? `<div class="mr-question">❓ ${esc(run.question)}</div>` : ''}
        ${run.steps_detail.map((s) => `<div class="mr-step d-${s.decision}"><span>${DECISION_FR[s.decision] || s.decision}</span>
          <code>${esc(s.action || '?')} ${esc(s.args || '')}</code><span class="ml-why">${esc(s.decision === 'refused' ? s.reason : (s.result || s.reason || ''))}</span></div>`).join('')}
      </div>`).join('')
      : '<div class="mind-empty">Aucune séance. Activez l’autonomie, puis autorisez-la sur un objectif actif (bouton AUTONOMIE).</div>';
  }

  function renderSelf(r) {
    $('mind-tools').innerHTML = r.tools.length ? r.tools.map((t) => `<div class="mt-row${t.weak ? ' weak' : ''}">
        <span class="mt-name">${esc(t.tool)}</span>
        <div class="mv-bar"><i style="left:0;width:${t.expected * 100}%"></i></div>
        <b>${pct(t.expected)}</b><span class="mt-n">${t.n} essai${t.n > 1 ? 's' : ''}${t.weak ? ' · PEU FIABLE' : ''}</span></div>`).join('')
      : '<div class="mind-empty">Aucun essai enregistré pour l’instant.</div>';
    const tc = r.calibration.tools, mc = r.calibration.memories;
    const srcRows = Object.entries(mc.by_source || {}).map(([s, d]) =>
      `<tr><td>${esc(SOURCE_FR[s] || s)}</td><td>${d.n}</td><td>${pct(d.mean_confidence)}</td><td>${pct(d.validated_rate)}</td></tr>`).join('');
    $('mind-calib').innerHTML = `
      <div class="mc-cell"><span>OUTILS</span><b>Brier ${num(tc.brier, 3)}</b><b>ECE ${num(tc.ece, 3)}</b><small>${tc.n} appel${tc.n > 1 ? 's' : ''}</small></div>
      <div class="mc-cell"><span>SOUVENIRS × VOS VERDICTS</span><b>Brier ${num(mc.brier, 3)}</b><b>ECE ${num(mc.ece, 3)}</b><small>${mc.n} verdict${mc.n > 1 ? 's' : ''}</small></div>
      ${tc.bins.length ? `<table class="mind-table"><tr><th>confiance</th><th>appels</th><th>attendu</th><th>observé</th></tr>
        ${tc.bins.map((b) => `<tr><td>${b.range}</td><td>${b.n}</td><td>${pct(b.confidence)}</td><td>${pct(b.observed)}</td></tr>`).join('')}</table>` : ''}
      ${srcRows ? `<table class="mind-table"><tr><th>provenance</th><th>verdicts</th><th>confiance</th><th>validés</th></tr>${srcRows}</table>`
        : '<div class="mind-empty">Validez ou rejetez des souvenirs (onglet SOUVENIRS) : vos verdicts mesurent si sa confiance est juste.</div>'}`;
    $('mind-limits').innerHTML = (r.limits_fr || r.limits).map((l) => `<li>${esc(l)}</li>`).join('');
    $('mind-providers').innerHTML = r.providers.length ? r.providers.map((p) => `<div class="ml-row">
        <span class="ml-t">${esc(p.name)}</span><span>${esc(p.model)}</span>
        <span class="${p.available ? 'up' : 'down'}">${p.available ? 'disponible' : `en pause ${p.paused_s} s`}</span>
        <span class="ml-why">${p.calls} appels, ${p.failures} échecs${p.latency_s != null ? `, ${p.latency_s} s` : ''}</span></div>`).join('')
      : '<div class="mind-empty">Aucun générateur configuré.</div>';
  }

  // ── souvenirs ──
  async function loadMemories() {
    const q = $('mind-q').value.trim();
    const fb = $('mind-forget');
    fb.hidden = q.length < 2;
    if (!fb.classList.contains('arm')) fb.textContent = 'OUBLIER CE SUJET';
    const r = await ctx.rpc('cognition.memories', { view, query: q });
    if (r.error) { status(r.error, 'err'); return; }
    $('mind-views').innerHTML = VIEWS.map(([v, label]) =>
      `<button class="mini-btn${v === view ? ' sel' : ''}" data-view="${v}">${label} <small>${r.totals[v] ?? 0}</small></button>`).join('');
    $('mind-mem').innerHTML = r.items.length ? r.items.map((m) => `<div class="mem-row mind-mem st-${m.status}">
        <span class="cat">${KIND_FR[m.kind] || m.kind}<br><em>${STATUS_FR[m.status] || m.status}</em></span>
        <span class="kv">${esc(m.content)}<small>${esc(SOURCE_FR[m.source] || m.source)} · confiance ${pct(m.confidence)}${m.cites.length ? ` · cite ${m.cites.length} souvenir(s)` : ''}</small></span>
        <span class="date">${esc(day(m.updated_at))}</span>
        <span class="mm-actions">
          ${m.status !== 'validated' ? `<button title="Valider : c'est vrai" data-mem="${esc(m.id)}" data-v="validate">✓</button>` : ''}
          <button title="Rejeter : c'est faux" data-mem="${esc(m.id)}" data-v="reject">✗</button>
          <button title="Oublier : effacer pour de bon" data-mem="${esc(m.id)}" data-v="forget">🗑</button>
        </span></div>`).join('')
      : '<div class="mind-empty">Rien dans cette vue.</div>';
  }

  // ── actions de l'utilisateur ──
  async function review(btn) {
    const verdict = btn.dataset.v;
    if (verdict === 'forget' && !btn.classList.contains('arm')) {
      btn.classList.add('arm'); btn.textContent = 'SÛR ?';
      setTimeout(() => { btn.classList.remove('arm'); btn.textContent = '🗑'; }, 3000);
      return;
    }
    const r = await ctx.rpc('cognition.memory_review', { id: btn.dataset.mem, verdict });
    if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
    status({ validate: 'Souvenir validé.', reject: 'Souvenir rejeté : il ne sera plus rappelé.', forget: 'Souvenir effacé.' }[verdict], 'ok');
    ctx.sound.play('click');
    loadMemories(); refresh();
  }
  async function forgetSubject() {
    const fb = $('mind-forget');
    const subject = $('mind-q').value.trim();
    if (!fb.classList.contains('arm')) {
      const p = await ctx.rpc('cognition.forget_about', { subject, preview: true });
      if (p.error) { status(p.error, 'err'); return; }
      if (!p.count) { status(`Rien ne mentionne « ${subject} ».`, ''); return; }
      fb.classList.add('arm');
      fb.textContent = `EFFACER ${p.count} SOUVENIR${p.count > 1 ? 'S' : ''} ?`;
      status(`Effacement définitif de ${p.count} souvenir(s), résumés compris. Cliquez à nouveau pour confirmer.`, 'err');
      setTimeout(() => { fb.classList.remove('arm'); fb.textContent = 'OUBLIER CE SUJET'; }, 5000);
      return;
    }
    fb.classList.remove('arm');
    const r = await ctx.rpc('cognition.forget_about', { subject });
    if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
    status(r.detail + ' (la mémoire V1 se gère dans Paramètres → Mémoire).', 'ok');
    ctx.sound.play('click');
    loadMemories(); refresh();
  }

  async function goalAction(btn) {
    const act = btn.dataset.act;
    if (act === 'autonomy' || act === 'run') {
      const r = act === 'run'
        ? await ctx.rpc('cognition.autonomy_run', { id: btn.dataset.goal })
        : await ctx.rpc('cognition.goal_autonomy', { id: btn.dataset.goal, on: btn.dataset.on === '1' });
      if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
      status(act === 'run' ? 'Séance lancée : le journal se remplit en direct.' : (btn.dataset.on === '1' ? 'Autonomie autorisée sur cet objectif.' : 'Autonomie retirée.'), 'ok');
      ctx.sound.play('click'); refresh(); return;
    }
    if (act === 'cancel' && !btn.classList.contains('arm')) {
      btn.classList.add('arm'); btn.textContent = 'SÛR ?';
      setTimeout(() => { btn.classList.remove('arm'); btn.textContent = 'ANNULER'; }, 3000);
      return;
    }
    const r = await ctx.rpc('cognition.goal_action', { id: btn.dataset.goal, action: act });
    if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
    status(r.detail || 'Fait.', 'ok');
    ctx.sound.play('click');
    refresh();
  }

  function wire() {
    $('btn-mind').addEventListener('click', () => (open ? hide() : show()));
    $('mind-close').addEventListener('click', hide);
    box.addEventListener('mousedown', (e) => { if (e.target === box) hide(); });
    $('mind-tabs').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-tab]');
      if (b) { selectTab(b.dataset.tab); ctx.sound.play('click'); }
    });
    $('usage-range').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-days]');
      if (!b) return;
      usageDays = Number(b.dataset.days);
      for (const x of $('usage-range').querySelectorAll('button')) x.classList.toggle('sel', x === b);
      loadUsage();
    });
    wireNight(ctx, status);
    wireMatthew(ctx, status);
    $('mind-views').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-view]');
      if (b) { view = b.dataset.view; loadMemories(); }
    });
    $('mind-q').addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(loadMemories, 250); });
    $('mind-forget').addEventListener('click', forgetSubject);
    $('mind-mem').addEventListener('click', (e) => { const b = e.target.closest('button[data-mem]'); if (b) review(b); });
    $('mind-goals').addEventListener('click', (e) => { const b = e.target.closest('button[data-goal]'); if (b) goalAction(b); });
    $('mind-auto-toggle').addEventListener('click', async () => {
      const on = !(last && last.autonomy && last.autonomy.enabled);
      const r = await ctx.rpc('cognition.autonomy', { enabled: on });
      if (r.error) { status(r.error, 'err'); return; }
      status(on ? 'Autonomie activée : elle n’agit que sur les objectifs que vous autorisez.' : 'Autonomie coupée.', 'ok');
      ctx.sound.play('click'); refresh();
    });
    $('mind-auto-kill').addEventListener('click', async () => {
      const r = await ctx.rpc('cognition.autonomy', { enabled: false });
      if (r.error) { status(r.error, 'err'); return; }
      status('ARRÊT D’URGENCE : autonomie coupée, la séance en cours s’arrête avant son étape suivante.', 'ok');
      ctx.sound.play('close'); refresh();
    });
  }

  wire();
  return {
    open: show,
    close: hide,
    get isOpen() { return open; },
  };
}
