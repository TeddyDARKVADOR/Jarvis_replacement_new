/**
 * reactor.js — the core. A living, audio-reactive hologram in three.js.
 *
 * Layers, from the inside out:
 *   heart      a small white-hot sphere and its glow sprite
 *   plasma     an icosphere displaced by 3D simplex noise, lit by a fresnel
 *              rim with holographic scan bands — the part that "breathes" with
 *              the voice (SPEAKING) or the microphone (LISTENING)
 *   lattice    a slowly counter-rotating wireframe icosahedron
 *   shell      ~2 400 points on a Fibonacci sphere, pushed outward by the
 *              same noise; they swirl when JARVIS is thinking
 *   orbits     three tilted hairline rings with a satellite each
 *
 * No post-processing pass: the glow is additive blending and sprites, which
 * costs a fraction of a bloom pass and looks the same at this scale.
 *
 * Everything reads `V` (the shared visual state main.js maintains) once per
 * frame. Nothing here knows about the network.
 */

const NOISE = /* glsl */`
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.0-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;vec4 s1=floor(b1)*2.0+1.0;vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const PLASMA_VERT = /* glsl */`
uniform float uTime; uniform float uAmp; uniform float uFreq; uniform float uSpeed;
varying vec3 vNormal; varying vec3 vView; varying float vNoise; varying vec3 vPos;
${NOISE}
void main(){
  float n = snoise(normal * uFreq + vec3(uTime * uSpeed));
  float n2 = snoise(normal * uFreq * 2.6 - vec3(uTime * uSpeed * 1.7)) * .22;
  float d = (n + n2) * uAmp;
  vNoise = n;
  vec3 p = position + normal * d;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vView = -mv.xyz;
  vNormal = normalize(normalMatrix * normal);
  vPos = p;
  gl_Position = projectionMatrix * mv;
}`;

const PLASMA_FRAG = /* glsl */`
uniform vec3 uColor; uniform float uTime; uniform float uGlow;
varying vec3 vNormal; varying vec3 vView; varying float vNoise; varying vec3 vPos;
void main(){
  float f = 1.0 - max(dot(normalize(vNormal), normalize(vView)), 0.0);
  float rim = pow(f, 2.2);
  float bands = smoothstep(.55, 1.0, sin(vPos.y * 38.0 - uTime * 3.0) * .5 + .5) * .35;
  float veins = smoothstep(.35, .9, vNoise) * .5;
  vec3 col = uColor * (0.10 + rim * 1.15 + bands * rim + veins * .35) + vec3(1.0) * pow(rim, 6.0) * .18;
  float a = clamp(.10 + rim * .75 + veins * .18, 0.0, 1.0) * uGlow;
  gl_FragColor = vec4(col * uGlow, a);
}`;

const SHELL_VERT = /* glsl */`
uniform float uTime; uniform float uAmp; uniform float uSize; uniform float uPixel;
attribute float aSeed;
varying float vAlpha;
${NOISE}
void main(){
  vec3 dir = normalize(position);
  float n = snoise(dir * 1.6 + vec3(uTime * .35));
  float r = length(position) * (1.0 + n * uAmp * .9) + sin(uTime * 2.0 + aSeed * 30.0) * .015;
  vec4 mv = modelViewMatrix * vec4(dir * r, 1.0);
  gl_Position = projectionMatrix * mv;
  float tw = .55 + .45 * sin(uTime * (1.5 + aSeed * 3.0) + aSeed * 60.0);
  vAlpha = tw * (.45 + max(n, 0.0) * 1.1);
  gl_PointSize = uSize * uPixel * (0.6 + aSeed * .8) * (6.0 / -mv.z);
}`;

const SHELL_FRAG = /* glsl */`
uniform vec3 uColor; uniform float uGlow;
varying float vAlpha;
void main(){
  vec2 c = gl_PointCoord - .5;
  float d = length(c);
  if (d > .5) discard;
  float a = smoothstep(.5, 0.0, d);
  gl_FragColor = vec4(uColor * 1.4, a * vAlpha * uGlow);
}`;

function glowTexture(THREE) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(.18, 'rgba(255,255,255,.55)');
  grd.addColorStop(.45, 'rgba(255,255,255,.12)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export async function createReactor(canvas, V, Q) {
  const THREE = await import('../vendor/three.module.min.js');

  // Antialiasing is fixed at context creation; a PC that starts in the low
  // tiers renders at reduced resolution anyway, where MSAA buys little.
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: TIERS_AA(Q), alpha: true, powerPreference: 'high-performance' });
  renderer.setClearColor(0x000000, 0);
  let pixel = Math.min(window.devicePixelRatio || 1, Q.px3d);
  renderer.setPixelRatio(pixel);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, .1, 100);
  camera.position.set(0, 0, 11.5);

  const root = new THREE.Group();
  scene.add(root);
  const color = new THREE.Color().setRGB(V.color[0] / 255, V.color[1] / 255, V.color[2] / 255, THREE.SRGBColorSpace);

  // ── plasma ──
  const plasmaU = {
    uTime: { value: 0 }, uAmp: { value: .08 }, uFreq: { value: 1.4 }, uSpeed: { value: .3 },
    uColor: { value: color.clone() }, uGlow: { value: 1 },
  };
  let detail = Q.detail;
  const plasma = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1, detail),
    new THREE.ShaderMaterial({
      uniforms: plasmaU, vertexShader: PLASMA_VERT, fragmentShader: PLASMA_FRAG,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    }),
  );
  root.add(plasma);

  // ── heart ──
  const tex = glowTexture(THREE);
  const heart = new THREE.Mesh(new THREE.SphereGeometry(.16, 24, 24),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .9 }));
  root.add(heart);
  const heartGlow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: tex, color: color.clone(), blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
  heartGlow.scale.setScalar(1.4);
  root.add(heartGlow);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({
    map: tex, color: color.clone(), blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, opacity: .35 }));
  halo.scale.setScalar(5.2);
  scene.add(halo);

  // ── lattice ──
  const latticeMat = new THREE.LineBasicMaterial({ color: color.clone(), transparent: true, opacity: .22,
    blending: THREE.AdditiveBlending, depthWrite: false });
  const lattice = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.42, 1)), latticeMat);
  root.add(lattice);

  // ── shell ──
  const N = 2400;
  const pos = new Float32Array(N * 3);
  const seeds = new Float32Array(N);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = golden * i;
    const R = 1.75 + (Math.random() - .5) * .08;
    pos[i * 3] = Math.cos(th) * r * R;
    pos[i * 3 + 1] = y * R;
    pos[i * 3 + 2] = Math.sin(th) * r * R;
    seeds[i] = Math.random();
  }
  // Shuffled, so that drawing only the first K points (lower tiers) still
  // covers the whole sphere evenly instead of stopping at a latitude.
  for (let i = N - 1; i > 0; i--) {
    const j = (Math.random() * (i + 1)) | 0;
    for (let k = 0; k < 3; k++) { const tmp = pos[i * 3 + k]; pos[i * 3 + k] = pos[j * 3 + k]; pos[j * 3 + k] = tmp; }
  }
  const shellGeo = new THREE.BufferGeometry();
  shellGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  shellGeo.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1));
  shellGeo.setDrawRange(0, Math.min(N, Q.shell));
  const shellU = {
    uTime: { value: 0 }, uAmp: { value: .1 }, uSize: { value: 4.2 }, uPixel: { value: pixel },
    uColor: { value: color.clone() }, uGlow: { value: 1 },
  };
  const shell = new THREE.Points(shellGeo, new THREE.ShaderMaterial({
    uniforms: shellU, vertexShader: SHELL_VERT, fragmentShader: SHELL_FRAG,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  root.add(shell);

  // ── orbits ──
  const orbits = [];
  const orbitMat = new THREE.MeshBasicMaterial({ color: color.clone(), transparent: true, opacity: .35,
    blending: THREE.AdditiveBlending, depthWrite: false });
  [[2.25, 1.15, .2], [2.45, -.5, .9], [2.65, .35, -1.0]].forEach(([r, rx, rz], i) => {
    const g = new THREE.Group();
    g.rotation.set(rx, 0, rz);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r, .0065, 6, 220), orbitMat);
    g.add(ring);
    const sat = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color: color.clone(),
      blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    sat.scale.setScalar(.38);
    g.add(sat);
    scene.add(g);
    orbits.push({ g, sat, r, phase: i * 2.1, dir: i % 2 ? -1 : 1 });
  });

  // ── sizing ──
  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // keep the reactor the same apparent size whichever side is shorter
    const fit = Math.min(1, w / h);
    camera.position.z = 11.5 / Math.max(.55, fit);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas);
  resize();

  // ── animation ──
  let t = 0, last = performance.now(), spinAcc = 0;
  const mouse = { x: 0, y: 0 };
  window.addEventListener('pointermove', (e) => {
    mouse.x = (e.clientX / innerWidth - .5) * 2;
    mouse.y = (e.clientY / innerHeight - .5) * 2;
  });
  const sm = { amp: .08, speed: .3, glow: .7, spin: .3, scale: 1 };
  const damp = (a, b, k, dt) => a + (b - a) * (1 - Math.exp(-k * dt));

  // Driven by main.js's single loop — one rAF for the whole page, so the
  // governor's frame cap applies to the 3D core too.
  function render(now) {
    const dt = Math.min(.05, (now - last) / 1000);
    last = now;
    t += dt;

    const P = V.physics;
    const lvl = V.level;              // live level for this state, 0..1, smoothed
    const burst = V.burst;            // wake / intent pulse, decays in main.js
    sm.amp = damp(sm.amp, Math.min(.32, P.amp * .8 + lvl * .2 + burst * .1), 6, dt);
    sm.speed = damp(sm.speed, P.speed + lvl * .5, 3, dt);
    sm.glow = damp(sm.glow, P.glow + lvl * .35 + burst * .4, 5, dt);
    sm.spin = damp(sm.spin, P.spin, 2, dt);
    sm.scale = damp(sm.scale, 1 + lvl * .18 + burst * .12, 10, dt);
    spinAcc += sm.spin * dt;

    color.setRGB(V.color[0] / 255, V.color[1] / 255, V.color[2] / 255, THREE.SRGBColorSpace);

    plasmaU.uTime.value = t;
    plasmaU.uAmp.value = sm.amp;
    plasmaU.uSpeed.value = sm.speed;
    plasmaU.uFreq.value = damp(plasmaU.uFreq.value, V.state === 'THINKING' ? 1.6 : 1.05, 2, dt);
    plasmaU.uGlow.value = sm.glow;
    plasmaU.uColor.value.copy(color);
    plasma.scale.setScalar(sm.scale);

    shellU.uTime.value = t;
    shellU.uAmp.value = sm.amp * 1.2;
    shellU.uGlow.value = sm.glow * (V.face ? .5 : 1);
    shellU.uColor.value.copy(color);
    shell.rotation.y = spinAcc * .35;
    shell.rotation.x = Math.sin(t * .2) * .15;

    lattice.rotation.y = -spinAcc * .2;
    lattice.rotation.z = spinAcc * .07;
    latticeMat.color.copy(color);
    latticeMat.opacity = .12 + sm.glow * .14;

    heart.scale.setScalar(1 + lvl * .9 + Math.sin(t * 2.2) * .05);
    heartGlow.material.color.copy(color);
    heartGlow.scale.setScalar(1.3 + lvl * 2 + burst * 1.5);
    halo.material.color.copy(color);
    halo.material.opacity = (.18 + sm.glow * .22) * (V.face ? .6 : 1);
    halo.scale.setScalar(5 + lvl * 1.4 + burst * 2);

    orbitMat.color.copy(color);
    orbitMat.opacity = .14 + sm.glow * .2;
    for (const o of orbits) {
      o.phase += dt * (.35 + sm.spin * .5) * o.dir;
      o.sat.position.set(Math.cos(o.phase) * o.r, Math.sin(o.phase) * o.r, 0);
      o.sat.material.color.copy(color);
      o.g.rotation.y += dt * .05 * o.dir;
    }

    // the face replaces the plasma, the frame of the reactor stays
    plasma.visible = heart.visible = heartGlow.visible = lattice.visible = !V.face;

    // a gentle parallax toward the pointer
    root.rotation.x = damp(root.rotation.x, mouse.y * .18, 2, dt);
    root.rotation.y = damp(root.rotation.y, mouse.x * .25, 2, dt);
    if (V.state === 'ERROR' && Math.random() < .04) root.position.x = (Math.random() - .5) * .08;
    else root.position.x = damp(root.position.x, 0, 12, dt);

    renderer.render(scene, camera);
  }

  function setQuality(q) {
    const px = Math.min(window.devicePixelRatio || 1, q.px3d);
    if (px !== pixel) {
      pixel = px;
      renderer.setPixelRatio(px);
      shellU.uPixel.value = px;      // particles keep the same on-screen size
      resize();
    }
    if (q.detail !== detail) {
      detail = q.detail;
      const old = plasma.geometry;
      plasma.geometry = new THREE.IcosahedronGeometry(1, detail);
      old.dispose();
    }
    shellGeo.setDrawRange(0, Math.min(N, q.shell));
  }

  return {
    render,
    setQuality,
    resume() { last = performance.now(); },
    info() { return { pixel, detail, shell: Math.min(N, shellGeo.drawRange.count) }; },
  };
}

function TIERS_AA(q) { return q.px3d >= 1; }
