# JARVIS Desktop

A permanent MARK LIII terminal for a Windows workstation. The third client, not
a third assistant.

```
                 ☁️  ORACLE
              MARK LIII + Gemini
                     │
                  Tailscale
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
       📱 Android           💻 client_desktop
```

---

## The rule this package is built on

The same one `server/` was built on, in the same direction:

```
client_desktop/  ──────►  core.wake_word        (imported, never modified)
client_desktop/  ◄──X──   (nothing existing imports this package)
```

`python main.py` on a desktop, and `python -m server.run_headless` on the VPS,
are byte-for-byte the programs they were before this directory existed.
`server/selftest.py` verifies that on every run; `client_desktop/selftest.py`
verifies it again from this side, plus two things `server/` cannot know to
check:

* **nothing here imports `main` or a Gemini SDK** — the "no second session"
  promise, made mechanical rather than left as a comment;
* **`core.wake_word` is the only thing reached into `core/`** — so a future
  `core.llm_client` import cannot arrive as a quiet one-line diff.

### Why it does not reuse `JarvisLive`

`JarvisLive` *is* the brain. Constructing one opens a Gemini Live session, and
Oracle already runs exactly one. A desktop client that instantiated it would be
a second assistant wearing the same name: two sessions, two histories, two
bills, and a memory that disagrees with itself. So this speaks the wire protocol
instead — and could not open a session if it tried, because the API key never
leaves the server.

### Why the directory is `client_desktop` and not `client-desktop`

A hyphen cannot be a Python package name. `client-android` is Kotlin and does
not care; this has to be importable, and the repository already runs
`python -m server.run_headless`.

---

## Running it

```bash
python -m client_desktop                # the panel
python -m client_desktop --debug        # the panel, developer window open
python -m client_desktop --selftest     # no window, no network, no microphone
python -m client_desktop --autostart on # register the Windows startup entry
```

Dependencies are all already in `requirements.txt` for other reasons — PyQt6,
sounddevice, numpy, requests, pywin32 — plus **`websockets`**, which the server
pulls in through `uvicorn[standard]` and which this client uses directly.
`requirements.txt` is deliberately not edited: it is one of the guarded paths.

`openwakeword` stays opt-in, exactly as it is for the desktop assistant. Without
it the client works; the microphone is opened with the button instead of by
voice, and the debug window says so.

---

## Pairing

There is no separate setup screen. The **Developer / Debug** window is it, and
it opens by itself on a machine that has never been paired.

| Field | Where it comes from |
|---|---|
| **Hôte** | the MagicDNS name, e.g. `jarvis-vps.tailnet-XXXX.ts.net` |
| **Port** | 8000 |
| **Jeton** | `python -m server.run_headless --pairing` on the VPS |

Use the MagicDNS name, not a `100.x.y.z` address — it survives a node being
re-registered.

Settings live in `%LOCALAPPDATA%\JarvisDesktop\settings.json`, **not** in
`config/`. The device token is a password, and a secret inside a git working
tree is one `git add -A` away from being published. `%LOCALAPPDATA%` is ACL'd to
the user; the file is not encrypted beyond that.

---

## The panel

```
┌────────────────────────────────────────────────────┐
│                 work happens here                  │
│                                      ┌───────────┐ │
│                                      │  JARVIS   │ │
│                                      │     ◉     │ │
│                                      │ Je vous   │ │
│                                      │ écoute.   │ │
│                                      │ ●CONNECTED│ │
│                                      └───────────┘ │
└────────────────────────────────────────────────────┘
```

Three properties, each a decision made once:

* **It does not take focus.** `WA_ShowWithoutActivating` on a `Qt.Tool` window:
  it can appear, change state, flash a wake ring and raise a confirmation while
  the caret stays in the editor being typed in.
* **It is not in the taskbar and not in Alt-Tab.** This is furniture, not an
  application to switch to.
* **It stays on top, which is why it must be narrow.** A 20 % strip is presence;
  an always-on-top window taking half the screen is an obstruction.

Measured on the machine it was built for — 1920×1080 at 125 % scaling = 1536×864
logical — 20 % is **307 px**, and that is what it renders at. The fraction is
clamped to 260–460 px so it stays readable on a small laptop and does not become
a billboard on an ultrawide.

`—` collapses it to a 68 px strip showing only the core. The tray icon toggles
it, and double-clicking the tray brings it back.

---

## Where it goes

Four modes, configured under **Position de JARVIS** in the debug window and
persisted like everything else.

| Mode | Placed against | Anchors |
|---|---|---|
| **Screen** | the screen's *work area* | Left · Right · Top · Bottom |
| **Window** | the foreground window, once | same four, plus "Réaligner" |
| **Follow** | the foreground window, continuously | same four |
| **Free** | nothing — dragged by hand, remembered | — |

**All of the arithmetic is in `placement.py`, which imports neither Qt nor
Win32.** Rectangles in, a rectangle out. That is what makes 26 of the 43
selftests possible: placement defects live on the third monitor at 150 %
scaling behind a maximised editor, and synthetic rectangles reproduce that in a
millisecond where a real desktop cannot reproduce it at all.

### The work area, not the screen

`QScreen.availableGeometry()` already excludes the taskbar wherever the user
keeps it and whether or not it auto-hides. Subtracting a taskbar by hand is how
a panel ends up underneath one on the machine where it lives on the left.

### Aligning against a window, in three attempts

1. the requested side, if the panel fits without leaving the screen;
2. **the opposite side** — a panel asked to sit right of a window that is itself
   pinned to the right edge belongs on its left, not half off the desktop;
3. the screen edge on the requested side, accepting an overlap.

Step 3 is a deliberate concession: against a maximised window there is no
non-overlapping answer, and refusing to place the panel would be worse than
covering 300 px of an editor that scrolls.

### Following, without ever stealing focus

`GetForegroundWindow()` polled at 4 Hz, not `SetWinEventHook`. The hook is the
"correct" API and it needs a C callback invoked on someone else's UI thread; a
Python callback there can take another process's window manager down with it.
One syscall four times a second costs less than a frame of the core animation.

Three things keep it stable:

* **Our own handles are excluded.** Otherwise clicking the panel makes the panel
  the foreground window, it aligns against itself, which moves it, which it
  aligns against again — a drift into the corner that reads as the panel
  wandering off.
* **A new window must hold still for 250 ms.** Alt-tabbing through six windows
  should not drag the panel across the desktop six times.
* **Movements under 8 px are ignored.** Physical-to-logical conversion divides
  by a scale factor, so an unmoved window can report a different coordinate
  between two reads, and the panel would shiver.

Minimised windows are excluded, and that is not an optimisation: a minimised
window's rectangle is around (−32000, −32000).

### Physical pixels and logical pixels

Windows reports physical pixels; Qt lays out in logical ones. `ui/screens.py` is
the only file that sees both:

```
logical = screen.geometry().topLeft()
        + (physical − monitor.physicalTopLeft()) / scale
```

Dividing the raw physical coordinate by the scale is the version that works on
one monitor and fails on two: a 1920-wide primary at 125 % is 1536 logical, so a
second monitor physically at x=1920 starts at x=1536 for Qt. Monitors are
matched by device name (`\\.\DISPLAY1`), which `GetMonitorInfoExW` and
`QScreen.name()` spell identically.

### Snap, and a position that outlives its monitor

Snapping moves the panel and never resizes it — a window that changes size when
dragged near an edge reads as a bug however it is explained. Both of the panel's
edges are candidates on each axis, against both the bare screen edges and the
edges inset by the margin.

A saved FREE position outlives the monitor it was saved on: undock, change a
resolution, unplug a screen, and the stored rectangle points at empty space.
`ensure_visible` corrects a rectangle that no longer has at least 80×80 px on
any real work area, because Windows would place a window there quite happily and
the user would have no way to get it back. Screen additions, removals and DPI
changes all re-run it immediately rather than at the next restart.

### Responsive, without guessing at resolutions

The content reflows off the size it actually has, in three densities:

| | |
|---|---|
| **full** | core, last line, state, history, input |
| **compact** | history dropped, core halved |
| **strip** | core *beside* the text, no history — a wide short panel |

The direction of one `QBoxLayout` is flipped rather than the widget tree
rebuilt: the core is a single widget that cannot exist in two layouts, and
recreating it on every resize would restart its animation.

Two ordering rules here were each found by a test rather than by reasoning:

* **The density is applied before `setGeometry`, not after.** Qt will not shrink
  a window below its layout's minimum, so a panel still laid out as a tall
  column cannot be given a 241 px strip — the call silently clamps it and the
  strip never happens.
* **The density is decided on the unmodified rectangle.** A pending
  confirmation makes the panel grow to fit its banner; measuring the density
  *after* that growth makes the two feed each other, and a strip that asked for
  241 px settles at 421.

`resizeEvent` deliberately does not recompute anything. This window is frameless
with no resize grip, so every resize is either our own or Qt growing the window
to fit its content, and re-deciding on the latter is the runaway above.

### Topmost is visibility, never activation

`setGeometry` on an already-visible window does not activate it, and nothing
here calls `raise_()`, `activateWindow()` or `SetForegroundWindow` on its own
initiative. Toggling the setting re-applies the flag and re-shows the window,
which `WA_ShowWithoutActivating` keeps silent — and the geometry is restored
afterwards, because re-showing can nudge it on some Windows builds.

### The eight states

`OFFLINE · CONNECTING · CONNECTED · LISTENING · THINKING · SPEAKING ·
RECONNECTING · ERROR`

Two enums collapsed into one label, and **the link always outranks the
assistant**: what MARK LIII last said it was doing is not interesting while this
machine cannot reach it.

The core is a port of `JarvisCore.kt`, primitive for primitive and colour for
colour, so the two clients are recognisably one product. One difference, and it
is not cosmetic: every animation is a pure function of `time.monotonic()` rather
than a framework-owned clock, so the phases stay correct when Windows throttles
the paint timer — which it does the moment a maximised editor covers the panel,
the state it spends most of its life in.

---

## The gate

Detection and permission are deliberately separate:

```
   microphone  ──every frame──►  wake word          (local, always)
                                      │
                                   detected
                                      ▼
   microphone  ──while open───►  the gate  ──►  /ws/phone-audio   (the VPS)
```

* The gate **shuts on a timer** (25 s, refreshed by activity). A gate that
  stayed open until something closed it is an open microphone on a work laptop
  the first time a callback is missed — and that failure is silent.
* The uplink socket's lifetime **is** the gate's lifetime. Connecting it means
  something to the server: it broadcasts that a microphone is live and silences
  its own.
* The microphone is **not held open when nothing needs it**. It runs when the
  wake word is listening or the gate is open, and stops otherwise.

The mic button latches the gate open and ignores the timer, which is the
push-to-talk case. Clicking the core interrupts JARVIS mid-sentence if it is
speaking, and toggles the gate otherwise.

---

## Starting with Windows

`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `pythonw.exe launch.pyw`.

**Not a service:** a service runs in session 0, with no desktop to show a panel
on and no audio session to open a microphone in. It would mean a service *plus*
a user-mode agent *plus* the IPC between them.

**Not Task Scheduler:** its restart-on-failure policy, combined with the Run key
and a user double-clicking the shortcut, is precisely the duplicate-instance
loop the brief warns against. The mutex is the safety net; not building the
hazard is the fix.

The Run key needs no administrator rights and is visible in Task Manager →
Startup, where it can be turned off without asking anyone.

### One instance

A named kernel mutex (`Local\JarvisDesktop.SingleInstance`), not a PID file: a
PID file survives a crash and then lies. A second launch sets a named event and
exits; the first sees it and shows its panel, so a double-clicked shortcut does
something visible rather than nothing.

`Local\` rather than `Global\`: two fast-switched users on the same workstation
each get their own JARVIS, their own microphone and their own settings.

---

## Reconnection

Ported unchanged from `ReconnectManager.kt`, because the failure it was written
against is not Android-specific.

| Situation | Response |
|---|---|
| ordinary close, server restart, network gone | backoff 1→2→4→8→15→30 s, ±20 % |
| close **4001** | the bearer went stale — log in again, not fatal |
| **401** on `/api/device-login` | the credential is refused — stop and say so |

* **The delay is capped, the attempts are not.** A workstation whose tunnel is
  down while the VPS reboots must still be connected in the morning.
* **A connection that lived less than 3 s does not reset the backoff.** It is
  one failure in two acts; treating it as a success produces a tight retry loop
  that reads like a working reconnect in the logs.
* Android gets "the network is back" from a system callback. Here a cheap TCP
  knock during a long countdown does the same job — otherwise the client sits
  out thirty seconds that the returning link has just made pointless.

---

## Verified

`python -m client_desktop --selftest` → **16/16**, and `python -m server.selftest`
still reports **15/15** with *MARK LIII core is unmodified — 8 paths clean*.

A live UI run rendered the panel at 307×848 and painted all eight core moods,
the confirmation banner, the history and the debug window without an exception.

**Against the real MARK LIII on `jarvis-vps.tailf11740.ts.net:8000`**
(`{"ok":true,"service":"MARK LIII"}`, 49 h uptime, 8 ms via DERP Paris):

| | |
|---|---|
| `device-login` + `/ws` + `/ws/phone-out` | CONNECTED in **0.1–0.6 s** |
| downlink format | announced **24 000 Hz**, player built from the announcement |
| text command → answer | **5 of 6 turns**, 3.6–4.7 s, mean **4.2 s** |
| JARVIS's voice | 106 k–171 kB of PCM per turn, played on the laptop's speakers |
| `bytes_played` vs `bytes_received` | **exactly equal** — no underrun, no padding miscount |
| `interrupt` | local buffer flushed to 0 |
| disconnect | clean, `OFFLINE` |

### The one measured defect, and it is the server's

**The first command after a fresh connection is lost.** Six turns on one
connection: turn 1 timed out with the server reporting *0 frames produced*;
turns 2–6 all answered. The server log shows why:

```
SYS: Phone connected via Remote Dashboard.
[Web]: <the command>
SYS: Reconnected — conversation restored.      ← the turn died here
SYS: JARVIS online.
```

Connecting a client coincides with a Gemini session rotation, and the turn in
flight is swallowed. `counts` on this deployment: **538 sessions / 538 reconnects
in 49 h**, one every ~5.5 minutes — the 1008 rotation already documented in
[`../server/README.md`](../server/README.md#known-bugs).

A fix belongs in `main.py` or `dashboard/server.py`, both of which this client
must not modify. What it does instead is **notice and say so** after 25 s, in
the log and as a notification. It deliberately **does not retry**: a command can
be "delete the downloads folder", and a silent second attempt at an irreversible
action is a far worse failure than a sentence the user has to repeat.

## À TESTER — not yet verified

**None of these should be presented as working.**

```
⏳ Le wake word « Hey Jarvis » avec une vraie voix
⏳ L'uplink micro reel (micro -> Gemini) — seul le sens descendant est teste
⏳ Le demarrage automatique a une vraie ouverture de session
⏳ La reconnexion apres un redemarrage reel du serveur
⏳ Le comportement apres veille/reprise du portable
⏳ Une session longue (plusieurs heures)
```

The wake word is untested for a simple reason: `openwakeword` is not installed
on this machine, and installing it is opt-in. The button path works without it.

The uplink is untested because every live test so far drove JARVIS with typed
commands. `/ws/phone-audio` opens and closes with the gate in code that the
offline selftest covers, but no real speech has gone up it from this machine.

### Note on the Tailscale prerequisite

`tailscaled` can sit in `BackendState: NoState` indefinitely — reporting
"Tailscale is starting" and listing no peers — when **`tailscale-ipn.exe` is not
running**. On Windows that GUI process is what activates the logged-in user's
profile against the daemon; restarting the *service* does not help. Starting
`tailscale-ipn.exe` brought the tailnet up immediately and revealed all three
nodes. It is in Common Startup, so this only bites if it has been closed.
