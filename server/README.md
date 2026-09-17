# The 24/7 layer

MARK LIII, running on a server with no screen and no sound card, reachable from
an Android phone.

Written for whoever maintains this next. It documents the layer built *around*
MARK LIII — not MARK LIII itself, which is unchanged.

```
📱 client-android  ──►  server/  ──►  main.py  ──►  Gemini
                                        │
                                  actions/ plugins/ memory/
```

---

## The rule this layer is built on

**Nothing in `server/` is imported by anything that existed before it.**

```
server/  ──────►  main.py, dashboard/, core/, memory/
server/  ◄──X──   (nothing existing imports server/)
```

`python main.py` on a desktop is byte-for-byte the program it was before this
directory existed. That is verified on every run of `server/selftest.py`, which
fails if `git status` reports a modification to `main.py`, `dashboard/`,
`core/`, `memory/`, `actions/`, `plugins/` or `requirements.txt`.

The same JarvisLive runs in both modes. Two things differ, and both are
substitutions rather than edits:

| | Desktop | Server |
|---|---|---|
| `sys.modules["ui"]` | `ui.py` (PyQt6) | `server/headless_ui.py` |
| `sys.modules["sounddevice"]` | the real binding | `server/audio_bridge.py` |

Installing the `ui` stand-in is also why **PyQt6 is not installed on the
server**: the real `ui.py` is never imported.

---

## Files

| File | What it is |
|---|---|
| `run_headless.py` | The entry point. Installs the two stand-ins *before* importing `main`, then builds the same `JarvisLive`. |
| `headless_ui.py` | A stand-in for `ui.JarvisUI` — the same 26-attribute surface, no window. |
| `audio_bridge.py` | A stand-in for `sounddevice`: a microphone that delivers nothing, and a speaker that publishes to the phone. |
| `api.py` | Grafts `/health`, `/status` and `WS /ws/phone-out` onto the FastAPI app `dashboard/server.py` already runs. |
| `auth.py` | The persistent device credential, seeded into the dashboard's own pairing store. |
| `runtime_state.py` | Records the state stream `main.py` already emits. Not a second state machine. |
| `notify.py` | Turns a `NOTIFY` decision into an event on the socket the phone already listens to. Holds recent ones so a reconnect does not lose them, and re-offers them so a reconnect does not repeat them. |
| `watch.py` | Prints a timeline of link transitions. One line per change, nothing while healthy. |
| `selftest.py` | 22 checks, no Gemini, no microphone, no display, no network. |

## Running it

```bash
python -m server.selftest          # 12/12 before anything else
python -m server.run_headless      # the assistant
python -m server.run_headless --pairing    # the phone's device token
python -m server.watch             # live timeline
```

Deployment: [`../deployment/ORACLE.md`](../deployment/ORACLE.md).
Wire protocol: [`../client-android/PROTOCOL.md`](../client-android/PROTOCOL.md).

## The two things worth understanding

**The speaker is a socket.** `_play_audio` in `main.py` calls
`stream.write(pcm)`. The stand-in's `write()` publishes those bytes to every
`/ws/phone-out` listener. That one line is the entire downlink — the batching,
the interrupt behaviour and the 24 kHz format are all `main.py`'s, untouched.

**The uplink already existed.** `dashboard/server.py` has served
`/ws/phone-audio` since before this work; `main.py`'s `_relay_phone_audio` feeds
it to Gemini. The phone did not need a new path, only a client.

---

## Validated on real hardware

Measured over USB adb on a Redmi Note 14 Pro+ 5G (Android 16 / HyperOS 3.0),
15 September 2026.

| | Evidence |
|---|---|
| Foreground service in background | `service=alive fg=true types=0x00000080` (microphone), microphone still held, 1.2 s of speech delivered |
| Screen off, 1 / 5 / 15 min | all three held; the phone never reconnected once in 16 minutes |
| Survives MIUI defaults | no autostart exemption, **not** in the `deviceidle` whitelist, no recent-apps lock |
| Phone → MARK LIII → Gemini | Gemini transcribed French speech and called `system_status` |
| MARK LIII → phone | `RX = Played`, `Dropped 0` |
| Bluetooth output | `usage=USAGE_ASSISTANT → Devices: bt_a2dp`, full quality, no SCO, no Bluetooth permission |
| Wake word pipeline | on-device self-test: worst deviation **7e-9** against openWakeWord's own output |
| Forced stop | `am force-stop` kills it and Android does **not** restart it — correct for a microphone foreground service |

Two things follow that are easy to misread:

* **A2DP is output only.** JARVIS speaks into the headset but listens through
  the *phone's* microphone. Using the headset's microphone would mean SCO, which
  drops the audio to 8 kHz telephony.
* **11 actions load on a server, not 16.** Five import `pyautogui` and need an X
  display; the loader rejects them. That is correct — a VPS has no mouse.

---

## À TESTER PLUS TARD

Not yet verified. **None of these should be presented as working.** They do not
block deployment.

```
⏳ Endurance réelle 1–2 h ou nuit complète
⏳ Consommation batterie sans câble USB
⏳ Perte réseau réelle dans une topologie différente
⏳ Redémarrage du serveur avec téléphone réellement connecté
⏳ Résilience prolongée MIUI / HyperOS lorsque le standby bucket devient FREQUENT/RARE
⏳ Test Bluetooth avec changement casque ↔ haut-parleur
⏳ Redémarrage automatique après arrêt forcé
⏳ Test prolongé du wake word avec la voix réelle de l'utilisateur
```

Why each is still open:

* **Endurance / battery** — the longest measured run is 33 minutes, and the
  phone was charging over the adb cable throughout, so no discharge figure
  exists. The standby bucket was `ACTIVE` because the app had just been used;
  over hours it drops to `FREQUENT`/`RARE` and HyperOS gets more aggressive. The
  16-minute result does not extend to a night.
* **Network loss** — untestable in the topology it was measured in: the PC's
  default gateway *was the phone* (the laptop was on the phone's hotspot), so
  the link could not be cut from either side. Needs a real router between them.
* **Server restart with the phone connected** — the phone was disconnected at
  the time.
* **Wake word with a real voice** — the pipeline is proven numerically identical
  to the reference implementation, which means it will detect as well as
  openWakeWord does. It has never been triggered by an actual spoken
  "Hey Jarvis".

---

## Known bugs

### ~~Gemini Live closes with 1008 every 2–3 minutes~~ — fixed, but read the caveat

Gemini still closes the session every 2–3 minutes. It is no longer an error, no
longer logged as one, and no longer costs 3 seconds of silence. The history
below is kept because the measurement is what identified it as a timeout rather
than a fault.

```
[PhoneOut] listener connected
[PhoneOut] listener gone
[JARVIS] ❌ Recv: 1008 None. The operation was aborted.
[JARVIS] Reconnecting in 3s...
```

Measured over 33 minutes of continuous monitoring:

```
11 drops, 11 recoveries, 0 unrecovered errors
15:27:26  15:30:02  15:32:50  15:36:16  15:38:47  15:42:16
15:44:50  15:47:23  15:50:51  15:56:15  15:58:50
→ every 2 min 30 to 3 min 30
```

The regularity is the finding: that is not an error pattern, it is a timeout
pattern. No correlation with the screen being off or with the phone
disconnecting. Session resumption restores the conversation each time and it is
inaudible in use.

**A second finding, from reproducing the exception shape:**

```python
str(BaseExceptionGroup(...)) == 'unhandled errors in a TaskGroup (1 sub-exception)'
```

`_receive_audio` raises inside an `asyncio.TaskGroup`, so what reaches the
handler in `main.py:1671` is an `ExceptionGroup` whose `str()` contains neither
`"1008"` nor any network keyword. **`main.py`'s error classification is
therefore bypassed for everything raised inside the TaskGroup** — including its
"API key invalid" branch, which is fortunate: that branch would have parked the
server waiting for a key to be re-entered. The generic path runs instead, which
is why the log says `Reconnecting in 3s`.

**Fixed.** The drop itself is Gemini's, and nothing on this side prevents it —
what is fixed is that it is no longer an error. Three changes in `main.py`:

* `_flatten_exc_text()` walks the group and the `__cause__`/`__context__` chain,
  so the run loop's classifier finally reads a string that *contains* the status
  code. This is the second finding above, undone.
* `_is_session_rotation()` recognises the 1008 close and the run loop reconnects
  at once, keeping the resumption handle — one log line, no backoff, no
  traceback, conversation intact.
* `_receive_audio` handles `go_away`, Gemini's advance warning of a planned
  close, and rotates *before* the socket dies.

Measured over a clean 6-minute window on the Oracle deployment, 3 rotations:

```
Traceback        0
JARVIS] Error    0
Reconnecting in  0      (was 3s of silence per drop)
starting fresh   0      (context kept every time)
```

The journal now contains nothing but the expected rotation lines.

**One caveat, worth knowing before you trust the `go_away` path:** in 16 minutes
of observation on `gemini-3.1-flash-live`, **`go_away` never arrived** — 0
occurrences against 6 rotations. Every rotation went through the 1008 fallback.
The handler is correct per the API contract and costs six lines, so it stays;
but it is currently untested against a real message, and the fallback is what is
actually carrying the fix. If you ever see `Session end announced by Gemini` in
the log, that path has finally fired.

Not attempted: rotating pre-emptively on a timer, before Gemini does. The
cadence is regular enough to guess at, but it would be a guess — it would add
reconnections when they are not needed, and the drop is already invisible.

### The exception-shape fix has a second consequence

The classifier was reading the wrong string for *everything* raised inside the
session TaskGroup, not just 1008. Now that it reads the real text, its other
branches became reachable — including the "API key invalid" one, which parks the
assistant until a human types a key. That branch used to test the bare substring
`"1007"`, which would also match a resumption handle containing those four
characters. On a headless server a false positive there is unrecoverable without
SSH, so it now matches `\b1007\b`. If you add branches to that classifier, keep
them that specific.

### `POST /api/wake` does nothing

`main.py` never calls `dashboard.set_wake_callback`, so the endpoint accepts the
request and has no effect. The Android client does not use it. Sending a text
command wakes the assistant.

### ~~No interrupt from the phone~~ — fixed

A client sends `{"type":"interrupt"}` on the existing `/ws`. It reaches the same
`interrupt()` the HUD button calls, and `AudioHub.flush()` then drops every frame
already queued for every listener.

That second step is the one worth knowing about. `interrupt()` drains the queue
feeding `_play_audio`, which stops audio being *produced* — but Gemini produces
faster than real time, so up to `_QUEUE_SLOTS` frames (~40 s) can already be past
that point. Without the flush, an interrupt stops the source and the phone goes
on speaking the abandoned answer for another half minute, which reads as the
button doing nothing. The client clears its own playback buffer too; the server
cannot reach into it.

The wiring is in `server/run_headless.py` (`_remote_interrupt`), not in
`main.py`: `JarvisLive.__init__` already puts `interrupt` on the UI object, so
both ends of the wire existed and this only joins them.

**Still missing: voice barge-in.** `_relay_phone_audio` discards uplink audio
while JARVIS is speaking, so interrupting by talking over it is not possible —
only by pressing the button.

### ~~The confirmation gate expires unanswered~~ — fixed

`core/confirm.py` parks irreversible actions behind a CONFIRM button. There is
now one on the phone: the request goes out as a `confirm` event carrying an id,
and the answer comes back as `confirmation_response`. See
[PROTOCOL.md §4.1](../client-android/PROTOCOL.md).

**The client sends a decision, never an action.** What runs is decided here,
against the pending request and its own 90 s expiry.

`core/confirm.py` gained one field for this: `cid`. A HUD could not answer the
wrong question — its banner and the pending request were the same thing by
construction — but a phone can hold a stale banner across an expiry and a new
request, and without an id its CONFIRM would resolve whatever happened to be
waiting. That is how "yes, empty the trash" becomes "yes, shut down the machine".
`resolve(accepted, cid)` applies an answer only if `cid` is the request still
waiting; a stale answer is discarded **and leaves the live request waiting**.
`resolve(accepted)` with no id keeps the HUD's original behaviour.

With no client connected the behaviour is unchanged: announced, surfaced in
`/status`, expires unconfirmed.

---

## Non-regression

After any change:

```bash
python -m server.selftest
git diff -- main.py dashboard core memory actions plugins
```

22/22, and an empty diff. `selftest.py` parses `main.py`'s syntax tree to
collect every `self.ui.<attr>` it touches and fails if `HeadlessUI` is missing
one — so the stand-in cannot silently fall behind the file it stands in for.
