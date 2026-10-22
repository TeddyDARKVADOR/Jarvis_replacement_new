/**
 * sim.js — a fake MARK LIII for when the page is opened outside Qt.
 *
 * Open `client_desktop/hud/web/index.html` through any static server and the
 * HUD runs against this: it cycles states, speaks with a synthetic level,
 * answers typed commands and raises a confirmation if you type "éteins".
 * It exists to tune the visual without a server, a key or a microphone.
 */
export function createSim(HUD) {
  const S = {
    state: 'CONNECTING', link: 'CONNECTING', assistant: 'UNKNOWN', gate: false, micOpen: false,
    mic: 0, voice: 0, wakeAge: null, confirm: null, messages: [], log: [],
    counters: { sent: 0, received: 0, played: 0, framesSent: 0, framesReceived: 0, framesPlayed: 0, dropped: 0, rate: 24000 },
    wake: { name: 'hey_jarvis', score: 0 }, retry: { attempt: 0, next: 0 }, error: null,
  };
  let speakUntil = 0, thinkUntil = 0, wokeAt = 0, pendingReply = null, confirmAt = 0;
  const now = () => performance.now() / 1000;
  const log = (l) => { S.log = [...S.log, `${new Date().toLocaleTimeString('fr-FR')}  ${l}`].slice(-80); };
  const say = (t, title = null) => {
    S.messages = [...S.messages, { j: true, t, a: Date.now() / 1000, ti: title }];
    speakUntil = now() + Math.min(9, 1.5 + t.length / 18);
  };

  setTimeout(() => { S.link = 'CONNECTED'; S.state = 'CONNECTED'; log('Connecté à MARK LIII (simulation).'); }, 1800);
  setTimeout(() => say('Bonsoir. Tous les systèmes sont opérationnels. En quoi puis-je vous aider ?'), 3000);

  const bridge = {
    ready() {},
    send(text) {
      S.messages = [...S.messages, { j: false, t: text, a: Date.now() / 1000 }];
      S.counters.sent += 120;
      log(`commande : ${text}`);
      if (/éteins|eteins|shutdown|redémarre/i.test(text)) {
        thinkUntil = now() + 1;
        setTimeout(() => {
          S.confirm = { id: 'c1', title: 'Arrêt du poste', detail: 'Matthew va éteindre cet ordinateur.', remaining: 90 };
          confirmAt = now();
        }, 1000);
        return;
      }
      thinkUntil = now() + 1.4;
      pendingReply = /météo|meteo/i.test(text)
        ? ['Ciel dégagé, 18 °C, vent faible. Aucune pluie prévue avant demain soir.', 'MÉTÉO — PARIS\n☀  18 °C  ·  vent 9 km/h\nDemain : 21 °C, averses le soir']
        : [`Bien reçu : « ${text} ». Je m'en occupe.`, null];
    },
    mic(open) { S.gate = open; S.micOpen = open; if (open) wokeAt = now(); log(open ? 'micro ouvert' : 'micro fermé'); },
    interrupt() { speakUntil = 0; log('interrompu'); },
    confirm(id, ok) { S.confirm = null; say(ok ? 'Très bien, c\'est fait.' : 'Entendu, j\'annule.'); },
    settings() { log('(réglages : disponibles dans l\'application)'); },
    quit() {}, hideToTray() {}, minimize() {}, toggleMaximize() {}, fullscreen() {},
    startMove() {}, startResize() {}, setMini(m) { HUD.setMini(m); },
    savePrefs() {}, setPushRate() {}, installWakeWord() {},
    rpc(id, op, json) {
      const f = window.__simRpc;
      Promise.resolve(f ? f(op, JSON.parse(json || '{}')) : { error: 'simulation : pas de cerveau' })
        .then((r) => HUD.rpcResult(id, r));
    },
    requestSettings() {
      setTimeout(() => HUD.settingsData({
        host: 'jarvis-vps.tailnet.ts.net', port: 8000, use_tls: false, device_token: 'demo-token',
        device_name: 'Poste de démo', device_id: 'sim-0001', input_device: null, output_device: null,
        inputs: ['Microphone (Realtek)', 'Casque USB'], outputs: ['Haut-parleurs (Realtek)', 'Casque USB'],
        wake_word_enabled: true, wake_word_threshold: .5, wake_available: false, wake_installed: false,
        gate_seconds: 25, mic_open_at_start: false, start_collapsed: false, notifications: true,
        autostart: false, paired: true, settings_path: '%LOCALAPPDATA%\\JarvisDesktop\\settings.json',
      }), 120);
    },
    saveSettings() { setTimeout(() => HUD.settingsSaved({ ok: true, changed: [], detail: 'Paramètres enregistrés (simulation).' }), 200); },
    testConnection() { setTimeout(() => HUD.settingsTest({ ok: true, detail: 'MARK LIII en ligne depuis 49 h 12 — jeton accepté (simulation).' }), 900); }, copy(t) { navigator.clipboard && navigator.clipboard.writeText(t); },
    openUrl(u) { window.open(u, '_blank'); },
  };

  // a wake every 25 s, so the shockwave can be seen
  setInterval(() => { if (S.state === 'CONNECTED') { wokeAt = now(); S.gate = true; setTimeout(() => { S.gate = false; }, 4000); } }, 25000);

  setInterval(() => {
    const t = now();
    if (pendingReply && t > thinkUntil) { say(...pendingReply); pendingReply = null; }
    if (S.link === 'CONNECTED') {
      S.assistant = t < thinkUntil ? 'THINKING' : t < speakUntil ? 'SPEAKING' : S.gate ? 'LISTENING' : 'UNKNOWN';
      S.state = S.assistant === 'UNKNOWN' ? 'CONNECTED' : S.assistant;
    }
    const syl = Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3) + Math.sin(t * 17) * .2);
    S.voice = S.state === 'SPEAKING' ? Math.min(1, syl * .9) : S.voice * .8;
    S.mic = S.gate ? Math.max(0, Math.sin(t * 5) * Math.sin(t * 1.3)) * .7 : 0;
    if (S.state === 'SPEAKING') { S.counters.received += 4800; S.counters.played += 4700; S.counters.framesReceived++; }
    S.wakeAge = wokeAt ? t - wokeAt : null;
    S.wake.score = S.wakeAge !== null && S.wakeAge < 1 ? .92 : Math.random() * .05;
    if (S.confirm) S.confirm.remaining = Math.max(0, 90 - (t - confirmAt));
    HUD.tick({ ...S });
  }, 33);

  let up = 0;
  setInterval(() => {
    up += 1;
    HUD.metrics({ available: true, cpu: 12 + Math.random() * 25, cores: 16, ram: 48 + Math.random() * 3,
      disk: 62, net_down: Math.random() * 2e6, net_up: Math.random() * 3e5, uptime: 3600 * 5 + up, battery: 87, plugged: true });
  }, 1000);

  setTimeout(() => HUD.init({
    prefs: { accent: '#7DD3FC', sound: false, volume: .35 },
    identity: { hostname: 'SIMULATION', os: 'Navigateur' },
    server: { host: 'jarvis-vps.tailnet.ts.net', port: 8000, tls: false, device: 'Poste de démo', paired: true },
    mini: false,
    // window.__simRpc (op, data) => result : un faux cerveau pour régler les
    // panneaux ESPRIT / STUDIO hors de Qt (test visuel, réglage du rendu).
    caps: window.__simRpc ? { cognition: true, local: true } : {},
    local: !!window.__simRpc,
  }), 50);

  return bridge;
}
