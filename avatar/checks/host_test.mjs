/**
 * host_test.mjs — the phone's side of the seam, without a phone.
 *
 *     node avatar/checks/host_test.mjs
 *
 * host.js is what an Android WebView drives: machine facts, `avatar` events
 * off /ws, audio levels. Every rule in it is a copy of a desktop rule, and
 * each check below names the one it mirrors. The Director is the real one
 * (director.js), the catalogue a face-mode head, the clocks are the test's.
 */
import { Director } from '../js/director.js';
import { Catalogue } from '../js/catalog.js';
import { PhoneHost, stateWord, AVATAR_FRESH_SECONDS } from '../js/host.js';

const results = [];
function check(name, fn) {
  try {
    const detail = fn() || '';
    results.push([true, name, detail]);
  } catch (err) {
    results.push([false, name, err.message]);
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

function makeHost() {
  const clock = { mono: 100, wall: 1_800_000_000 };
  const performed = [];
  const spoken = [];
  const heard = [];
  const host = new PhoneHost({
    director: new Director(new Catalogue(['head', 'torso', 'arms', 'legs'], 'face', [])),
    perform: (p) => performed.push(p),
    speak: (l) => spoken.push(l),
    listen: (l) => heard.push(l),
    monotonic: () => clock.mono,
    wall: () => clock.wall,
  });
  return { host, clock, performed, spoken, heard };
}

const event = (ts, directive) => ({ type: 'avatar', ts, directive });

check('the state word is avatar_view._state_word', () => {
  const cases = [
    [{ link: 'CONNECTED', assistant: 'SPEAKING' }, 'SPEAKING'],
    [{ link: 'CONNECTED', assistant: 'SPEAKING', confirm: true }, 'CONFIRM'],
    [{ link: 'DISCONNECTED', assistant: 'SPEAKING' }, 'OFFLINE'],
    [{ link: 'RECONNECTING', assistant: 'LISTENING' }, 'RECONNECTING'],
    [{ link: 'ERROR' }, 'ERROR'],
    [{ link: 'CONNECTED', assistant: 'LISTENING', wokeAgoS: 0.4 }, 'WAKING'],
    [{ link: 'CONNECTED', assistant: 'LISTENING', wokeAgoS: 1.6 }, 'LISTENING'],
    [{ link: 'CONNECTED', assistant: 'UNKNOWN' }, 'UNKNOWN'],
    [{}, 'OFFLINE'],
  ];
  for (const [facts, want] of cases) {
    const got = stateWord(facts);
    assert(got === want, `${JSON.stringify(facts)} -> ${got}, attendu ${want}`);
  }
  return `${cases.length} cas`;
});

check('a state change performs; the same state does not perform twice', () => {
  const { host, performed } = makeHost();
  host.state({ link: 'CONNECTED', assistant: 'LISTENING' });
  host.state({ link: 'CONNECTED', assistant: 'LISTENING' });
  host.state({ link: 'CONNECTED', assistant: 'THINKING' });
  assert(performed.length === 2, `${performed.length} performances`);
  assert(performed[1].expression === 'thinking', performed[1].expression);
  return 'LISTENING, (rien), THINKING';
});

check('a fresh avatar event reaches the face', () => {
  const { host, clock, performed } = makeHost();
  host.state({ link: 'CONNECTED', assistant: 'SPEAKING' });
  const out = host.intent(event(clock.wall - 1, { intent: 'warn' }));
  assert(out === 'played', out);
  const last = performed[performed.length - 1];
  assert(last.expression === 'concerned' || last.reason.includes('warn'), JSON.stringify(last).slice(0, 160));
  return `joue : ${last.expression} (${last.reason})`;
});

check(`older than ${AVATAR_FRESH_SECONDS} s is dropped (net.py EV_AVATAR)`, () => {
  const { host, clock, performed } = makeHost();
  host.state({ link: 'CONNECTED', assistant: 'SPEAKING' });
  const before = performed.length;
  const out = host.intent(event(clock.wall - (AVATAR_FRESH_SECONDS + 1), { intent: 'warn' }));
  assert(out === 'stale' && performed.length === before, `${out}, ${performed.length - before} performance(s)`);
  return 'perime : aucune performance';
});

check('a reconnection replaying the last 50 events never puts an old face back', () => {
  const { host, clock, performed } = makeHost();
  host.state({ link: 'CONNECTED', assistant: 'SPEAKING' });
  const played = host.intent(event(clock.wall - 2, { intent: 'reassure' }));
  assert(played === 'played', played);
  const faces = performed.length;
  // The socket drops, comes back, and the server replays its history: the
  // same event, older ones, and ones from this morning.
  host.state({ link: 'RECONNECTING', assistant: 'SPEAKING' });
  host.state({ link: 'CONNECTED', assistant: 'SPEAKING' });
  const replay = [];
  for (let i = 0; i < 50; i += 1) replay.push(event(clock.wall - 2 - i * 600, { intent: 'amuse' }));
  const outcomes = replay.map((e) => host.intent(e));
  const replayed = outcomes.filter((o) => o === 'played').length;
  assert(replayed === 0, `${replayed} evenement(s) rejoue(s)`);
  const amused = performed.slice(faces).filter((p) => (p.reason || '').includes('amuse'));
  assert(amused.length === 0, 'un ancien visage est revenu');
  return `50 rejoues : ${outcomes.filter((o) => o === 'older').length} plus anciens, `
    + `${outcomes.filter((o) => o === 'stale').length} perimes, 0 joue`;
});

check('an event without ts is accepted (older server), like net.py', () => {
  const { host } = makeHost();
  host.state({ link: 'CONNECTED', assistant: 'LISTENING' });
  assert(host.intent({ type: 'avatar', directive: { intent: 'greet' } }) === 'played');
});

check('garbage is refused without throwing', () => {
  const { host, clock } = makeHost();
  const outs = [host.intent(null), host.intent({}), host.intent({ ts: 'x', directive: 'pas du json' }),
    host.intent(event(clock.wall, [1, 2]))];
  assert(outs.every((o) => o === 'invalid'), outs.join(','));
  return outs.join(', ');
});

check('an intent decays on the tick: the face moves, then the reflex returns', () => {
  // Past its 25 s the intent is gone but the mood it left fades over about
  // two minutes (director_parity.py, "une humeur qui retombe deux minutes").
  const { host, clock, performed } = makeHost();
  host.state({ link: 'CONNECTED', assistant: 'LISTENING' });
  host.intent(event(clock.wall, { intent: 'warn' }));
  const during = performed[performed.length - 1];
  clock.mono += 40;
  host.tick();
  const fading = performed[performed.length - 1];
  assert(fading !== during, 'rien n\'a bouge a 40 s');
  // The mood never "ends": it decays toward the baseline with a half-life
  // (director.js decayed), so what returns is the reflex FACE, not a label.
  clock.mono += 300;
  host.tick();
  const after = performed[performed.length - 1];
  assert(after.expression === 'neutral' && after.intensity < during.intensity,
    `a 340 s : ${after.expression} ${after.intensity}`);
  return `${during.expression} ${during.intensity} -> ${fading.expression} ${fading.intensity} `
    + `-> ${after.expression} ${after.intensity}`;
});

check('levels: the speaker drives the mouth, the mic the ears', () => {
  const { host, spoken, heard } = makeHost();
  host.level({ speaker: 0.6, mic: 0 });
  host.level({ speaker: 0, mic: 0.3 });
  assert(spoken.join() === '0.6,0' && heard.join() === '0.3', `${spoken} / ${heard}`);
});

const width = Math.max(...results.map(([, n]) => n.length));
let bad = 0;
for (const [ok, name, detail] of results) {
  if (!ok) bad += 1;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name.padEnd(width)}  ${detail}`);
}
console.log(`\n  ${results.length - bad}/${results.length} passed\n`);
process.exit(bad ? 1 : 0);
