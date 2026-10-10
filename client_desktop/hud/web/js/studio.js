/**
 * studio.js — the Jarvis Code Studio panel.
 *
 * Everything here is read from `studio.status` (core/code_workspace/service.py):
 * the journal the tools write, the task list, the terminal lines of commands
 * that really ran, the checkpoints on disk. The page invents nothing — it
 * polls once a second while open, and asks `studio.ping` every few seconds
 * while closed so the header button can glow when JARVIS is at work (and the
 * panel opens by itself when a new job starts).
 */
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hm = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString('fr-FR') : '');

const STEPS = [
  ['planification', 'PLANIFICATION'],
  ['creation', 'CRÉATION'],
  ['verification', 'VÉRIFICATION'],
  ['presentation', 'PRÉSENTATION'],
];
const TASK_LABEL = { todo: 'À FAIRE', doing: 'EN COURS', done: 'TERMINÉ', blocked: 'BLOQUÉ' };
const KIND_ICON = {
  phase: '◆', plan: '✎', task: '☐', file: '▤', command: '›', test: '✓', error: '⚠', checkpoint: '⛁',
  restore: '↺', summary: '★', info: '·', blocked: '⛔', confirm: '?', preview: '▶',
};

export function createStudio(ctx) {
  const box = $('studio');
  let open = false;
  let project = null;          // the project being viewed ('' = follow the job)
  let last = null;             // last status
  let lastSeq = -1, lastJobKey = '';
  let timer = null, pingTimer = null, lastStarted = null;
  let optionsDirty = false;

  const available = () => !!(ctx.caps().studio);

  // ── open / close ──
  function show() {
    if (!available()) {
      box.hidden = false; open = true;
      $('st-unavailable').hidden = false;
      $('st-main').hidden = true;
      return;
    }
    $('st-unavailable').hidden = true;
    $('st-main').hidden = false;
    box.hidden = false; open = true;
    ctx.sound.play('open');
    refresh(true);
    clearInterval(timer);
    timer = setInterval(() => refresh(false), 1000);
  }
  function hide() {
    box.hidden = true; open = false;
    clearInterval(timer); timer = null;
    $('st-viewer').hidden = true;
    ctx.sound.play('close');
  }

  // ── polling ──
  let busy = false;
  async function refresh(force) {
    if (busy) return;
    busy = true;
    try {
      const r = await ctx.rpc('studio.status', project ? { project } : {});
      if (!open) return;
      if (r.error && !r.root) { status(r.error, 'err'); return; }
      render(r, force);
    } finally { busy = false; }
  }

  let primed = false, lastFinished = null;
  async function ping() {
    if (!available()) return;
    const r = await ctx.rpc('studio.ping');
    if (r.error) return;
    $('btn-studio').classList.toggle('working', !!r.running);
    const job = r.job;
    if (!primed) {            // what was already there before the HUD opened is old news
      primed = true;
      lastStarted = job ? job.started : null;
      lastFinished = job ? job.finished : null;
      return;
    }
    if (job && job.running && job.started !== lastStarted) {
      lastStarted = job.started;
      ctx.toast('CODE STUDIO', `JARVIS travaille sur « ${job.project} »`, null, 4000);
      // "montre-moi ce que tu fais": a job that starts opens the panel.
      if (!open && !ctx.isMini()) { project = null; show(); }
    }
    if (job && !job.running && job.finished && job.finished !== lastFinished) {
      lastFinished = job.finished;
      ctx.toast('CODE STUDIO', `« ${job.project} » : ${job.phase_label}`, job.phase === 'termine' ? null : '#FBBF6B', 6000);
    }
  }

  // ── rendering ──
  function render(s, force) {
    last = s;
    $('st-root').textContent = s.root || '—';
    $('st-coder').textContent = s.coder || '—';
    if (!optionsDirty) {
      const o = s.options || {};
      $('st-root-input').value = s.root || '';
      $('st-attempts').value = o.max_fix_attempts ?? 4;
      $('st-timeout').value = o.command_timeout ?? 120;
      $('st-confirm-plan').checked = !!o.confirm_plan;
    }
    $('st-safety').textContent = s.safety || '';
    $('st-admin').hidden = !s.admin;
    $('st-root-error').hidden = !s.root_error;
    $('st-root-error').textContent = s.root_error || '';

    // project picker
    const sel = $('st-project');
    const names = s.projects || [];
    const want = s.project || '';
    if (sel.dataset.names !== names.join('|') || sel.value !== want) {
      sel.innerHTML = names.length ? names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('')
        : '<option value="">(aucun projet)</option>';
      sel.dataset.names = names.join('|');
      sel.value = want;
    }
    $('st-empty').hidden = !!names.length;

    // job / phase
    const job = s.job && (!s.project || s.job.project === s.project) ? s.job : null;
    renderSteps(job);
    $('st-stop').disabled = !(job && job.running);
    const jobKey = job ? `${job.phase}|${job.step}|${job.attempt}|${job.running}` : '';
    $('st-phase').textContent = job ? `${job.phase_label}${job.step ? ' — ' + job.step : ''}` : (s.project ? 'Au repos' : '');
    $('st-attempt').textContent = job && job.attempt ? `correction ${job.attempt}/${job.max_attempts}` : '';
    $('st-request').textContent = job ? job.request : ((s.map && s.map.summary) || '');
    $('st-title').textContent = (s.map && s.map.title) || s.project || 'JARVIS CODE STUDIO';

    if (!force && s.seq === lastSeq && jobKey === lastJobKey) return;
    lastSeq = s.seq; lastJobKey = jobKey;

    renderTasks(s);
    renderFiles(s);
    renderEvents(s);
    renderTerminal(s);
    renderSummary(s);
    renderCheckpoints(s);
    renderPreviews(s);
  }

  function renderSteps(job) {
    let idx = job ? STEPS.findIndex(([k]) => k === job.phase) : -1;
    if (job && (job.phase === 'termine' || job.phase === 'bloque')) idx = STEPS.length;
    const ended = job && ['bloque', 'arrete', 'erreur'].includes(job.phase);
    $('st-steps').innerHTML = STEPS.map(([, label], i) => {
      let cls = i < idx ? 'done' : i === idx ? 'now' : 'todo';
      if (job && job.phase === 'bloque' && i === 2) cls = 'warn';
      return `<div class="st-step ${cls}"><span>${i + 1}</span>${label}</div>`;
    }).join('') + (ended ? `<div class="st-step end ${job.phase}">${esc(job.phase_label.toUpperCase())}</div>` : '');
  }

  function renderTasks(s) {
    const tasks = s.tasks || [];
    const c = s.counts || {};
    $('st-task-count').textContent = tasks.length ? `${c.done || 0}/${tasks.length}` : '';
    $('st-tasks').innerHTML = tasks.length ? tasks.map((t) => `
      <div class="st-task ${t.status}">
        <span class="st-badge">${TASK_LABEL[t.status] || t.status}</span>
        <div><div class="st-task-title">${esc(t.title)}</div>
        ${t.detail ? `<div class="st-task-detail">${esc(t.detail)}</div>` : ''}
        ${t.files && t.files.length ? `<div class="st-task-files">${t.files.map(esc).join(' · ')}</div>` : ''}</div>
      </div>`).join('') : '<div class="st-none">Aucune tâche.</div>';
  }

  function renderFiles(s) {
    const touched = s.touched || [];
    const changed = new Map((s.changed || []).map((c) => [c.path, c.op]));
    const all = s.files || [];
    $('st-file-count').textContent = `${all.length} fichier${all.length > 1 ? 's' : ''}`;
    const seen = new Set();
    const rows = [];
    touched.forEach((f) => {
      seen.add(f.path);
      rows.push({ path: f.path, op: f.op, ts: f.ts, lines: f.lines });
    });
    all.forEach((f) => { if (!seen.has(f.path)) rows.push({ path: f.path, op: changed.get(f.path) || '', size: f.size }); });
    $('st-files').innerHTML = rows.length ? rows.map((f) => `
      <button class="st-file ${f.op}" data-path="${esc(f.path)}" title="Voir les différences avant / après">
        <span class="st-op">${f.op === 'create' || f.op === 'added' ? '+' : f.op === 'delete' || f.op === 'deleted' ? '−' : f.op ? '~' : ' '}</span>
        <span class="st-path">${esc(f.path)}</span>
        <span class="st-meta">${f.lines ? f.lines + ' l.' : ''} ${f.ts ? hm(f.ts) : ''}</span>
      </button>`).join('') : '<div class="st-none">Aucun fichier.</div>';
  }

  function renderEvents(s) {
    const el = $('st-events');
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.innerHTML = (s.events || []).map((e) => {
      const ok = e.data && e.data.ok;
      const cls = e.kind === 'test' ? (ok === true ? 'ok' : ok === false ? 'bad' : '') : '';
      const src = e.data && e.data.source === 'model' ? '<span class="st-src">MODÈLE</span>' : '';
      return `<div class="st-ev ${e.kind} ${cls}"><span class="st-t">${hm(e.ts)}</span>`
        + `<span class="st-i">${KIND_ICON[e.kind] || '·'}</span><span class="st-m">${src}${esc(e.msg)}</span></div>`;
    }).join('') || '<div class="st-none">Le journal est vide.</div>';
    if (stick) el.scrollTop = el.scrollHeight;
  }

  function renderTerminal(s) {
    const el = $('st-term');
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.innerHTML = (s.terminal || []).map((l) => `<div class="tl s-${esc(l.s).split(':')[0]}">${esc(l.t) || '&nbsp;'}</div>`).join('')
      || '<div class="st-none">Aucune commande exécutée.</div>';
    if (stick) el.scrollTop = el.scrollHeight;
  }

  function renderSummary(s) {
    const lr = s.map && s.map.last_run;
    const el = $('st-summary');
    if (!lr) { el.innerHTML = '<div class="st-none">Le bilan apparaît à la fin d\'un travail.</div>'; return; }
    const checks = (lr.checks || []).map((c) => `<li class="${c.ok ? 'ok' : 'bad'}">${c.ok ? '✓' : '✗'} ${esc(c.name)}`
      + `${c.tests ? ` — ${esc(c.tests)}` : ''}${c.headline ? `<small>${esc(c.headline)}</small>` : ''}</li>`).join('');
    el.innerHTML = `
      <div class="st-sum-state ${lr.status === 'ok' ? 'ok' : 'bad'}">${lr.status === 'ok' ? 'TOUS LES CONTRÔLES PASSENT' : 'PROBLÈME RESTANT'}</div>
      <ul class="st-checks">${checks || '<li>Aucun contrôle.</li>'}</ul>
      ${lr.remaining ? `<div class="st-remaining">${esc(lr.remaining)}</div>` : ''}
      <dl class="facts">
        <dt>FICHIERS</dt><dd>${lr.files ?? '—'}</dd>
        <dt>CORRECTIONS</dt><dd>${lr.fix_attempts ?? 0}</dd>
        <dt>DURÉE</dt><dd>${lr.seconds ?? '—'} s</dd>
        <dt>LANCER</dt><dd>${esc(lr.launch || '—')}</dd>
        <dt>DÉPENDANCES</dt><dd>${esc((lr.dependencies || []).join(', ') || 'aucune')}</dd>
      </dl>`;
  }

  function renderCheckpoints(s) {
    const cps = s.checkpoints || [];
    $('st-cps').innerHTML = cps.length ? cps.map((c) => `
      <div class="st-cp"><div><b>${esc(c.label)}</b><small>${hm(c.ts)} · ${c.files} fichiers</small></div>
      <button class="mini-btn" data-restore="${esc(c.id)}">RESTAURER</button></div>`).join('')
      : '<div class="st-none">Aucun point de sauvegarde.</div>';
  }

  function renderPreviews(s) {
    const p = s.previews || [];
    $('st-previews').innerHTML = p.map((x) => `<div class="st-prev">▶ ${esc(x.label)}
      ${x.url ? `<a href="#" data-url="${esc(x.url)}">${esc(x.url)}</a>` : ''}
      <button class="mini-btn" data-stop-preview="1">ARRÊTER</button></div>`).join('');
  }

  // ── actions ──
  function status(text, kind = '') {
    const el = $('st-status');
    el.textContent = text; el.className = kind;
  }
  async function act(op, data, okText) {
    status('…', 'busy');
    const r = await ctx.rpc(op, { project: last && last.project, ...data });
    if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return r; }
    if (okText) status(okText(r), 'ok');
    refresh(true);
    return r;
  }

  async function showDiff(path) {
    const r = await ctx.rpc('studio.diff', { project: last && last.project, path });
    const v = $('st-viewer');
    $('st-viewer-title').textContent = path;
    $('st-viewer-body').innerHTML = r.error ? esc(r.error) : esc(r.diff).split('\n').map((l) => {
      const c = l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '';
      return `<div class="${c}">${l || '&nbsp;'}</div>`;
    }).join('');
    v.hidden = false;
  }

  function wire() {
    $('btn-studio').addEventListener('click', () => (open ? hide() : show()));
    $('st-close').addEventListener('click', hide);
    $('st-unavailable-close').addEventListener('click', hide);
    box.addEventListener('mousedown', (e) => { if (e.target === box) hide(); });
    $('st-project').addEventListener('change', (e) => { project = e.target.value || null; lastSeq = -1; refresh(true); });
    $('st-stop').addEventListener('click', () => act('studio.stop', {}, () => 'Arrêt demandé.'));
    $('st-preview').addEventListener('click', async () => {
      const r = await act('studio.preview', {}, (x) => (x.url ? `Aperçu : ${x.url}` : x.kind === 'gui' ? 'Application lancée.' : `Exécution terminée (code ${x.code}).`));
      if (r && r.url) ctx.call('openUrl', r.url);
      if (r && r.output !== undefined) {
        $('st-viewer-title').textContent = 'Résultat de l\'exécution';
        $('st-viewer-body').innerHTML = esc(r.output || '(aucune sortie)').split('\n').map((l) => `<div>${l || '&nbsp;'}</div>`).join('');
        $('st-viewer').hidden = false;
      }
    });
    $('st-checkpoint').addEventListener('click', () => act('studio.checkpoint', { label: 'manuel (HUD)' }, (r) => `Point de sauvegarde créé (${r.files} fichiers).`));
    $('st-folder').addEventListener('click', () => act('studio.open_folder', {}, (r) => r.folder));
    $('st-viewer-close').addEventListener('click', () => { $('st-viewer').hidden = true; });
    $('st-files').addEventListener('click', (e) => { const b = e.target.closest('[data-path]'); if (b) showDiff(b.dataset.path); });
    $('st-cps').addEventListener('click', (e) => {
      const b = e.target.closest('[data-restore]');
      if (!b) return;
      if (b.dataset.armed !== '1') { b.dataset.armed = '1'; b.textContent = 'CONFIRMER ?'; setTimeout(() => { b.dataset.armed = ''; b.textContent = 'RESTAURER'; }, 4000); return; }
      act('studio.restore', { id: b.dataset.restore }, (r) => `Restauré : ${r.written} fichiers rétablis, ${r.removed} retirés (sauvegarde de sécurité ${r.safety}).`);
    });
    $('st-previews').addEventListener('click', (e) => {
      const a = e.target.closest('[data-url]');
      if (a) { e.preventDefault(); ctx.call('openUrl', a.dataset.url); return; }
      if (e.target.closest('[data-stop-preview]')) act('studio.stop_preview', {}, () => 'Aperçu arrêté.');
    });
    ['st-root-input', 'st-attempts', 'st-timeout', 'st-confirm-plan'].forEach((id) => $(id).addEventListener('input', () => { optionsDirty = true; }));
    $('st-save').addEventListener('click', async () => {
      const root = $('st-root-input').value.trim();
      if (last && root && root !== last.root) {
        const r = await ctx.rpc('studio.set_root', { path: root });
        if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
      }
      const r = await ctx.rpc('studio.options', {
        max_fix_attempts: Number($('st-attempts').value), command_timeout: Number($('st-timeout').value),
        confirm_plan: $('st-confirm-plan').checked,
      });
      if (r.error) { status(r.error, 'err'); return; }
      optionsDirty = false; project = null;
      status('Réglages du Studio enregistrés.', 'ok');
      refresh(true);
    });
    clearInterval(pingTimer);
    pingTimer = setInterval(ping, 3000);
    ping();
  }

  wire();
  return {
    open: show,
    close: hide,
    get isOpen() { return open; },
    get viewerOpen() { return !$('st-viewer').hidden; },
    closeViewer() { $('st-viewer').hidden = true; },
  };
}
