/**
 * settings.js — the settings page.
 *
 * Connection, audio and start-up values belong to Python (`config.Settings`,
 * the same file the debug window writes): the page asks for them
 * (`requestSettings`), shows them, and sends the whole form back on
 * ENREGISTRER (`saveSettings`). Nothing is applied half-way.
 *
 * Interface choices (colour, sounds, face, performance) are the HUD's own
 * preferences and apply the moment they are touched, like everywhere else in
 * the HUD — previewing a colour should not need a Save button.
 */
const $ = (id) => document.getElementById(id);

export function createSettings(ctx) {
  const box = $('settings');
  let data = null;          // last values received from Python
  let dirty = false;
  let escArmed = false;

  // ── tabs ──
  function selectTab(tab) {
    box.querySelectorAll('.set-nav button[data-tab]').forEach((b) => b.classList.toggle('sel', b.dataset.tab === tab));
    box.querySelectorAll('.set-tab').forEach((s) => { s.hidden = s.dataset.tab !== tab; });
  }
  box.querySelectorAll('.set-nav button[data-tab]').forEach((b) => { b.onclick = () => { selectTab(b.dataset.tab); ctx.sound.play('click'); }; });

  function status(text, kind = '') {
    const el = $('s-status');
    el.textContent = text;
    el.className = kind;
  }
  function markDirty() {
    dirty = true; escArmed = false;
    status('Modifications non enregistrées.');
  }

  // ── fill from Python ──
  function option(sel, values, current, defaultLabel) {
    sel.innerHTML = '';
    const def = document.createElement('option');
    def.value = ''; def.textContent = defaultLabel;
    sel.appendChild(def);
    const list = [...values];
    if (current && !list.includes(current)) list.push(current);   // a device unplugged right now
    list.forEach((v) => {
      const o = document.createElement('option');
      o.value = v; o.textContent = v + (values.includes(v) ? '' : ' (absent)');
      sel.appendChild(o);
    });
    sel.value = current || '';
  }

  function fill(d) {
    data = d;
    if (d.error) { status(`Lecture impossible : ${d.error}`, 'err'); return; }
    $('s-host').value = d.host || '';
    $('s-port').value = d.port || 8000;
    $('s-tls').checked = !!d.use_tls;
    $('s-token').value = d.device_token || '';
    $('s-device').value = d.device_name || '';
    $('set-welcome').hidden = !!d.paired;

    option($('s-input'), d.inputs || [], d.input_device, 'Micro par défaut de Windows');
    option($('s-output'), d.outputs || [], d.output_device, 'Sortie par défaut de Windows');
    $('s-wake').checked = !!d.wake_word_enabled;
    $('s-wake-install').hidden = !!d.wake_available;
    $('s-wake-note').textContent = d.wake_available
      ? 'Détection locale : rien ne quitte ce PC avant le mot d\'éveil.'
      : 'Moteur non installé : le micro s\'ouvre au bouton ou au clic sur le noyau.';
    $('s-thresh').value = d.wake_word_threshold;
    $('s-gate').value = d.gate_seconds;
    $('s-micstart').checked = !!d.mic_open_at_start;

    $('s-autostart').checked = !!d.autostart;
    $('s-collapsed').checked = !!d.start_collapsed;
    $('s-notif').checked = !!d.notifications;
    $('s-maximized').checked = ctx.getPrefs().maximized !== false;

    $('s-devid').textContent = d.device_id || '—';
    $('s-path').textContent = d.settings_path || '—';
    rangeLabels();
    dirty = false;
    // A refresh right after a save must not wipe the "saved" message.
    if ($('s-status').textContent === 'Chargement…') status(d.paired ? '' : 'Ce PC n\'est pas encore relié à JARVIS.');
  }

  function rangeLabels() {
    $('s-thresh-val').textContent = Number($('s-thresh').value).toFixed(2);
    $('s-gate-val').textContent = `${$('s-gate').value} s`;
  }

  function form() {
    return {
      host: $('s-host').value, port: Number($('s-port').value) || 8000, use_tls: $('s-tls').checked,
      device_token: $('s-token').value, device_name: $('s-device').value,
      input_device: $('s-input').value || null, output_device: $('s-output').value || null,
      wake_word_enabled: $('s-wake').checked, wake_word_threshold: Number($('s-thresh').value),
      gate_seconds: Number($('s-gate').value), mic_open_at_start: $('s-micstart').checked,
      start_collapsed: $('s-collapsed').checked, notifications: $('s-notif').checked,
      autostart: $('s-autostart').checked,
    };
  }

  // ── connection tab ──
  $('s-token-eye').onclick = (e) => {
    e.preventDefault();
    const t = $('s-token');
    t.type = t.type === 'password' ? 'text' : 'password';
    $('s-token-eye').textContent = t.type === 'password' ? 'AFFICHER' : 'MASQUER';
  };
  $('s-token-paste').onclick = async (e) => {
    e.preventDefault();
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (text) { $('s-token').value = text; markDirty(); return; }
    } catch { /* clipboard read not allowed here */ }
    $('s-token').focus();
    status('Collez avec Ctrl+V dans le champ du jeton.');
  };
  $('s-pair-copy').onclick = (e) => {
    e.preventDefault();
    ctx.call('copy', $('s-pair-cmd').textContent);
    $('s-pair-copy').textContent = 'COPIÉ ✓';
    setTimeout(() => { $('s-pair-copy').textContent = 'COPIER'; }, 1200);
  };
  $('s-test').onclick = () => {
    const f = form();
    const r = $('s-test-result');
    r.className = 'test-result busy';
    r.textContent = 'Test en cours…';
    ctx.call('testConnection', JSON.stringify({ host: f.host, port: f.port, use_tls: f.use_tls, device_token: f.device_token }));
  };

  // ── audio tab ──
  $('s-wake-install-btn').onclick = (e) => {
    e.preventDefault();
    ctx.call('installWakeWord');
    $('s-wake-install').textContent = 'Installation lancée (téléchargement du modèle) — suivez le journal. Rouvrez cette page ensuite.';
  };
  ['s-thresh', 's-gate'].forEach((id) => $(id).addEventListener('input', rangeLabels));

  // ── interface tab: applies immediately ──
  const sw = $('s-swatches');
  ctx.swatches.forEach(([hex, name]) => {
    const b = document.createElement('button');
    b.dataset.hex = hex; b.style.setProperty('--c', hex); b.title = name;
    b.onclick = (e) => { e.preventDefault(); ctx.applyAccent(hex); ctx.sound.play('click'); };
    sw.appendChild(b);
  });
  $('s-sound').onchange = () => ctx.setSound($('s-sound').checked, Number($('s-volume').value));
  $('s-volume').oninput = () => ctx.setSound($('s-sound').checked, Number($('s-volume').value));
  $('s-volume').onchange = () => ctx.sound.play('reply');
  $('s-face').onchange = () => ctx.setFace($('s-face').checked);
  const q = $('s-quality');
  [['auto', 'AUTO'], ...ctx.tiers.map((t, i) => [String(i), t.name])].forEach(([v, label]) => {
    const b = document.createElement('button');
    b.dataset.q = v; b.textContent = label;
    b.onclick = (e) => { e.preventDefault(); ctx.setQuality(v === 'auto' ? 'auto' : Number(v)); refreshInterface(); };
    q.appendChild(b);
  });

  function refreshInterface() {
    const p = ctx.getPrefs();
    $('s-sound').checked = !!p.sound;
    $('s-volume').value = p.volume ?? .35;
    $('s-face').checked = !!p.face;
    $('s-face-row').hidden = !ctx.faceAvailable();
    sw.querySelectorAll('button').forEach((b) => b.classList.toggle('sel', b.dataset.hex.toLowerCase() === String(p.accent).toLowerCase()));
    const g = ctx.gov();
    q.querySelectorAll('button').forEach((b) => {
      const v = b.dataset.q;
      b.classList.toggle('sel', v === 'auto' ? g.locked === null : g.locked === Number(v));
    });
    $('s-quality-note').textContent = `Niveau actuel : ${g.Q.name} — ${g.fps ? `${g.fps.toFixed(0)} FPS` : 'mesure en cours'}${g.locked === null ? ', choisi automatiquement selon ce PC.' : ', forcé.'}`;
  }

  // ── everything that is a Python setting marks the form dirty ──
  box.querySelectorAll('[data-tab="connexion"] input, [data-tab="audio"] input, [data-tab="audio"] select, [data-tab="demarrage"] input')
    .forEach((el) => el.addEventListener(el.type === 'checkbox' || el.tagName === 'SELECT' ? 'change' : 'input', markDirty));

  $('s-debug').onclick = () => ctx.call('settings');
  $('s-cancel').onclick = () => close(true);
  $('s-save').onclick = save;
  box.addEventListener('mousedown', (e) => { if (e.target === box) close(); });

  function save() {
    status('Enregistrement…');
    // the full-screen preference is the HUD's own; it is not in config.Settings
    ctx.setPrefs({ maximized: $('s-maximized').checked });
    ctx.call('saveSettings', JSON.stringify(form()));
    ctx.sound.play('send');
  }

  function open(tab = 'connexion') {
    box.hidden = false;
    selectTab(tab);
    status('Chargement…');
    refreshInterface();
    ctx.call('requestSettings');
    ctx.sound.play('open');
    if (tab === 'connexion') setTimeout(() => (!$('s-host').value ? $('s-host') : $('s-token')).focus(), 80);
  }

  function close(force = false) {
    if (dirty && !force && !escArmed) {
      escArmed = true;
      status('Modifications non enregistrées — Échap encore pour fermer sans enregistrer.', 'err');
      return;
    }
    box.hidden = true;
    dirty = false; escArmed = false;
    ctx.sound.play('close');
  }

  return {
    open,
    close,
    get isOpen() { return !box.hidden; },
    fill,
    refreshInterface,
    tested(r) {
      const el = $('s-test-result');
      el.className = `test-result ${r.ok ? 'ok' : 'err'}`;
      el.textContent = (r.ok ? '✓ ' : '✕ ') + (r.detail || '');
      ctx.sound.play(r.ok ? 'reply' : 'error');
    },
    saved(r) {
      dirty = false;
      const extra = r.autostart ? ` Démarrage : ${r.autostart}.` : '';
      status((r.detail || 'Enregistré.') + extra, r.ok ? 'ok' : 'err');
    },
    setLink(label) { $('set-link-text').textContent = label; },
  };
}
