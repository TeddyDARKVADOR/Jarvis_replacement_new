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
  fillChanges();
  fillLive();
  $("domains").addEventListener("click", (e) => { const t = e.target.closest("[data-tool]"); if (t) openTool(t.dataset.tool); });
  $("domains").addEventListener("keydown", (e) => { const t = e.target.closest("[data-tool]"); if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openTool(t.dataset.tool); } });
  const closeTool = () => { $("drawer").hidden = true; history.replaceState(null, "", location.pathname); };
  $("dr-close").onclick = closeTool;
  addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeTool(); });
  if (location.hash.startsWith("#outil-")) openTool(decodeURIComponent(location.hash.slice(7)));
  const scene = await startScene().catch((e) => { console.warn("3D indisponible :", e); return null; });
  if (scene) scene.onPick(openTool);
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
  const month = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  $("s-changes").textContent = (DATA.changes || []).filter((c) => c.date >= month).length || "—";
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
        return `<div class="tool${todo ? " todo" : ""}" tabindex="0" role="button" data-tool="${esc(t.id)}"><span>${esc(t.label)}</span><span class="v">${v}</span><span class="bar"><i style="width:${w}%"></i></span></div>`;
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

function fillChanges() {
  const all = DATA.changes || [], chips = $("chips"), ol = $("changes");
  const areas = ["Tout", ...new Set(all.map((c) => c.area))];
  let area = "Tout", shown = 25;
  function render() {
    const rows = all.filter((c) => area === "Tout" || c.area === area);
    ol.innerHTML = rows.slice(0, shown).map((c) => `<li data-area="${esc(c.area)}"><span class="d">${frDate(c.date)}</span><span>${esc(c.title)}</span><span class="a">${esc(c.area)}</span></li>`).join("")
      + (rows.length > shown ? `<li class="more"><button type="button" id="more">Voir les ${rows.length - shown} autres</button></li>` : "");
    const more = $("more"); if (more) more.onclick = () => { shown = rows.length; render(); };
  }
  for (const a of areas) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = a; b.setAttribute("aria-pressed", String(a === area));
    b.onclick = () => { area = a; shown = 25; for (const x of chips.children) x.setAttribute("aria-pressed", String(x.textContent === a)); render(); };
    chips.appendChild(b);
  }
  if (!all.length) ol.innerHTML = "<li>Les premières améliorations arrivent.</li>"; else render();
}

function spark(svg, values, w, h, color) {
  const v = values.filter((x) => x != null);
  if (!v.length) { svg.innerHTML = ""; return; }
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => [v.length < 2 ? w / 2 : 4 + (i * (w - 8)) / (v.length - 1), h - 6 - ((x - lo) / span) * (h - 12)]);
  svg.innerHTML = (pts.length > 1 ? `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts.map((q) => q.join(",")).join(" ")}"/>` : "")
    + pts.map((q) => `<circle cx="${q[0]}" cy="${q[1]}" r="2.5" fill="${color}"/>`).join("");
}

function fillLive() {
  const L = DATA.live;
  if (!L) return;
  $("livebar").hidden = false;
  const ep = L.epoch != null && L.epochs ? ` — époque ${Math.min(Math.ceil(L.epoch), L.epochs)}/${L.epochs}` : "";
  $("live-text").textContent = (L.phase === "export" ? "Entraînement terminé, empaquetage" : "Entraînement en cours") + ep;
  spark($("live-spark"), L.losses || [], 120, 28, "#4dffb0");
}

function openTool(id) {
  const t = DATA.tools.find((x) => x.id === id);
  if (!t) return;
  $("dr-domain").textContent = t.domain;
  $("dr-title").textContent = t.label;
  $("dr-acc").textContent = !t.examples ? "à venir" : t.accuracy == null ? "appris" : pct(t.accuracy);
  $("dr-acc").className = "big" + (!t.examples || t.accuracy == null ? " na" : t.accuracy < 0.5 ? " low" : t.accuracy < 0.75 ? " mid" : "");
  const hist = DATA.history.filter((h) => h.per_tool && h.per_tool[id] != null);
  spark($("dr-spark"), hist.map((h) => h.per_tool[id]), 300, 70, "#5fe3ff");
  $("dr-meta").textContent = `${t.examples} exemples vérifiés · ${hist.length} examen${hist.length > 1 ? "s" : ""} noté${hist.length > 1 ? "s" : ""}`
    + (t.examples ? "" : " · il l'apprendra dans les prochaines nuits");
  const cases = DATA.showcase.filter((x) => x.target === id);
  $("dr-cases").innerHTML = cases.length ? cases.map((c) => `<li>« ${esc(c.request)} »${c.heldout ? ' <span class="badge ghost">jamais vu</span>' : ""}<div class="days">${
    Object.entries(c.results).sort().slice(-7).map(([d, r]) => `<span class="d${r.ok ? "" : " ko"}">${frDate(d)} ${r.ok ? "✓" : "✗"}</span>`).join(" ")}</div></li>`).join("")
    : "<li class='muted'>Pas encore de scénario d'examen pour cet outil.</li>";
  $("drawer").hidden = false;
  $("dr-close").focus();
  history.replaceState(null, "", `#outil-${id}`);                // une adresse par outil, partageable
}

// ── Scène 3D ─────────────────────────────────────────────────────────────────
async function startScene() {
  const host = $("scene");
  const probe = document.createElement("canvas");
  if (!(probe.getContext("webgl2") || probe.getContext("webgl"))) throw new Error("pas de WebGL");
  const THREE = await import("three");
  const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
  const { CSS2DRenderer, CSS2DObject } = await import("three/addons/renderers/CSS2DRenderer.js");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const { RGBELoader } = await import("three/addons/loaders/RGBELoader.js");
  const small = innerWidth < 860;
  let composer = null;

  const renderer = new THREE.WebGLRenderer({ antialias: !small, alpha: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, small ? 1.5 : 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.style.cssText = "position:absolute;inset:0;pointer-events:none";
  host.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x030810);
  scene.fog = new THREE.Fog(0x050b16, 34, 110);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 400);
  camera.position.set(small ? 0 : 10, small ? 18 : 15, small ? 32 : 27);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.enablePan = false;
  controls.minDistance = 12; controls.maxDistance = 46; controls.maxPolarAngle = Math.PI * 0.47;
  controls.autoRotate = !REDUCED; controls.autoRotateSpeed = 0.3;
  controls.target.set(small ? 0 : 3, 2.2, 0);

  const CYAN = new THREE.Color(0x5fe3ff), GREEN = new THREE.Color(0x4dffb0), RED = new THREE.Color(0xff5a6e), AMBER = new THREE.Color(0xffb84d);

  // Ciel étoilé réel (Poly Haven, CC0) : fond ET éclairage d'ambiance.
  const pmrem = new THREE.PMREMGenerator(renderer);
  new RGBELoader().load("assets/sky/night_1k.hdr", (hdr) => {
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    scene.environment = pmrem.fromEquirectangular(hdr).texture;
    scene.background = hdr;
    // Luminance médiane mesurée 0,48 (un crépuscule) : ramenée à ~0,02 pour une nuit
    // profonde ; les étoiles (p99 ≈ 6) restent visibles.
    scene.backgroundIntensity = 0.045;
    scene.environmentIntensity = 0.3;
  });
  scene.add(new THREE.HemisphereLight(0x6fa8ff, 0x0b1018, 0.35));
  const moon = new THREE.DirectionalLight(0xbcd8ff, 1.4);
  moon.position.set(-14, 22, 10);
  scene.add(moon);
  const coreLight = new THREE.PointLight(0x5fe3ff, 40, 26, 1.6);
  coreLight.position.set(0, 4.2, 0);
  scene.add(coreLight);

  // Sol lunaire.
  const ground = new THREE.Mesh(new THREE.CircleGeometry(220, 96),
    new THREE.MeshStandardMaterial({ color: 0x1a222c, roughness: 0.96, metalness: 0.05 }));
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const grid = new THREE.PolarGridHelper(13, 24, 6, 96, 0x2a7d9c, 0x123446);
  grid.position.y = 0.02; grid.material.transparent = true; grid.material.opacity = 0.45; scene.add(grid);

  // Modèles Kenney (Space Kit, CC0) : chargés une fois, clonés ensuite.
  const loader = new GLTFLoader();
  const names = ["platform_large", "hangar_largeA", "hangar_roundGlass", "hangar_smallA", "satelliteDish_large",
    "satelliteDish", "machine_wireless", "machine_generatorLarge", "machine_generator", "desk_computer",
    "structure_detailed", "rocket_baseA", "rocket_fuelA", "rocket_topA", "rover", "craft_speederA", "crater",
    "craterLarge", "rock_largeA", "rocks_smallA", "rock_crystals", "rock_crystalsLargeA", "machine_barrel",
    "supports_low", "astronautA", "meteor"];
  const M = {};
  await Promise.all(names.map((n) => loader.loadAsync(`assets/models/${n}.glb`).then((g) => (M[n] = g.scene)).catch(() => null)));
  // Les modèles Kenney ont leur origine dans un coin (ce sont des tuiles) : chacun est
  // recentré sur le milieu de sa base avant d'être posé, tourné et mis à l'échelle.
  const box = new THREE.Box3(), mid = new THREE.Vector3();
  const place = (name, x, z, s = 1, rotY = 0, y = 0) => {
    if (!M[name]) return null;
    const inner = M[name].clone(true);
    box.setFromObject(inner); box.getCenter(mid);
    inner.position.set(-mid.x, -box.min.y, -mid.z);
    const o = new THREE.Group(); o.add(inner);
    o.scale.setScalar(s); o.position.set(x, y, z); o.rotation.y = rotY;
    scene.add(o);
    return o;
  };
  const dim = (obj, f) => obj && obj.traverse((m) => {
    if (m.isMesh) { m.material = m.material.clone(); m.material.color.multiplyScalar(f); }
  });

  // Paysage : cratères, rochers, cristaux, en dehors du cercle de la base.
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 26; i++) {
    const a = rnd() * Math.PI * 2, r = 16 + rnd() * 22;
    const kind = ["crater", "craterLarge", "rock_largeA", "rocks_smallA", "rock_crystals", "rock_crystalsLargeA", "meteor"][i % 7];
    place(kind, Math.cos(a) * r, Math.sin(a) * r, 1.6 + rnd() * 2.4, rnd() * 6.28);
  }

  // Le centre : la plateforme, Matthew au-dessus.
  place("platform_large", 0, 0, 3.2);
  place("astronautA", 3.4, 2.6, 1.4, -0.6, 0.2);
  const fresnel = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uColor: { value: CYAN.clone() }, uPulse: { value: 0 } },
    vertexShader: `varying vec3 vN; varying vec3 vV; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }`,
    fragmentShader: `uniform float uTime; uniform vec3 uColor; uniform float uPulse; varying vec3 vN; varying vec3 vV;
      void main(){ float f = pow(1.-max(dot(vN,vV),0.), 2.2); float scan = .55+.45*sin(gl_FragCoord.y*.9+uTime*4.);
        gl_FragColor = vec4(uColor*(f*1.8+.12+uPulse*.6)*mix(.8,1.,scan), f*.95+.18+uPulse*.3); }`,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const CORE_Y = 4.2;
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.3, 5), fresnel);
  core.position.y = CORE_Y; scene.add(core);
  const shell = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.95, 1)),
    new THREE.LineBasicMaterial({ color: 0x5fe3ff, transparent: true, opacity: 0.4 }));
  shell.position.y = CORE_Y; scene.add(shell);
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.5, CORE_Y, 24, 1, true),
    new THREE.MeshBasicMaterial({ color: 0x5fe3ff, transparent: true, opacity: 0.18, blending: THREE.AdditiveBlending, depthWrite: false }));
  beam.position.y = CORE_Y / 2; scene.add(beam);
  const coreLbl = document.createElement("div"); coreLbl.className = "lbl-domain"; coreLbl.textContent = "Matthew";
  const coreObj = new CSS2DObject(coreLbl); coreObj.position.set(0, CORE_Y + 2.6, 0); scene.add(coreObj);

  // Un bâtiment par domaine, un module par outil, une balise par module.
  const BUILDING = { "Système": ["hangar_largeA", 1.5], "Bureau": ["hangar_smallA", 1.8], "Infos": ["satelliteDish_large", 2.0],
    "Communication": ["machine_wireless", 2.2], "Mémoire": ["hangar_roundGlass", 1.9], "Code": ["rocket", 1.8],
    "Noyau": ["machine_generatorLarge", 2.0], "Autres": ["structure_detailed", 1.4] };
  const POD = { "Système": "machine_generator", "Bureau": "desk_computer", "Infos": "satelliteDish", "Communication": "machine_wireless",
    "Mémoire": "machine_barrel", "Code": "structure_detailed", "Noyau": "supports_low", "Autres": "rocks_smallA" };
  const spinners = [];
  const stations = new Map(), doms = DATA.domains.filter((d) => DATA.tools.some((t) => t.domain === d));
  const sector = (Math.PI * 2) / doms.length;
  const beaconGeo = new THREE.SphereGeometry(0.2, 16, 16);
  const hits = [];
  doms.forEach((dom, di) => {
    const tools = DATA.tools.filter((t) => t.domain === dom);
    const a0 = di * sector + 0.14, a1 = (di + 1) * sector - 0.14, mid = (a0 + a1) / 2;
    const bx = Math.cos(mid) * 12.2, bz = Math.sin(mid) * 12.2, face = -mid + Math.PI / 2;
    const [bName, bScale] = BUILDING[dom] || BUILDING["Autres"];
    if (bName === "rocket") {
      place("rocket_baseA", bx, bz, bScale); place("rocket_fuelA", bx, bz, bScale, 0, bScale); place("rocket_topA", bx, bz, bScale, 0, bScale * 2);
    } else {
      const b = place(bName, bx, bz, bScale, face);
      if (b && /satellite|wireless/.test(bName)) spinners.push(b);
    }
    const dl = document.createElement("div"); dl.className = "lbl-domain"; dl.textContent = dom;
    const dObj = new CSS2DObject(dl); dObj.position.set(bx, bName === "rocket" ? 7.4 : 4.6, bz); scene.add(dObj);
    tools.forEach((t, i) => {
      const a = a0 + ((i + 0.5) * (a1 - a0)) / tools.length, r = 6.6 + (i % 2) * 2.2;
      const x = Math.cos(a) * r, z = Math.sin(a) * r;
      const learned = t.examples > 0;
      const pod = place(POD[dom] || "rocks_smallA", x, z, 1.15, -a + Math.PI / 2);
      if (!learned) dim(pod, 0.3);
      const col = !learned ? new THREE.Color(0x33475a) : t.accuracy == null ? CYAN.clone() : AMBER.clone().lerp(GREEN, t.accuracy);
      const beacon = new THREE.Mesh(beaconGeo, new THREE.MeshBasicMaterial({ color: col.clone().multiplyScalar(learned ? 2.2 : 0.8) }));
      beacon.position.set(x, 2.1, z);
      scene.add(beacon);
      const el = document.createElement("div"); el.className = "lbl-tool" + (learned ? "" : " off"); el.textContent = t.label;
      const lo = new CSS2DObject(el); lo.position.set(x, 2.65, z); scene.add(lo);
      const hit = new THREE.Mesh(new THREE.SphereGeometry(0.75, 8, 8), new THREE.MeshBasicMaterial({ visible: false }));
      hit.position.copy(beacon.position); hit.userData.tool = t.id; scene.add(hit); hits.push(hit);
      stations.set(t.id, { mesh: beacon, el, base: beacon.material.color.clone() });
    });
  });

  // La vie de la base : un rover qui patrouille, un vaisseau en orbite.
  const rover = place("rover", 15, 0, 1.6);
  const ship = place("craft_speederA", 0, 20, 1.8, 0, 9);

  // Halo (bloom) sur les grands écrans : seules les lumières vives brillent.
  if (!small && !REDUCED) {
    try {
      const { EffectComposer } = await import("three/addons/postprocessing/EffectComposer.js");
      const { RenderPass } = await import("three/addons/postprocessing/RenderPass.js");
      const { UnrealBloomPass } = await import("three/addons/postprocessing/UnrealBloomPass.js");
      const { OutputPass } = await import("three/addons/postprocessing/OutputPass.js");
      composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));
      composer.addPass(new UnrealBloomPass(new THREE.Vector2(256, 256), 0.8, 0.45, 0.9));
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

  // Faisceaux : un point lumineux voyage du noyau à la balise, sur un arc.
  const beams = [];
  const ORIGIN = new THREE.Vector3(0, CORE_Y, 0);
  function fire(targetId, ok, expectedId) {
    const st = stations.get(targetId);
    const end = st ? st.mesh.position.clone() : new THREE.Vector3(0, CORE_Y + 6, 0);
    const curve = new THREE.QuadraticBezierCurve3(ORIGIN.clone(), ORIGIN.clone().lerp(end, 0.5).add(new THREE.Vector3(0, 4, 0)), end);
    const color = ok ? GREEN : RED;
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(48)),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0 }));
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.16, 12, 12), new THREE.MeshBasicMaterial({ color: color.clone().multiplyScalar(3) }));
    scene.add(line, dot);
    beams.push({ curve, line, dot, t: 0, st, ok, color });
    if (!ok && expectedId && stations.get(expectedId)) {             // l'outil attendu, en pointillé
      const ex = stations.get(expectedId).mesh.position;
      const dash = new THREE.Line(new THREE.BufferGeometry().setFromPoints([ORIGIN.clone(), ex.clone()]),
        new THREE.LineDashedMaterial({ color: 0xffb84d, dashSize: 0.3, gapSize: 0.2, transparent: true, opacity: 0.7 }));
      dash.computeLineDistances(); scene.add(dash);
      setTimeout(() => scene.remove(dash), 3200);
    }
    fresnel.uniforms.uPulse.value = 1;
  }
  function focus(id) {
    for (const [k, s] of stations) s.el.classList.toggle("on", k === id);
  }

  // Cliquer une balise ouvre la fiche de l'outil (un glissement de caméra n'est pas un clic).
  let pickCb = null, down = null;
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  renderer.domElement.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6 || !pickCb) return;
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const got = ray.intersectObjects(hits, false)[0];
    if (got) pickCb(got.object.userData.tool);
  });
  const training = !!DATA.live;
  const clock = new THREE.Clock(), ONE = new THREE.Vector3(1, 1, 1);
  (function loop() {
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    fresnel.uniforms.uTime.value = t;
    fresnel.uniforms.uPulse.value *= 0.94;
    if (training) fresnel.uniforms.uPulse.value = Math.max(fresnel.uniforms.uPulse.value, 0.35 + 0.35 * Math.sin(t * 6));
    coreLight.intensity = 40 + fresnel.uniforms.uPulse.value * 60;
    if (!REDUCED) {
      shell.rotation.y += dt * 0.15; shell.rotation.x += dt * 0.05;
      core.position.y = CORE_Y + Math.sin(t * 1.2) * 0.12;
      for (const s of spinners) s.rotation.y += dt * 0.25;
      if (rover) { const a = t * 0.08; rover.position.set(Math.cos(a) * 15.5, 0, Math.sin(a) * 15.5); rover.rotation.y = -a; }
      if (ship) { const a = -t * 0.15; ship.position.set(Math.cos(a) * 21, 9 + Math.sin(t * 0.7) * 0.8, Math.sin(a) * 21); ship.rotation.y = -a + Math.PI; }
    }
    for (const s of stations.values()) { s.mesh.material.color.lerp(s.base, 0.04); s.mesh.scale.lerp(ONE, 0.08); }
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i];
      b.t += dt * (REDUCED ? 3 : 0.9);
      const k = Math.min(b.t, 1);
      b.dot.position.copy(b.curve.getPoint(k));
      b.line.material.opacity = Math.min(0.9, b.t * 1.5) * (b.t > 1 ? Math.max(0, 2.2 - b.t) : 1);
      if (b.t >= 1 && !b.hit) {
        b.hit = true; scene.remove(b.dot);
        if (b.st) { b.st.mesh.material.color.copy(b.color).multiplyScalar(4); b.st.mesh.scale.setScalar(2.4); }
      }
      if (b.t > 2.2) { scene.remove(b.line); beams.splice(i, 1); }
    }
    controls.update();
    composer ? composer.render() : renderer.render(scene, camera);
    labels.render(scene, camera);
    requestAnimationFrame(loop);
  })();

  return { fire, focus, onPick: (cb) => (pickCb = cb) };
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
