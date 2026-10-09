/**
 * states.js — what each of the panel's eight states looks like and says.
 *
 * One table, read by every renderer, so the orb, the rings, the chip and the
 * caption can never disagree about what "THINKING" means. Colours are the
 * panel's (`ui/panel.py::_STATE_COLOURS`); `accent: true` means "the user's
 * chosen colour" instead of a fixed one.
 *
 * The physics columns drive the reactor:
 *   amp    noise displacement of the core       speed  how fast the noise moves
 *   spin   rotation speed of rings and shells   glow   overall brightness
 *   react  which live level shapes the core ('mic' | 'voice' | null)
 */

export const STATES = {
  OFFLINE:      { label: 'HORS LIGNE',  color: '#6B7A93', amp: .04, speed: .15, spin: .15, glow: .35, react: null },
  CONNECTING:   { label: 'CONNEXION',   color: '#FBBF6B', amp: .10, speed: .55, spin: 1.2, glow: .6,  react: null },
  RECONNECTING: { label: 'RECONNEXION', color: '#FBBF6B', amp: .10, speed: .55, spin: 1.2, glow: .6,  react: null },
  ERROR:        { label: 'ERREUR',      color: '#F87171', amp: .22, speed: .9,  spin: .4,  glow: .55, react: null },
  CONNECTED:    { label: 'PRÊT',        accent: true,     amp: .08, speed: .3,  spin: .35, glow: .75, react: null },
  LISTENING:    { label: 'ÉCOUTE',      color: '#6EE7B7', amp: .10, speed: .5,  spin: .6,  glow: .9,  react: 'mic' },
  THINKING:     { label: 'RÉFLEXION',   color: '#C4B5FD', amp: .22, speed: 1.4, spin: 2.4, glow: .95, react: null },
  SPEAKING:     { label: 'RÉPONSE',     accent: true,     amp: .08, speed: .6,  spin: .8,  glow: 1.0, react: 'voice' },
  SLEEPING:     { label: 'EN SOMMEIL',  color: '#4B5A73', amp: .03, speed: .1,  spin: .1,  glow: .3,  react: null },
  CONFIRM:      { label: 'CONFIRMATION', color: '#FBBF6B', amp: .14, speed: .7,  spin: .5,  glow: .9,  react: null },
};

export function hintFor(state, ctx) {
  switch (state) {
    case 'OFFLINE':
      return ctx.paired ? 'Liaison coupée — cliquez sur ⚙ pour vous reconnecter'
                        : 'Non appairé — ouvrez ⚙ pour saisir l\'hôte et le jeton';
    case 'CONNECTING':   return ctx.local ? 'Démarrage de JARVIS sur ce PC…' : 'Connexion à MARK LIII…';
    case 'RECONNECTING': return ctx.retry ? `Nouvelle tentative dans ${ctx.retry} s` : 'Reconnexion…';
    case 'ERROR':        return ctx.error || 'Erreur de liaison';
    case 'CONNECTED':    return ctx.wake ? 'Dites « Hey Jarvis » ou cliquez sur le noyau' : 'Cliquez sur le noyau ou Ctrl+Espace pour parler';
    case 'LISTENING':    return ctx.gate ? 'Je vous écoute — parlez' : 'À l\'écoute';
    case 'THINKING':     return 'Analyse en cours…';
    case 'SPEAKING':     return 'Cliquez ou Échap pour interrompre';
    case 'SLEEPING':     return 'Dites « Hey Jarvis » pour me réveiller';
    case 'CONFIRM':      return 'Une action attend votre accord';
    default:             return '';
  }
}

export function colorOf(state, accent) {
  const s = STATES[state] || STATES.OFFLINE;
  return s.accent ? accent : s.color;
}

// ── colour helpers ────────────────────────────────────────────────────────
export function hexToRgb(hex) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
export const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
export const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
