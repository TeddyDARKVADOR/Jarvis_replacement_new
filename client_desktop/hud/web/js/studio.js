/**
 * studio.js — the Jarvis Code Studio panel.
 *
 * Everything here is read from `studio.status` (core/code_workspace/service.py):
 * the journal the tools write, the task list, the terminal lines of commands
 * that really ran, the checkpoints on disk. The page invents nothing — it
 * polls once a second while open, and asks `studio.ping` every few seconds
 * while closed so the header button can glow when JARVIS is at work (and the
 * panel opens by itself when a new job starts).
 *
 * The summary keeps apart what was CHECKED (tests, browser paths), what was
 * LAUNCHED (the script's own run) and what was SHOWN (the preview window): an
 * app that opens is not an app that works, and the verdict says which.
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
const VERDICT = {
  ok: ['VALIDÉ', 'ok', 'Les contrôles passent et chaque critère est prouvé par un test réel.'],
  partial: ['PARTIELLEMENT VÉRIFIÉ', 'warn', ''],
  unverified: ['NON VÉRIFIÉ', 'warn', ''],
  blocked: ['PROBLÈME RESTANT', 'bad', ''],
};
const CRIT = {
  passed: ['✓', 'ok', 'prouvé'], failed: ['✗', 'bad', 'échoue'],
  unverified: ['?', 'warn', 'non vérifié'], manual: ['◇', 'muted', 'à vérifier par vous'],
};
const CHECK_STATE = (ok) => (ok === true ? ['✓', 'ok'] : ok === false ? ['✗', 'bad'] : ['•', 'muted']);
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
      const res = job.result || {};
      const v = VERDICT[res.status];
      ctx.toast('CODE STUDIO', `« ${job.project} » : ${v ? v[0] : job.phase_label}`, res.status === 'ok' ? null : '#FBBF6B', 6000);
      // The engine opens the preview itself when the HUD host lets it; this is
      // the fallback when it could not (no opener at that moment).
      const pv = res.preview;
      if (pv && pv.url && !pv.opened) ctx.call('openPreview', job.project, pv.url, res.title || job.project, res.status || '');
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
      $('st-auto-preview').checked = o.auto_preview !== false;
      $('st-auto-run').checked = o.auto_run_scripts !== false;
    }
    $('st-safety').textContent = s.safety || '';
    const sb = s.sandbox || {};
    $('st-sandbox').classList.toggle('off', !sb.available);
    $('st-sandbox').title = sb.available
      ? 'Lancer le projet dans Windows Sandbox : machine jetable, réseau coupé, projet en lecture seule'
      : (sb.reason || 'Windows Sandbox indisponible');
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
      <button class="st-file ${f.op}" data-path="${esc(f.path)}" title="Voir le code (et les différences avant / après)">
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
    // Summaries written before verdicts existed only knew ok / blocked.
    const status = VERDICT[lr.status] ? lr.status : 'blocked';
    const [label, cls, okText] = VERDICT[status];
    const checks = (lr.checks || []).map((c) => {
      const [icon, k] = CHECK_STATE(c.ok);
      const name = c.kind === 'web' ? 'navigateur' : c.kind === 'run' ? `exécution : ${c.name}` : c.kind === 'syntax' ? 'syntaxe' : c.name;
      return `<li class="${k}">${icon} ${esc(name)}${c.ok === null ? ' — non lancé / non concluant' : ''}`
        + `${c.tests ? ` — ${esc(c.tests)}` : ''}${c.headline ? `<small>${esc(c.headline)}</small>` : ''}</li>`;
    }).join('');
    const crit = (lr.criteria || []).map((c) => {
      const [icon, k, word] = CRIT[c.status] || CRIT.unverified;
      const ev = c.evidence && c.status !== 'manual' ? ` — ${esc(c.evidence)}` : '';
      return `<li class="${k}"><b>${icon} ${esc(c.id)}</b> ${esc(c.criterion)}<small>${esc(word)}${ev}</small></li>`;
    }).join('');
    const proven = (lr.criteria || []).filter((c) => c.status === 'passed').length;
    const an = lr.analysis, run = lr.run, pv = lr.preview, br = lr.browser;
    let launch = '';
    if (run) {
      const st = run.timed_out ? 'toujours actif après le délai (arrêté)' : `code ${run.code}`;
      launch += `<div class="st-run ${run.crashed ? 'bad' : ''}"><b>PROGRAMME LANCÉ</b> <code>${esc(run.command)}</code> — ${esc(st)}`
        + `${run.crashed ? ' — arrêté sur une erreur' : ''}<button class="mini-btn" data-sum="run">VOIR LA SORTIE</button></div>`;
    }
    if (pv && pv.kind === 'web') {
      launch += `<div class="st-run"><b>APERÇU</b> ${pv.opened ? 'ouvert dans la fenêtre d\'aperçu' : pv.url ? 'prêt' : 'non ouvert'}`
        + `<button class="mini-btn" data-sum="preview">${pv.opened ? 'RÉOUVRIR' : 'OUVRIR L\'APERÇU'}</button></div>`;
    } else if (pv && pv.kind === 'gui') {
      launch += '<div class="st-run"><b>APPLICATION</b> jamais lancée automatiquement'
        + '<button class="mini-btn" data-sum="preview">LANCER L\'APPLICATION</button>'
        + '<button class="mini-btn" data-sum="sandbox">DANS WINDOWS SANDBOX</button></div>';
    }
    if (br) {
      const blocked = br.blocked && br.blocked.length ? `${br.blocked.length} requête(s) Internet bloquée(s)` : 'aucune requête externe';
      launch += `<div class="st-run"><b>NAVIGATEUR DE TEST</b> ${blocked}`
        + `${br.screenshot ? '<button class="mini-btn" data-sum="shot">CAPTURE</button>' : ''}</div>`;
    }
    const analysisRow = (a, label, key) => (a ? `<div class="st-analysis ${a.ok === false ? 'warn' : ''}"><b>${label}</b> ${esc(a.headline || '')}`
      + `${a.count ? `<button class="mini-btn" data-sum="${key}">DÉTAIL</button>` : ''}`
      + '<small>informative : n\'entre pas dans le verdict</small></div>' : '');
    const analysis = analysisRow(an, 'ANALYSE', 'lint') + analysisRow(lr.types, 'TYPES', 'types');
    el.innerHTML = `
      <div class="st-sum-state ${cls}">${label}</div>
      <div class="st-why">${esc(status === 'ok' ? okText : (lr.why || lr.remaining || ''))}</div>
      <h5>CONTRÔLES</h5>
      <ul class="st-checks">${checks || '<li class="muted">Aucun contrôle.</li>'}</ul>
      ${crit ? `<h5>CRITÈRES D'ACCEPTATION <span>${proven}/${lr.criteria.length} prouvés</span></h5><ul class="st-crit">${crit}</ul>` : ''}
      ${analysis}
      ${launch}
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

  // ── viewer: source (line numbers, colours, search), diff, outputs, screenshot ──
  const KEYWORDS = {
    py: 'and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|self|True|try|while|with|yield',
    js: 'async|await|break|case|catch|class|const|continue|default|delete|do|else|export|extends|false|finally|for|function|if|import|in|instanceof|let|new|null|of|return|static|super|switch|this|throw|true|try|typeof|undefined|var|void|while|yield',
  };
  const LANG = { py: 'py', js: 'js', mjs: 'js', ts: 'js', json: 'js', css: 'css', html: 'html', htm: 'html' };
  const TOKENS = {
    py: new RegExp(`(#.*$)|("(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*')|\\b(\\d+(?:\\.\\d+)?)\\b|\\b(${KEYWORDS.py})\\b`, 'g'),
    js: new RegExp(`(\\/\\/.*$)|("(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|\`(?:\\\\.|[^\`\\\\])*\`)|\\b(\\d+(?:\\.\\d+)?)\\b|\\b(${KEYWORDS.js})\\b`, 'g'),
  };
  function highlight(line, lang) {
    if (lang === 'html' || lang === 'css') {
      return esc(line).replace(/(&lt;\/?[\w-]+|\/?&gt;)/g, '<span class="hk">$1</span>')
        .replace(/(&quot;.*?&quot;)/g, '<span class="hs">$1</span>');
    }
    const re = TOKENS[lang];
    if (!re) return esc(line);
    re.lastIndex = 0;
    let out = '', lastIdx = 0, m;
    while ((m = re.exec(line))) {
      if (!m[0].length) { re.lastIndex += 1; continue; }
      out += esc(line.slice(lastIdx, m.index));
      const cls = m[1] ? 'hc' : m[2] ? 'hs' : m[3] ? 'hn' : 'hk';
      out += `<span class="${cls}">${esc(m[0])}</span>`;
      lastIdx = m.index + m[0].length;
    }
    return out + esc(line.slice(lastIdx));
  }

  let viewing = null;            // {path, mode, content}
  let editing = false;           // the source is a textarea
  function openViewer(title, html, opts = {}) {
    $('st-viewer-title').textContent = title;
    $('st-viewer-body').innerHTML = html;
    $('st-viewer-body').scrollTop = 0;
    $('st-tab-src').hidden = !opts.file;
    $('st-tab-diff').hidden = !opts.file;
    $('st-find').hidden = !opts.find;
    $('st-find-count').textContent = '';
    if (!opts.file) viewing = null;
    $('st-tab-src').classList.toggle('on', opts.mode === 'src');
    $('st-tab-diff').classList.toggle('on', opts.mode === 'diff');
    $('st-edit').hidden = !(opts.file && opts.mode === 'src' && !opts.error);
    $('st-save-file').hidden = true;
    $('st-cancel-edit').hidden = true;
    editing = false;
    $('st-viewer').hidden = false;
  }
  const plain = (text) => esc(text || '').split('\n').map((l) => `<div>${l || '&nbsp;'}</div>`).join('');

  async function showFile(path, mode = 'src', line = 0) {
    viewing = { path, mode };
    if (mode === 'diff') { showDiff(path); return; }
    const r = await ctx.rpc('studio.file', { project: last && last.project, path });
    if (r.error) { openViewer(path, esc(r.error), { file: true, mode, error: true }); return; }
    viewing.content = String(r.content || '');
    const lang = LANG[(path.split('.').pop() || '').toLowerCase()];
    const lines = viewing.content.replace(/\n$/, '').split('\n');
    const html = lines.map((l, i) => `<div class="sl"><span class="ln">${i + 1}</span><span class="lc">${highlight(l, lang) || '&nbsp;'}</span></div>`).join('');
    openViewer(`${path} — ${lines.length} lignes`, `<div class="st-code">${html}</div>`, { file: true, mode, find: true });
    const q = $('st-find').value.trim();
    if (q) find(q);
    const target = line > 0 ? $('st-viewer-body').querySelectorAll('.sl')[line - 1] : null;
    if (target) { target.classList.add('goto'); target.scrollIntoView({ block: 'center' }); }
  }

  // ── a quick edit, saved through the Studio (journaled, undoable) ──
  function editFile() {
    if (!viewing || viewing.content === undefined) return;
    editing = true;
    $('st-viewer-body').innerHTML = '<textarea id="st-editor" class="st-editor" spellcheck="false"></textarea>';
    const ta = $('st-editor');
    ta.value = viewing.content;
    ta.focus();
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') {
        e.preventDefault();
        ta.setRangeText('    ', ta.selectionStart, ta.selectionEnd, 'end');
      } else if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        saveFile();
      }
    });
    ['st-edit', 'st-tab-src', 'st-tab-diff', 'st-find'].forEach((id) => { $(id).hidden = true; });
    $('st-find-count').textContent = '';
    $('st-save-file').hidden = false;
    $('st-cancel-edit').hidden = false;
  }
  const dirty = () => editing && $('st-editor') && $('st-editor').value !== viewing.content;
  async function saveFile() {
    if (!editing || !viewing) return;
    const r = await act('studio.write', { path: viewing.path, content: $('st-editor').value },
      (x) => `${x.path} enregistré (${x.lines} lignes) — annulable. Relancez les TESTS pour vérifier.`);
    if (r && !r.error) showFile(viewing.path, 'src');
  }

  async function runTests() {
    status('Tests en cours…', 'busy');
    const r = await ctx.rpc('studio.test', { project: last && last.project });
    if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
    const verdict = r.ok ? 'RÉUSSIS' : r.timed_out ? 'DÉLAI DÉPASSÉ' : `ÉCHEC (code ${r.code ?? '—'})`;
    status(`Tests ${verdict.toLowerCase()} : ${r.command}`, r.ok ? 'ok' : 'err');
    openViewer(`Tests ${verdict} — ${r.command}`, plain(r.output || '(aucune sortie)'));
    refresh(true);
  }

  async function showDiff(path) {
    const r = await ctx.rpc('studio.diff', { project: last && last.project, path });
    const html = r.error ? esc(r.error) : (r.diff ? esc(r.diff).split('\n').map((l) => {
      const c = l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '';
      return `<div class="${c}">${l || '&nbsp;'}</div>`;
    }).join('') : '<div class="st-none">Aucune différence avec le dernier point de sauvegarde.</div>');
    openViewer(path, html, { file: true, mode: 'diff' });
  }

  function find(q) {
    const rows = [...$('st-viewer-body').querySelectorAll('.sl')];
    const needle = q.toLowerCase();
    let first = null, n = 0;
    rows.forEach((row) => {
      const hit = !!needle && row.textContent.toLowerCase().includes(needle);
      row.classList.toggle('hit', hit);
      if (hit) { n += 1; first = first || row; }
    });
    $('st-find-count').textContent = needle ? `${n} ligne${n > 1 ? 's' : ''}` : '';
    if (first) first.scrollIntoView({ block: 'center' });
  }

  async function openPreview() {
    const r = await act('studio.preview', {}, (x) => (x.url ? (x.opened ? 'Aperçu ouvert dans la fenêtre d\'aperçu.' : `Aperçu : ${x.url}`)
      : x.kind === 'gui' ? 'Application lancée : fermez-la ou utilisez ARRÊTER.' : `Exécution terminée (code ${x.code}).`));
    if (!r || r.error) return;
    if (r.url && !r.opened) {
      const lr = (last && last.map && last.map.last_run) || {};
      ctx.call('openPreview', (last && last.project) || '', r.url, (last && last.map && last.map.title) || '', lr.status || '');
    }
    if (r.output !== undefined) openViewer('Résultat de l\'exécution', plain(r.output || '(aucune sortie)'));
  }

  async function summaryAction(what) {
    const lr = last && last.map && last.map.last_run;
    if (!lr) return;
    if (what === 'run' && lr.run) openViewer(`Sortie de ${lr.run.command}`, plain(lr.run.output || '(aucune sortie)'));
    else if (what === 'preview') openPreview();
    else if (what === 'sandbox') act('studio.sandbox', {}, (r) => r.message || '');
    else if ((what === 'lint' && lr.analysis) || (what === 'types' && lr.types)) {
      const a = what === 'lint' ? lr.analysis : lr.types;
      // Each remark opens its file at its line.
      openViewer(what === 'lint' ? 'Analyse statique (Ruff : F, E9, PLE)' : 'Analyse des types (Pyright, mode basic)',
        (a.findings || []).map((f) => `<div class="st-goto" data-goto-path="${esc(f.path)}" data-goto-line="${Number(f.line) || 0}">`
          + `<b>${esc(f.path)}:${esc(f.line)}</b>  ${esc(f.code)}  ${esc(f.message)}</div>`).join('') || plain('(rien)'));
    } else if (what === 'shot') {
      const r = await ctx.rpc('studio.screenshot', { project: last && last.project });
      openViewer('Capture du navigateur de test', r.error ? esc(r.error) : `<img class="st-shot" src="${r.url}" alt="capture de la page">`);
    }
  }

  function wire() {
    $('btn-studio').addEventListener('click', () => (open ? hide() : show()));
    $('st-close').addEventListener('click', hide);
    $('st-unavailable-close').addEventListener('click', hide);
    box.addEventListener('mousedown', (e) => { if (e.target === box) hide(); });
    $('st-project').addEventListener('change', (e) => { project = e.target.value || null; lastSeq = -1; refresh(true); });
    $('st-stop').addEventListener('click', () => act('studio.stop', {}, () => 'Arrêt demandé.'));
    $('st-preview').addEventListener('click', openPreview);
    $('st-sandbox').addEventListener('click', () => act('studio.sandbox', {}, (r) => r.message || ''));
    $('st-test').addEventListener('click', runTests);
    $('st-edit').addEventListener('click', editFile);
    $('st-save-file').addEventListener('click', saveFile);
    $('st-cancel-edit').addEventListener('click', () => { if (viewing) showFile(viewing.path, 'src'); });
    $('st-viewer-body').addEventListener('click', (e) => {
      const g = e.target.closest('[data-goto-path]');
      if (g) showFile(g.dataset.gotoPath, 'src', Number(g.dataset.gotoLine) || 0);
    });
    $('st-summary').addEventListener('click', (e) => { const b = e.target.closest('[data-sum]'); if (b) summaryAction(b.dataset.sum); });
    $('st-tab-src').addEventListener('click', () => { if (viewing) showFile(viewing.path, 'src'); });
    $('st-tab-diff').addEventListener('click', () => { if (viewing) showFile(viewing.path, 'diff'); });
    $('st-find').addEventListener('input', (e) => find(e.target.value.trim()));
    $('st-checkpoint').addEventListener('click', () => act('studio.checkpoint', { label: 'manuel (HUD)' }, (r) => `Point de sauvegarde créé (${r.files} fichiers).`));
    $('st-folder').addEventListener('click', () => act('studio.open_folder', {}, (r) => r.folder));
    $('st-viewer-close').addEventListener('click', () => { $('st-viewer').hidden = true; });
    $('st-files').addEventListener('click', (e) => { const b = e.target.closest('[data-path]'); if (b) showFile(b.dataset.path, 'src'); });
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
    ['st-root-input', 'st-attempts', 'st-timeout', 'st-confirm-plan', 'st-auto-preview', 'st-auto-run'].forEach((id) => $(id).addEventListener('input', () => { optionsDirty = true; }));
    $('st-save').addEventListener('click', async () => {
      const root = $('st-root-input').value.trim();
      if (last && root && root !== last.root) {
        const r = await ctx.rpc('studio.set_root', { path: root });
        if (r.error) { status(r.error, 'err'); ctx.sound.play('error'); return; }
      }
      const r = await ctx.rpc('studio.options', {
        max_fix_attempts: Number($('st-attempts').value), command_timeout: Number($('st-timeout').value),
        confirm_plan: $('st-confirm-plan').checked,
        auto_preview: $('st-auto-preview').checked, auto_run_scripts: $('st-auto-run').checked,
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
    closeViewer() {
      // Escape never throws away an unsaved edit.
      if (dirty()) { status('Modifications non enregistrées : ENREGISTRER (Ctrl+S) ou ANNULER.', 'err'); return; }
      $('st-viewer').hidden = true;
    },
  };
}
