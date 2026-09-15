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
| `watch.py` | Prints a timeline of link transitions. One line per change, nothing while healthy. |
| `selftest.py` | 12 checks, no Gemini, no microphone, no display, no network. |

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

### Gemini Live closes with 1008 every 2–3 minutes

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

**Not fixed, deliberately.** It belongs to a dedicated phase covering Gemini
session lifecycle, `go_away`, pre-emptive reconnection and error classification.
Nothing here works around it.

### `POST /api/wake` does nothing

`main.py` never calls `dashboard.set_wake_callback`, so the endpoint accepts the
request and has no effect. The Android client does not use it. Sending a text
command wakes the assistant.

### No interrupt from the phone

`interrupt()` is bound to a desktop HUD button only, and `_relay_phone_audio`
discards uplink audio while JARVIS is speaking — so there is no voice barge-in
either. A route in `server/api.py` calling `ui.on_interrupt` would fix half of
it; the other half is in `main.py`.

### The confirmation gate expires unanswered

`core/confirm.py` parks irreversible actions behind an on-screen CONFIRM button.
There is no such button on the phone yet, so such an action is announced,
surfaced in `/status`, and expires after 90 seconds without running. That is the
safe failure: nothing runs that would not have run before.

---

## Non-regression

After any change:

```bash
python -m server.selftest
git diff -- main.py dashboard core memory actions plugins
```

12/12, and an empty diff. `selftest.py` parses `main.py`'s syntax tree to
collect every `self.ui.<attr>` it touches and fails if `HeadlessUI` is missing
one — so the stand-in cannot silently fall behind the file it stands in for.
