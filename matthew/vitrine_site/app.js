// Vitrine de Matthew — lecture seule. Lit data/vitrine.json (généré par matthew/vitrine.py,
// liste blanche vérifiée) et anime l'examen du jour dans une scène three.js.
// Tout le texte de la page est en HTML : la 3D n'est qu'un plus, et s'efface si WebGL manque
// ou si le visiteur demande moins de mouvement.

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (id) => document.getElementById(id);
const pct = (x) => (x == null ? "—" : Math.round(x * 100) + " %");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const frDate = (d) => new Date(d + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "short" });

let DATA = null;

async function main() {
  try {
    const r = await fetch("data/vitrine.json", { cache: "no-cache" });
    DATA = await r.json();
  } catch (e) {
    $("r-req").textContent = "Les données du jour ne sont pas encore publiées.";
    return;
  }
  fillStats();
  drawChart();
  fillDomains();
  fillExam();
  fillNights();
  const scene = await startScene().catch((e) => { console.warn("3D indisponible :", e); return null; });
  startReplay(scene);
}

// ── Chiffres ─────────────────────────────────────────────────────────────────
function fillStats() {
  const c = DATA.champion, s = c && c.score, cov = DATA.coverage;
  $("s-score").textContent = s && s.composite != null ? s.composite.toFixed(2) : "—";
  $("s-tool").textContent = pct(s && s.tool_name);
  $("s-tools").textContent = `${cov.tools_covered}/${cov.tools_total}`;
  $("s-ex").textContent = cov.examples.toLocaleString("fr-FR");
  $("s-nights").textContent = new Set(DATA.cycles.map((x) => x.date)).size || "—";
  $("gen").textContent = DATA.generated;
  $("base").textContent = c ? c.base : "—";
  $("tok").textContent = `${(DATA.tokens.matthew || 0).toLocaleString("fr-FR")} tokens générés par Matthew`;
}

function drawChart() {
  const svg = $("chart"), W = svg.clientWidth || 800, H = 280, P = { l: 40, r: 16, t: 16, b: 30 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const byDay = new Map();
  for (const h of DATA.history) if (h.score) byDay.set(h.date, h);          // la dernière de la journée
  const pts = [...byDay.values()];
  const x = (i) => P.l + (pts.length < 2 ? (W - P.l - P.r) / 2 : (i * (W - P.l - P.r)) / (pts.length - 1));
  const y = (v) => P.t + (1 - v) * (H - P.t - P.b);
  let g = "";
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    g += `<line class="axis" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l - 8}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(2)}</text>`;
  }
  const series = [["composite", "#5fe3ff", 3], ["tool_name", "#4dffb0", 2], ["tool_args", "#ffb84d", 2]];
  for (const [k, col, w] of series) {
    const p = pts.map((h, i) => [x(i), y(h.score[k] ?? 0)]);
    if (p.length > 1) g += `<polyline fill="none" stroke="${col}" stroke-width="${w}" stroke-linejoin="round" points="${p.map((q) => q.join(",")).join(" ")}" opacity="${k === "composite" ? 1 : 0.7}"/>`;
    pts.forEach((h, i) => {
      const filled = k === "composite" && h.promoted;
      g += `<circle cx="${x(i)}" cy="${y(h.score[k] ?? 0)}" r="${k === "composite" ? 5 : 3}" fill="${filled ? col : "#030810"}" stroke="${col}" stroke-width="2"><title>${frDate(h.date)} — ${k} ${pct(h.score[k])}</title></circle>`;
    });
  }
  pts.forEach((h, i) => (g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${frDate(h.date)}</text>`));
  if (pts.length === 1) g += `<text x="${W / 2}" y="${P.t + 14}" text-anchor="middle">Première nuit — la courbe se dessine nuit après nuit</text>`;
  svg.innerHTML = `<title id="chart-t">Courbe du score par nuit</title>` + g;
}

function fillDomains() {
  const box = $("domains");
  for (const dom of DATA.domains) {
    const tools = DATA.tools.filter((t) => t.domain === dom);
    if (!tools.length) continue;
    tools.sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1));
    const learned = tools.filter((t) => t.examples > 0).length;
    box.insertAdjacentHTML("beforeend", `<div class="domain"><h3>${esc(dom)} <small style="color:var(--muted);letter-spacing:0">${learned}/${tools.length}</small></h3>${tools
      .map((t) => {
        const todo = !t.examples;
        const v = todo ? "à venir" : t.accuracy == null ? "appris" : pct(t.accuracy);
        const w = todo ? 0 : Math.round((t.accuracy ?? 1) * 100);
        return `<div class="tool${todo ? " todo" : ""}"><span>${esc(t.label)}</span><span class="v">${v}</span><span class="bar"><i style="width:${w}%"></i></span></div>`;
      }).join("")}</div>`);
  }
}

function examDays() {
  const s = new Set();
  for (const sc of DATA.showcase) for (const d of Object.keys(sc.results)) s.add(d);
  return [...s].sort();
}

function fillExam() {
  const days = examDays().slice(-7), lbl = Object.fromEntries(DATA.tools.map((t) => [t.id, t.label]));
  let h = `<thead><tr><th scope="col">Demande (inventée pour l'examen)</th><th scope="col">Outil attendu</th>${days.map((d) => `<th scope="col" class="c">${frDate(d)}</th>`).join("")}</tr></thead><tbody>`;
  for (const s of DATA.showcase) {
    h += `<tr><td>${esc(s.request)}${s.heldout ? ' <span class="badge ghost">jamais vu</span>' : ""}</td><td>${esc(lbl[s.target] || s.target)}</td>${days
      .map((d) => {
        const r = s.results[d];
        if (!r) return `<td class="c">·</td>`;
        return r.ok ? `<td class="c ok" title="${esc(lbl[r.chosen] || r.chosen)}">✓</td>` : `<td class="c ko" title="choisi : ${esc(lbl[r.chosen] || r.chosen || "aucun outil")}">✗</td>`;
      }).join("")}</tr>`;
  }
  $("exam").insertAdjacentHTML("beforeend", h + "</tbody>");
}

function fillNights() {
  const ol = $("nights");
  const rows = [...DATA.cycles].reverse().slice(0, 14);
  if (!rows.length) ol.innerHTML = "<li>Pas encore de nuit d'apprentissage.</li>";
  for (const c of rows) {
    const win = /champion/.test(c.outcome);
    ol.insertAdjacentHTML("beforeend", `<li><span>${frDate(c.date)}</span><span class="o${win ? " win" : ""}">${esc(c.outcome)}</span></li>`);
  }
}

// ── Scène 3D ─────────────────────────────────────────────────────────────────
async function startScene() {
  const host = $("scene");
  const probe = document.createElement("canvas");
  if (!(probe.getContext("webgl2") || probe.getContext("webgl"))) throw new Error("pas de WebGL");
  const THREE = await import("three");
  const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
  const { CSS2DRenderer, CSS2DObject } = await import("three/addons/renderers/CSS2DRenderer.js");
  const small = innerWidth < 860;
  let composer = null;

  const renderer = new THREE.WebGLRenderer({ antialias: !small, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, small ? 1.5 : 2));
  host.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.style.cssText = "position:absolute;inset:0;pointer-events:none";
  host.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x030810, 0.035);
  const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 200);
  camera.position.set(small ? 0 : 6, small ? 9 : 7.5, small ? 19 : 16);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.enablePan = false;
  controls.minDistance = 9; controls.maxDistance = 28; controls.maxPolarAngle = Math.PI * 0.48;
  controls.autoRotate = !REDUCED; controls.autoRotateSpeed = 0.35;
  controls.target.set(small ? 0 : 2.5, 0, 0);

  const CYAN = new THREE.Color(0x5fe3ff), GREEN = new THREE.Color(0x4dffb0), RED = new THREE.Color(0xff5a6e), AMBER = new THREE.Color(0xffb84d);

  // Noyau : sphère à effet Fresnel, coquille filaire, nuage de points.
  const fresnel = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uColor: { value: CYAN.clone() }, uPulse: { value: 0 } },
    vertexShader: `varying vec3 vN; varying vec3 vV; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }`,
    fragmentShader: `uniform float uTime; uniform vec3 uColor; uniform float uPulse; varying vec3 vN; varying vec3 vV;
      void main(){ float f = pow(1.-max(dot(vN,vV),0.), 2.2); float scan = .55+.45*sin(gl_FragCoord.y*.9+uTime*4.);
        gl_FragColor = vec4(uColor*(f*1.6+.08+uPulse*.6)*mix(.8,1.,scan), f*.95+.12+uPulse*.3); }`,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.25, 5), fresnel);
  scene.add(core);
  const shell = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.85, 1)),
    new THREE.LineBasicMaterial({ color: 0x5fe3ff, transparent: true, opacity: 0.35 }));
  scene.add(shell);
  const coreLbl = document.createElement("div"); coreLbl.className = "lbl-domain"; coreLbl.textContent = "Matthew";
  const coreObj = new CSS2DObject(coreLbl); coreObj.position.set(0, 2.4, 0); scene.add(coreObj);

  // Sol : anneaux et rayons, comme une table holographique.
  const grid = new THREE.PolarGridHelper(11, 24, 8, 96, 0x1d5a73, 0x0e2f40);
  grid.position.y = -1.6; grid.material.transparent = true; grid.material.opacity = 0.55; scene.add(grid);

  // Étoiles.
  const starGeo = new THREE.BufferGeometry(), N = small ? 600 : 1400, pos = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) { const r = 40 + Math.random() * 50, t = Math.random() * Math.PI * 2, p = Math.acos(2 * Math.random() - 1);
    pos.set([r * Math.sin(p) * Math.cos(t), r * Math.cos(p) * 0.6, r * Math.sin(p) * Math.sin(t)], i * 3); }
  starGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0x7fbfd6, size: 0.12, transparent: true, opacity: 0.6 })));

  // Stations : un secteur par domaine, un octaèdre par outil, couleur = précision.
  const stations = new Map(), doms = DATA.domains.filter((d) => DATA.tools.some((t) => t.domain === d));
  const sector = (Math.PI * 2) / doms.length;
  doms.forEach((dom, di) => {
    const tools = DATA.tools.filter((t) => t.domain === dom);
    const a0 = di * sector + 0.12, a1 = (di + 1) * sector - 0.12;
    const mid = (a0 + a1) / 2;
    const dl = document.createElement("div"); dl.className = "lbl-domain"; dl.textContent = dom;
    const dObj = new CSS2DObject(dl); dObj.position.set(Math.cos(mid) * 10.4, -1.2, Math.sin(mid) * 10.4); scene.add(dObj);
    const arc = new THREE.EllipseCurve(0, 0, 9.4, 9.4, a0, a1).getPoints(40).map((p) => new THREE.Vector3(p.x, -1.55, p.y));
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(arc), new THREE.LineBasicMaterial({ color: 0x5fe3ff, transparent: true, opacity: 0.35 })));
    tools.forEach((t, i) => {
      const a = a0 + ((i + 0.5) * (a1 - a0)) / tools.length, row = i % 2;
      const r = 6.2 + row * 1.6, yy = -0.6 + Math.sin(i * 1.7 + di) * 0.5;
      const learned = t.examples > 0;
      const col = !learned ? new THREE.Color(0x2a4250) : t.accuracy == null ? CYAN.clone() : AMBER.clone().lerp(GREEN, t.accuracy);
      const mesh = new THREE.Mesh(new THREE.OctahedronGeometry(learned ? 0.26 : 0.18, 0),
        learned ? new THREE.MeshBasicMaterial({ color: col }) : new THREE.MeshBasicMaterial({ color: col, wireframe: true }));
      mesh.position.set(Math.cos(a) * r, yy, Math.sin(a) * r);
      scene.add(mesh);
      const stem = new THREE.Line(new THREE.BufferGeometry().setFromPoints([mesh.position.clone(), new THREE.Vector3(mesh.position.x, -1.6, mesh.position.z)]),
        new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: learned ? 0.35 : 0.15 }));
      scene.add(stem);
      const el = document.createElement("div"); el.className = "lbl-tool" + (learned ? "" : " off"); el.textContent = t.label;
      const lo = new CSS2DObject(el); lo.position.copy(mesh.position).add(new THREE.Vector3(0, 0.45, 0)); scene.add(lo);
      stations.set(t.id, { mesh, el, base: col.clone(), spin: 0.4 + Math.random() * 0.6 });
    });
  });

  // Halo (bloom) sur les grands écrans seulement.
  if (!small && !REDUCED) {
    try {
      const { EffectComposer } = await import("three/addons/postprocessing/EffectComposer.js");
      const { RenderPass } = await import("three/addons/postprocessing/RenderPass.js");
      const { UnrealBloomPass } = await import("three/addons/postprocessing/UnrealBloomPass.js");
      const { OutputPass } = await import("three/addons/postprocessing/OutputPass.js");
      composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));
      composer.addPass(new UnrealBloomPass(new THREE.Vector2(256, 256), 0.85, 0.55, 0.12));
      composer.addPass(new OutputPass());
    } catch (e) { composer = null; }
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    if (composer) composer.setSize(w, h);
  }
  new ResizeObserver(resize).observe(host); resize();

  // Faisceaux : un point lumineux voyage du noyau à la station, sur un arc.
  const beams = [];
  function fire(targetId, ok, expectedId) {
    const st = stations.get(targetId);
    const end = st ? st.mesh.position.clone() : new THREE.Vector3(0, 4, 0);
    const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(0, 0.4, 0), end.clone().multiplyScalar(0.5).add(new THREE.Vector3(0, 3.2, 0)), end);
    const color = ok ? GREEN : RED;
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(48)),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0 }));
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 12), new THREE.MeshBasicMaterial({ color }));
    scene.add(line, dot);
    beams.push({ curve, line, dot, t: 0, st, ok, color });
    if (!ok && expectedId && stations.get(expectedId)) {            // l'outil attendu, en pointillé
      const ex = stations.get(expectedId).mesh.position;
      const dash = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0.4, 0), ex.clone()]),
        new THREE.LineDashedMaterial({ color: 0xffb84d, dashSize: 0.3, gapSize: 0.2, transparent: true, opacity: 0.6 }));
      dash.computeLineDistances(); scene.add(dash);
      setTimeout(() => scene.remove(dash), 3200);
    }
    fresnel.uniforms.uPulse.value = 1;
  }
  function focus(id) {
    for (const [k, s] of stations) s.el.classList.toggle("on", k === id);
  }

  const clock = new THREE.Clock();
  (function loop() {
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    fresnel.uniforms.uTime.value = t;
    fresnel.uniforms.uPulse.value *= 0.94;
    if (!REDUCED) { shell.rotation.y += dt * 0.15; shell.rotation.x += dt * 0.05; core.scale.setScalar(1 + Math.sin(t * 1.6) * 0.02); }
    for (const s of stations.values()) { s.mesh.rotation.y += dt * s.spin; s.mesh.material.color.lerp(s.base, 0.04); s.mesh.scale.lerp(new THREE.Vector3(1, 1, 1), 0.08); }
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i];
      b.t += dt * (REDUCED ? 3 : 0.9);
      const k = Math.min(b.t, 1);
      b.dot.position.copy(b.curve.getPoint(k));
      b.line.material.opacity = Math.min(0.8, b.t * 1.5) * (b.t > 1 ? Math.max(0, 2.2 - b.t) : 1);
      if (b.t >= 1 && !b.hit) { b.hit = true; scene.remove(b.dot); if (b.st) { b.st.mesh.material.color.copy(b.color); b.st.mesh.scale.setScalar(2.2); } }
      if (b.t > 2.2) { scene.remove(b.line); beams.splice(i, 1); }
    }
    controls.update();
    composer ? composer.render() : renderer.render(scene, camera);
    labels.render(scene, camera);
    requestAnimationFrame(loop);
  })();

  return { fire, focus };
}

// ── Rejeu de l'examen ────────────────────────────────────────────────────────
function startReplay(scene) {
  const days = examDays(), lbl = Object.fromEntries(DATA.tools.map((t) => [t.id, t.label]));
  if (!days.length || !DATA.showcase.length) { $("r-req").textContent = "Premier examen ce soir."; return; }
  const today = days[days.length - 1];
  const choices = [[today, "Aujourd'hui"]];
  const weekAgo = new Date(new Date(today + "T12:00:00") - 7 * 864e5).toISOString().slice(0, 10);
  const past = days.filter((d) => d <= weekAgo).pop() || (days.length > 1 ? days[0] : null);
  if (past && past !== today) choices.push([past, past <= weekAgo ? "J-7" : frDate(past)]);
  let day = today, idx = 0, paused = false;
  const box = $("days");
  for (const [d, name] of choices) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = name; b.dataset.day = d;
    b.setAttribute("aria-pressed", String(d === day));
    b.onclick = () => { day = d; idx = 0; for (const x of box.children) x.setAttribute("aria-pressed", String(x.dataset.day === d)); step(); };
    box.appendChild(b);
  }
  $("r-pause").onclick = (e) => { paused = !paused; e.target.textContent = paused ? "Lecture" : "Pause"; e.target.setAttribute("aria-pressed", String(paused)); };
  const list = () => DATA.showcase.filter((s) => s.results[day]);
  function step() {
    const L = list(); if (!L.length) return;
    const s = L[idx % L.length], r = s.results[day];
    $("r-req").textContent = s.request;
    $("r-chosen").textContent = r.chosen ? lbl[r.chosen] || r.chosen : "aucun outil";
    const b = $("r-badge"); b.textContent = r.ok ? "réussi" : "raté"; b.className = "badge" + (r.ok ? "" : " ko");
    $("r-seen").textContent = s.heldout ? "jamais vu" : "vu à l'entraînement";
    const ok = L.filter((x) => x.results[day].ok).length;
    $("r-count").textContent = `${(idx % L.length) + 1}/${L.length} · ${ok} réussis le ${frDate(day)}${r.ms ? ` · ${(r.ms / 1000).toFixed(1)} s` : ""}`;
    if (scene) { scene.focus(r.chosen); scene.fire(r.chosen, r.ok, s.target); }
  }
  step();
  setInterval(() => { if (!paused && !document.hidden) { idx++; step(); } }, REDUCED ? 7000 : 4200);
}

main();
