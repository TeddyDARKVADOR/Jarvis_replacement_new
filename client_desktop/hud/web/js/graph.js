/**
 * graph.js — le panneau GRAPHE : ce que JARVIS sait faire, tel que son graphe le décrit.
 *
 * Les données viennent de capabilities/graphs/ (lues par window.py, jamais par le
 * réseau) et arrivent par HUD.showGraph(). Il n'y a pas de bouton : le panneau
 * s'ouvre quand on demande à JARVIS de montrer son graphe (plugins/capabilities.py).
 *
 * Disposition radiale déterministe : la racine au centre, puis chaque anneau suit
 * les arêtes « contient ». Les autres relations ne s'allument qu'autour du nœud
 * survolé ou choisi, sinon le graphe général devient une pelote.
 */
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fold = (t) => String(t ?? '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');

const KIND_FR = {
  racine: 'RACINE', domaine: 'DOMAINE', fonction: 'FONCTION', outil: 'OUTIL', module: 'MODULE',
  regle: 'RÈGLE', donnee: 'DONNÉE', test: 'TEST', procedure: 'PROCÉDURE', limite: 'LIMITE',
};
const KIND_COLOR = {
  fonction: '#93C5FD', outil: '#F9A8D4', module: '#A5B4FC', regle: '#FCD34D', donnee: '#5EEAD4',
  test: '#86EFAC', procedure: '#FDBA74', limite: '#FCA5A5',
};
const STATUS_FR = { ok: 'VÉRIFIÉ', partiel: 'PARTIEL', a_tester: 'À TESTER', bug: 'BUG CONNU' };
const STATUS_COLOR = { ok: '#6EE7B7', partiel: '#FBBF6B', a_tester: '#C4B5FD', bug: '#F87171' };
const REL_FR = {
  contient: 'contient', utilise: 'utilise', expose: 'expose', protege: 'protège',
  alimente: 'alimente', teste: 'teste', deploie: 'déploie', remplace: 'remplace',
};
const RADIUS = { racine: 15, domaine: 10, fonction: 6.5, outil: 6, module: 5.5, regle: 6, donnee: 5.5, test: 5.5, procedure: 5.5, limite: 6 };

export function createGraph(ctx) {
  const box = $('graph');
  const canvas = $('graph-canvas');
  const g2 = canvas.getContext('2d');
  let open = false;
  let data = null;            // {graphs, start, focus}
  let current = null;         // id du graphe affiché
  let trail = [];             // fil d'Ariane
  let nodes = [], byId = new Map(), edges = [], adj = new Map();
  let view = { s: 1, x: 0, y: 0 };
  let hover = null, selected = null, matches = new Set();
  let quiet = false;          // la fiche montre la racine, sans griser le reste
  let drag = null, dpr = 1, accent = '#7DD3FC';

  // ── ouverture ──
  function show(payload) {
    if (!payload || typeof payload !== 'object') return;
    if (payload.error || !payload.graphs) {
      data = null;
      box.hidden = false; open = true;
      $('graph-side').innerHTML = `<div class="set-note accent">${esc(payload.error || 'aucun graphe reçu')}</div>`;
      return;
    }
    data = payload;
    box.hidden = false; open = true;
    accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || accent;
    ctx.sound.play('open');
    trail = []; current = null;
    load(payload.start && payload.graphs[payload.start] ? payload.start : 'general', payload.focus);
  }
  function hide() {
    box.hidden = true; open = false; hover = selected = null;
    ctx.sound.play('close');
  }

  // ── un graphe ──
  function load(id, focus = '') {
    const gr = data.graphs[id];
    if (!gr) return;
    if (current && current !== id && trail[trail.length - 1] !== current) trail.push(current);
    current = id;
    const pos = trail.indexOf(id);
    if (pos >= 0) trail = trail.slice(0, pos);
    nodes = gr.nodes.map((n) => ({ ...n }));
    byId = new Map(nodes.map((n) => [n.id, n]));
    edges = (gr.edges || []).filter(([a, b]) => byId.has(a) && byId.has(b)).map(([a, b, rel]) => ({ a, b, rel }));
    adj = new Map(nodes.map((n) => [n.id, []]));
    for (const e of edges) { adj.get(e.a).push(e); adj.get(e.b).push(e); }
    layout();
    hover = null; selected = null; matches = new Set();
    $('graph-q').value = '';
    $('graph-title').textContent = gr.title || id;
    $('graph-sub').textContent = gr.summary || '';
    const total = nodes.length;
    const tools = nodes.filter((n) => n.kind === 'outil').length;
    $('graph-meta').textContent = `mis à jour le ${frDate(gr.updated)} · ${total} nœuds · ${edges.length} liens${tools ? ` · ${tools} outils` : ''}`;
    renderTrail();
    resize();
    fit();
    const target = focus && byId.get(focus);
    select(target || byId.get(rootId()) || null, !!target);
    quiet = !target;
  }
  function frDate(d) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || '');
    return m ? `${m[3]}/${m[2]}/${m[1]}` : (d || '—');
  }
  function rootId() {
    const r = nodes.find((n) => n.kind === 'racine');
    return r ? r.id : (nodes[0] && nodes[0].id);
  }
  function renderTrail() {
    const items = [...trail, current].map((id, i, all) => {
      const t = (data.graphs[id] && data.graphs[id].id) || id;
      const label = id === 'general' ? 'GÉNÉRAL' : t.toUpperCase();
      return i === all.length - 1 ? `<b>${esc(label)}</b>` : `<button data-g="${esc(id)}">${esc(label)}</button>`;
    });
    $('graph-trail').innerHTML = items.join('<span>›</span>');
    for (const b of $('graph-trail').querySelectorAll('button')) b.onclick = () => load(b.dataset.g);
  }

  // ── disposition radiale ──
  function layout() {
    const root = rootId();
    const children = new Map(nodes.map((n) => [n.id, []]));
    const parent = new Map([[root, null]]);
    const depth = new Map([[root, 0]]);
    const queue = [root];
    // d'abord l'arbre « contient », puis tout nœud encore isolé par n'importe quelle arête
    for (const pass of ['contient', null]) {
      let i = 0;
      const q = pass ? queue : [...depth.keys()];
      while (i < q.length) {
        const id = q[i++];
        for (const e of adj.get(id) || []) {
          if (pass && (e.rel !== pass || e.a !== id)) continue;
          const other = e.a === id ? e.b : e.a;
          if (depth.has(other)) continue;
          depth.set(other, depth.get(id) + 1); parent.set(other, id); children.get(id).push(other);
          q.push(other);
        }
      }
    }
    for (const n of nodes) if (!depth.has(n.id)) { depth.set(n.id, 1); parent.set(n.id, root); children.get(root).push(n.id); }
    const leaves = new Map();
    const count = (id) => {
      const c = children.get(id);
      const v = c.length ? c.reduce((s, k) => s + count(k), 0) : 1;
      leaves.set(id, v); return v;
    };
    count(root);
    // Un anneau intérieur assez large pour ses libellés, et chaque branche une
    // part minimale d'angle : un domaine à une seule feuille ne doit pas se
    // retrouver collé à son voisin.
    const inner = Math.max(170, children.get(root).length * 19);
    const step = Math.max(200, Math.min(340, leaves.get(root) * 4.6));
    const ring = (d) => (d === 0 ? 0 : inner + (d - 1) * step);
    const weight = (k) => leaves.get(k) + 1.5;
    const place = (id, a0, a1) => {
      const n = byId.get(id), d = depth.get(id), mid = (a0 + a1) / 2;
      n.x = Math.cos(mid) * ring(d); n.y = Math.sin(mid) * ring(d); n.angle = mid; n.depth = d;
      const kids = children.get(id);
      const total = kids.reduce((s, k) => s + weight(k), 0);
      let a = a0;
      for (const k of kids) {
        const span = (a1 - a0) * weight(k) / total;
        place(k, a, a + span); a += span;
      }
    };
    place(root, -Math.PI / 2, Math.PI * 1.5);
    // écarter ce qui se chevauche, sans quitter son anneau de beaucoup
    for (let it = 0; it < 60; it++) {
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const p = nodes[i], q = nodes[j];
          const dx = q.x - p.x, dy = q.y - p.y, dist = Math.hypot(dx, dy) || 0.01;
          const min = 26 + (RADIUS[p.kind] || 6) + (RADIUS[q.kind] || 6);
          if (dist < min) {
            const push = (min - dist) / 2, ux = dx / dist, uy = dy / dist;
            if (p.depth) { p.x -= ux * push; p.y -= uy * push; }
            if (q.depth) { q.x += ux * push; q.y += uy * push; }
          }
        }
      }
    }
    for (const n of nodes) n.parent = parent.get(n.id);
  }

  // ── vue ──
  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    canvas.style.width = `${r.width}px`; canvas.style.height = `${r.height}px`;
    draw();
  }
  function fit() {
    if (!nodes.length) return;
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
    const w = canvas.width / dpr, h = canvas.height / dpr;
    const bw = Math.max(...xs) - Math.min(...xs) + 220, bh = Math.max(...ys) - Math.min(...ys) + 120;
    view.s = Math.max(0.25, Math.min(1.6, Math.min(w / bw, h / bh)));
    view.x = w / 2 - ((Math.max(...xs) + Math.min(...xs)) / 2) * view.s;
    view.y = h / 2 - ((Math.max(...ys) + Math.min(...ys)) / 2) * view.s;
    draw();
  }
  const toScreen = (n) => [n.x * view.s + view.x, n.y * view.s + view.y];
  function pick(mx, my) {
    let best = null, bd = 1e9;
    for (const n of nodes) {
      const [x, y] = toScreen(n);
      const d = Math.hypot(x - mx, y - my);
      const r = Math.max(9, (RADIUS[n.kind] || 6) * view.s + 4);
      if (d < r && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  function colorOf(n) {
    if (n.kind === 'racine' || n.kind === 'domaine') return accent;
    return KIND_COLOR[n.kind] || '#CBD7EA';
  }

  function draw() {
    if (!open) return;
    const w = canvas.width, h = canvas.height;
    g2.setTransform(1, 0, 0, 1, 0, 0);
    g2.clearRect(0, 0, w, h);
    g2.setTransform(dpr, 0, 0, dpr, 0, 0);
    const focus = (quiet ? null : selected) || hover;
    const near = new Set();
    if (focus) { near.add(focus.id); for (const e of adj.get(focus.id) || []) { near.add(e.a); near.add(e.b); } }
    const dim = focus || matches.size;

    for (const e of edges) {
      const a = byId.get(e.a), b = byId.get(e.b);
      const [x1, y1] = toScreen(a), [x2, y2] = toScreen(b);
      const lit = focus && (e.a === focus.id || e.b === focus.id);
      if (e.rel !== 'contient' && !lit) continue;
      g2.beginPath();
      g2.moveTo(x1, y1);
      if (e.rel === 'contient') g2.lineTo(x2, y2);
      else { const mx = (x1 + x2) / 2, my = (y1 + y2) / 2; g2.quadraticCurveTo(mx + (y2 - y1) * 0.18, my - (x2 - x1) * 0.18, x2, y2); }
      g2.setLineDash(e.rel === 'contient' ? [] : [5, 4]);
      g2.strokeStyle = lit ? accent : 'rgba(125,160,200,.16)';
      g2.globalAlpha = lit ? 0.9 : (dim ? 0.35 : 1);
      g2.lineWidth = lit ? 1.6 : 1;
      g2.stroke();
    }
    g2.setLineDash([]);
    g2.globalAlpha = 1;

    for (const n of nodes) {
      const [x, y] = toScreen(n);
      const r = Math.max(3, (RADIUS[n.kind] || 6) * Math.min(1.4, Math.max(0.6, view.s)));
      const faded = (focus && !near.has(n.id)) || (matches.size && !matches.has(n.id) && !(focus && near.has(n.id)));
      g2.globalAlpha = faded ? 0.22 : 1;
      if (n.graph) {                      // un sous-graphe s'ouvre depuis ce nœud
        g2.beginPath(); g2.arc(x, y, r + 5, 0, Math.PI * 2);
        g2.strokeStyle = accent; g2.lineWidth = 1; g2.setLineDash([2, 3]); g2.stroke(); g2.setLineDash([]);
      }
      g2.beginPath(); g2.arc(x, y, r, 0, Math.PI * 2);
      g2.fillStyle = colorOf(n); g2.globalAlpha *= n.kind === 'racine' ? 1 : 0.85; g2.fill();
      g2.globalAlpha = faded ? 0.22 : 1;
      g2.lineWidth = 2; g2.strokeStyle = STATUS_COLOR[n.status] || 'rgba(107,122,147,.9)'; g2.stroke();
      if (n === selected || matches.has(n.id)) {
        g2.beginPath(); g2.arc(x, y, r + 3, 0, Math.PI * 2);
        g2.strokeStyle = '#fff'; g2.lineWidth = 1.2; g2.stroke();
      }
      const showLabel = n.kind === 'racine' || n.kind === 'domaine' || view.s > 0.85
        || n === hover || n === selected || matches.has(n.id) || (focus && near.has(n.id));
      if (showLabel) {
        const big = n.kind === 'racine' || n.kind === 'domaine';
        g2.font = `${big ? 600 : 400} ${big ? 12.5 : 11}px Bahnschrift, "Segoe UI", sans-serif`;
        const out = n.depth ? 1 : 0;
        const ax = out ? Math.cos(Math.atan2(n.y, n.x)) : 0, ay = out ? Math.sin(Math.atan2(n.y, n.x)) : 1;
        g2.textAlign = !out ? 'center' : ax > 0.25 ? 'left' : ax < -0.25 ? 'right' : 'center';
        g2.textBaseline = 'middle';
        const lx = x + ax * (r + 7), ly = y + ay * (r + 9);
        g2.lineWidth = 3; g2.strokeStyle = 'rgba(3,6,11,.85)'; g2.strokeText(n.label, lx, ly);
        g2.fillStyle = big ? '#E6EEF9' : '#B4C2D8'; g2.fillText(n.label, lx, ly);
      }
    }
    g2.globalAlpha = 1;
  }

  // ── la fiche d'un nœud ──
  function select(n, center = false) {
    selected = n; quiet = false;
    renderSide();
    if (n && center) {
      const w = canvas.width / dpr, h = canvas.height / dpr;
      view.s = Math.max(view.s, 0.9);
      view.x = w / 2 - n.x * view.s; view.y = h / 2 - n.y * view.s;
    }
    draw();
  }
  function renderSide() {
    const n = selected;
    const side = $('graph-side');
    if (!n) { side.innerHTML = '<div class="mind-empty">Cliquez sur un nœud.</div>'; return; }
    const st = n.status ? `<span class="gs-badge" style="--c:${STATUS_COLOR[n.status]}">${STATUS_FR[n.status]}</span>`
      : '<span class="gs-badge muted">NON ÉVALUÉ</span>';
    const rels = (adj.get(n.id) || []).map((e) => {
      const out = e.a === n.id, other = byId.get(out ? e.b : e.a);
      const verb = out ? REL_FR[e.rel] || e.rel : `← ${REL_FR[e.rel] || e.rel}`;
      return `<button class="gs-rel" data-id="${esc(other.id)}"><i>${esc(verb)}</i> ${esc(other.label)}</button>`;
    }).join('');
    side.innerHTML = `
      <div class="gs-kind" style="--c:${colorOf(n)}">${KIND_FR[n.kind] || n.kind}</div>
      <h4 class="gs-title">${esc(n.label)}</h4>
      <div class="gs-badges">${st}${n.tool ? `<span class="gs-badge tool">outil ${esc(n.tool)}</span>` : ''}</div>
      <p class="gs-summary">${esc(n.summary || '')}</p>
      ${(n.details || []).length ? `<ul class="gs-details">${n.details.map((d) => `<li>${esc(d)}</li>`).join('')}</ul>` : ''}
      ${n.graph && data.graphs[n.graph] ? `<button class="mini-btn gs-open" data-g="${esc(n.graph)}">OUVRIR LE GRAPHE « ${esc((data.graphs[n.graph].title || n.graph).split(' — ')[0])} »</button>` : ''}
      ${n.test ? `<div class="gs-label">TEST</div><code class="gs-code">${esc(n.test)}</code>` : ''}
      ${(n.files || []).length ? `<div class="gs-label">FICHIERS</div><div class="gs-files">${n.files.map((f) => `<code>${esc(f)}</code>`).join('')}</div>` : ''}
      ${rels ? `<div class="gs-label">LIENS</div><div class="gs-rels">${rels}</div>` : ''}`;
    for (const b of side.querySelectorAll('.gs-rel')) b.onclick = () => select(byId.get(b.dataset.id), true);
    const o = side.querySelector('.gs-open');
    if (o) o.onclick = () => load(o.dataset.g);
  }

  // ── recherche ──
  function search(q) {
    const words = fold(q).split(/\s+/).filter((w) => w.length > 1);
    matches = new Set(words.length ? nodes.filter((n) => {
      const t = fold([n.label, n.summary, n.tool, ...(n.details || []), ...(n.files || [])].join(' '));
      return words.every((w) => t.includes(w));
    }).map((n) => n.id) : []);
    $('graph-count').textContent = words.length ? `${matches.size} trouvé${matches.size > 1 ? 's' : ''}` : '';
    draw();
  }

  // ── interactions ──
  canvas.addEventListener('mousedown', (e) => {
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
  });
  window.addEventListener('mousemove', (e) => {
    if (!open) return;
    const r = canvas.getBoundingClientRect();
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.x = drag.vx + dx; view.y = drag.vy + dy; draw(); return;
    }
    if (e.target !== canvas) return;
    const n = pick(e.clientX - r.left, e.clientY - r.top);
    if (n !== hover) { hover = n; canvas.style.cursor = n ? 'pointer' : 'grab'; draw(); }
  });
  window.addEventListener('mouseup', (e) => {
    if (!drag) return;
    const moved = drag.moved; drag = null;
    if (moved || e.target !== canvas) return;
    const r = canvas.getBoundingClientRect();
    const n = pick(e.clientX - r.left, e.clientY - r.top);
    if (n) select(n);
  });
  canvas.addEventListener('dblclick', (e) => {
    const r = canvas.getBoundingClientRect();
    const n = pick(e.clientX - r.left, e.clientY - r.top);
    if (n && n.graph && data.graphs[n.graph]) load(n.graph);
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.exp(-e.deltaY * 0.0015);
    const s = Math.max(0.2, Math.min(3, view.s * k));
    view.x = mx - (mx - view.x) * (s / view.s); view.y = my - (my - view.y) * (s / view.s); view.s = s;
    draw();
  }, { passive: false });
  canvas.addEventListener('mouseleave', () => { if (hover) { hover = null; draw(); } });
  $('graph-q').addEventListener('input', (e) => search(e.target.value));
  $('graph-q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = nodes.find((n) => matches.has(n.id));
      if (first) select(first, true);
    }
  });
  $('graph-fit').onclick = () => fit();
  $('graph-close').onclick = () => hide();
  new ResizeObserver(() => { if (open) resize(); }).observe(canvas.parentElement);

  // la légende ne change pas
  $('graph-legend').innerHTML =
    Object.entries(STATUS_FR).map(([k, v]) => `<span><i class="ring" style="--c:${STATUS_COLOR[k]}"></i>${v}</span>`).join('')
    + '<span><i class="ring" style="--c:#6B7A93"></i>NON ÉVALUÉ</span><span><i class="ring dashed"></i>SOUS-GRAPHE</span>';

  return {
    show,
    close: hide,
    get isOpen() { return open; },
  };
}
