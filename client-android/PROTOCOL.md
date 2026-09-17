# MARK LIII ↔ client protocol

Reference for anyone writing a client. Nothing here was designed for Android:
every endpoint already existed, three of them since before the phone work
started. This document records what the server does, not what a client wishes
it did.

Source of truth, in order: `dashboard/server.py`, `server/api.py`, `main.py`.

```
┌──────────┐   PCM 16 kHz   ┌─────────────┐            ┌────────┐
│  client  │ ─────────────► │  MARK LIII  │ ─────────► │ Gemini │
│ (phone)  │ ◄───────────── │  headless   │ ◄───────── │  Live  │
└──────────┘   PCM 24 kHz   └─────────────┘            └────────┘
       │        JSON events        │
       └───────────────────────────┘
```

---

## 1. Authentication

One long-lived **device token**, exchanged for a short-lived **bearer token**.

```
POST /api/device-login
  Content-Type: application/json
  {"device_token": "<43 url-safe chars>"}

200 {"ok": true, "token": "<bearer>", "key": "<session key>"}
401 {"ok": false}                       ← the server does not know this device
```

Where the device token comes from: `python -m server.run_headless --pairing` on
the server prints it. It is generated once into `config/device_credentials.json`
(mode 0600) and seeded into the dashboard's own `_device_sessions` at every
boot, which is what makes it survive a restart.

| | |
|---|---|
| **HTTP** | `Authorization: Bearer <token>` |
| **WebSocket** | `?token=<url-encoded bearer>` — a WS handshake cannot carry a custom header |

The bearer lives in the server's **RAM**. A server restart invalidates it and
every socket closes with **4001**; the correct response is to call
`/api/device-login` again, not to give up. Only a `401` on device-login means
the credential itself is wrong.

`POST /api/revoke-devices` (bearer required) invalidates every paired device,
including the persistent one, until the server restarts.

**The Gemini API key never leaves the server.** The device token is authority
over JARVIS, not over the model.

---

## 2. Uplink — microphone to Gemini

```
WS /ws/phone-audio?token=<bearer>
```

| | |
|---|---|
| Direction | client → server, **binary only** |
| Encoding | PCM, signed 16-bit, **little-endian** |
| Sample rate | **16 000 Hz** |
| Channels | 1 (mono) |
| Framing | **none** — raw samples, no header, no length prefix |
| Frame size | any; the Android client sends 2048 B (1024 samples ≈ 64 ms) |

2048 bytes is not arbitrary: it is `CHUNK_SIZE` in `main.py`, the block size the
desktop microphone uses, so the server sees one format whichever microphone is
talking.

The server reads with `receive_bytes()` and queues `{"data": …, "mime_type":
"audio/pcm"}` for `_relay_phone_audio`, which hands it to Gemini. That queue
holds 200 frames and **drops when full** — no backpressure reaches the client.

Three behaviours worth knowing:

* **Connecting means "the microphone is live".** The server broadcasts it and
  silences the desktop microphone. Open this socket when streaming starts and
  close it when it ends; holding it open silently leaves MARK LIII believing a
  microphone is present.
* **After 1 s with no frame**, the server marks the phone inactive and gives the
  desktop microphone back.
* **While JARVIS is speaking, uplink audio is discarded server-side**
  (`_relay_phone_audio` checks `_is_speaking`). Voice barge-in is therefore not
  possible today — see §7.

---

## 3. Downlink — JARVIS's voice

```
WS /ws/phone-out?token=<bearer>
```

First message is **text** (JSON), everything after it is **binary**:

```json
{"type":"audio_format","sample_rate":24000,"channels":1,"encoding":"pcm_s16le"}
```

| | |
|---|---|
| Direction | server → client |
| Encoding | PCM, signed 16-bit, little-endian |
| Sample rate | **announced**, currently 24 000 Hz (`RECEIVE_SAMPLE_RATE`) |
| Channels | 1 (mono) |
| Framing | none; batches of up to ~9600 B (≈200 ms) |

**Build the player from the announced rate, not from 24000.** The value comes
from `main.py`; hardcoding it means wrong-pitch audio the day it changes.

These are the same bytes `_play_audio` would have written to a desktop sound
card — `server/audio_bridge.py` stands in for that sound card and publishes them
here instead. So the ~200 ms batching, and the fact that an interrupt stops the
flow mid-sentence, are `main.py`'s behaviour, not this layer's.

A slow client loses the **oldest** queued audio, never the newest: a gap is
recoverable, a conversation five seconds behind is not.

---

## 4. Events

```
WS /ws?token=<bearer>
```

JSON both ways. On connect the server replays its **last 50** messages, so a
reconnecting client gets recent context rather than a blank screen.

### Server → client

| `type` | Payload | Since |
|---|---|---|
| `log` | `speaker` (`user`\|`jarvis`), `text`, `ts` | dashboard |
| `status` | `state` (`active`\|`sleeping`) | dashboard |
| `sys` | `text` — system line | dashboard |
| `file_received` | `name`, `size`, `saved_to` | dashboard |
| `jarvis_state` | `state` (`LISTENING`\|`THINKING`\|`SPEAKING`\|`SLEEPING`) | headless |
| `content` | `title`, `text` — what the desktop shows under the HUD | headless |
| `confirm` | `id`, `title`, `detail`, `timeout_s` — see §4.1 | headless |
| `confirm_hide` | *(no payload)* — the request is over, take the banner down | headless |
| `notification` | `id`, `priority`, `title`, `text`, `ts` — see §4.2 | V2 |

`jarvis_state` is finer-grained than `status` and exists as a separate type on
purpose: the existing web client only understands `active`/`sleeping`, and
pushing new values through `status` would make it display the wrong thing.

### Client → server

```json
{"type": "command",  "text": "what is the weather"}
{"type": "interrupt"}
{"type": "confirmation_response", "id": "<id from `confirm`>", "confirmed": true}
{"type": "device_state", "state": {"battery_percent": 42, "headset": true, …}}
```

An unrecognised `type` is ignored and the socket stays open, so a client may be
older or newer than the server it is talking to.

#### `device_state`

What the phone can see about its own situation, read by the optional `context/`
package so JARVIS can decide whether now is a good moment to speak at all.

Every field inside `state` is optional, and the server **merges** each report
into what it already holds — so a report carrying only a battery level does not
erase the headset state an earlier one established. A permission the phone does
not hold is a field it omits, not an error.

| Field | Meaning |
|---|---|
| `battery_percent`, `battery_charging` | 0–100, and whether it is on a charger |
| `screen_on`, `idle_seconds` | interactive now, and how long the screen has been dark |
| `headset`, `headset_name`, `bluetooth_devices` | audio outputs currently attached |
| `ringer`, `dnd` | `NORMAL`\|`VIBRATE`\|`SILENT`, and Do-Not-Disturb |
| `activity`, `activity_confidence` | `STILL`\|`WALKING`\|`IN_VEHICLE`…, 0–100 |
| `network`, `place` | `wifi`\|`cellular`\|`offline`, and a free label |

The whole record **expires after 300 s**, so a phone that drops off Wi-Fi cannot
leave a stale "headset connected" behind it. A client must therefore re-send
even when nothing has changed; the Android client does so every 120 s. A clean
disconnect drops the record immediately rather than waiting for the timer.

Sending this to a server without `context/` installed is harmless — the branch
in `dashboard/server.py` swallows it.

#### `interrupt`

Stop JARVIS mid-sentence. No payload: *stop talking* has no parameters, and a
payload would only be something to validate.

It reaches the same `interrupt()` the desktop HUD button calls. Two things are
discarded, and a client should expect both:

| | |
|---|---|
| audio not yet produced | the queue feeding the player is drained |
| audio already produced | **every frame still queued for every `/ws/phone-out` listener is dropped** |

The second one is not an optimisation. Gemini generates faster than real time —
measured at 1.52 s of speech delivered in 0.41 s — so by the time anyone asks for
silence, a complete answer can already be in flight. Up to ~40 s of it. Without
dropping that, an interrupt stops the *source* and the phone keeps speaking the
abandoned answer for another half minute.

**A client must also clear its own playback buffer** when it sends this. The
server cannot reach into it, and whatever the client has already received will
otherwise still be spoken.

After an interrupt: the abandoned turn does not resume, and the next thing the
user says is a new turn. Interrupting when nothing is being said is harmless.

#### `confirmation_response`

The answer to a `confirm` event. It carries a **decision, never an action** — see
§4.1.

### 4.1 The confirmation gate

Irreversible actions (`core/confirm.py`) do not run until a human says so. On a
desktop that is a HUD button; here it is an event:

```
MARK LIII  ──confirm{id,title,detail,timeout_s}──►  client
                                                      │  CONFIRM / CANCEL
MARK LIII  ◄──confirmation_response{id,confirmed}─────┘
```

The client displays and decides. It never executes. What runs — and whether
anything runs at all — is decided on the server against its own pending request.

`id` is mandatory in the answer, and this is the whole point of it: a phone can
hold a stale banner across an expiry and a new request in a way a HUD never
could. An answer is applied **only** if its `id` is the request still waiting.
The server refuses, silently and without side effects:

| The client says | What happens |
|---|---|
| an `id` that is not the pending one | ignored — and the live request stays waiting |
| the same `id` twice | the second is ignored; the action runs once |
| an `id` whose request has expired | ignored, nothing runs |
| an answer with nothing pending | ignored |

**Expiry is `timeout_s` seconds from the `confirm` event** (90 s as shipped). A
client should take its banner down when the timer runs out rather than leave a
live-looking button on screen; either way an answer sent after it will do
nothing. `confirm_hide` also means take it down — the request has been resolved
or cancelled somewhere else.

With no client connected, a confirmation is announced, surfaced in `/status`,
and expires unconfirmed. That is the safe failure and it is the old behaviour.

### 4.2 Notifications

```json
{"type":"notification","id":"9f2a…","priority":"IMPORTANT",
 "title":"Rendez-vous","text":"Ça commence dans 15 minutes.","ts":1758100000.0}
```

Something JARVIS decided belongs in the notification shade rather than in
speech. Produced by `server/notify.py`; classified before it gets here.

**The priority is already decided.** `context/policy.py` weighed the message
against the user's situation — asleep, driving, in a meeting, headset connected
— using signals the phone reported but the phone does not reason about. A client
**presents** this decision and must not re-rank it, or two policies end up
disagreeing about one message and the one with less information wins.

| `priority` | What the client should do |
|---|---|
| `CRITICAL` | interrupt: heads-up banner and sound |
| `IMPORTANT` | an ordinary notification |
| `USEFUL` | a quiet one — no sound, no banner |
| `TRIVIAL` | **never sent.** The server refuses it at the door |

#### Delivery, and why the same notification may arrive twice

`broadcast()` fans out to whoever is connected and keeps no obligation, so a
notification raised while the phone was out of coverage would simply be lost.
Two mechanisms prevent that, and both resend:

1. the ordinary **last-50 replay** every client gets on connect, and
2. the hub's own **pending list** — every notification still inside its TTL
   (6 h), re-offered in full the moment a client connects.

So a client will receive duplicates. This is by design and is not something the
server tries to avoid: losing one matters, repeating one does not, and the
server has no way to know what was displayed. There is **no acknowledgement in
this protocol** and none is planned.

#### `id` is what makes that safe

**A client must remember the ids it has shown, and drop a repeat.** It is the
only participant that knows what reached the user. Two properties matter:

* the memory must **survive a restart** — Android is most likely to have
  restarted the service at exactly the moment a replay arrives;
* it must hold **more ids than the server's pending list** (50), so an id cannot
  be forgotten locally while it is still being re-offered.

The Android client keeps the last 200 in `SharedPreferences`
(`JarvisNotificationManager`), and `server/selftest.py` fails if those two
numbers ever cross.

A **dismissed** notification whose id is still remembered is not re-posted:
dismissal is the user saying they have read it, and a replay is not new
information.

#### Producing one

```
POST /api/notify          Authorization: Bearer <token>
  {"priority": "IMPORTANT", "title": "…", "text": "…"}

200 {"ok": true, "id": "9f2a…", "priority": "IMPORTANT"}
400 {"ok": false, "reason": "refused (empty text, or TRIVIAL)"}
401 {"error": "Unauthorized"}
```

Same bearer as `/api/command`, which already runs arbitrary commands on the
host — raising a notification is strictly less authority than that, so this adds
no new privilege. It is also how the phone side is tested end to end without
waiting for a producer:

```bash
curl -X POST http://<host>:8000/api/notify \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"priority":"CRITICAL","title":"Test","text":"Ceci est un test."}'
```

Counters (not content) appear under `notifications` in `/status`.

Or `{"type":"command","enc":"<base64>"}` with the text encrypted AES-256-CBC
under `SHA256(session_key + b"JARVIS-DASHBOARD-v1")`, IV prepended, PKCS7.

**The Android client sends `text`.** The `enc` layer protects *commands only* —
`/ws/phone-audio` and `/ws/phone-out` carry raw PCM with no application-level
encryption at all. So the tunnel has to be trusted either way, and a second lock
on one of three doors is not what makes this safe. See §6.

A command wakes JARVIS if it is asleep. `POST /api/wake` exists but is **a
no-op**: `main.py` never registers a wake callback. Do not rely on it.

---

## 5. Heartbeat and reconnection

There is **no application-level heartbeat**, and none is needed:
`uvicorn[standard]` sends WebSocket pings on its own and a conforming client
answers automatically.

What a client must add is a ping in the **other** direction. A phone that loses
Wi-Fi gets no TCP reset — the socket goes quiet and looks healthy for ever.
OkHttp's `pingInterval(20s)` is the whole mechanism in the Android client.

Reconnection policy (`ReconnectManager`), three categories of failure:

| Situation | Response |
|---|---|
| Ordinary close, server restart, network gone | reconnect, growing backoff 1→2→4→8→15→30 s, ±20 % jitter |
| Close **4001** | bearer is stale — log in again, do not treat as fatal |
| **401** on `/api/device-login` | the credential is refused — stop and say so |

Two rules that are less obvious and both come from the old Jarvis Android's
microphone restart loop:

* **A connection that lived less than 3 s does not reset the backoff.** It is
  one failure in two acts, and treating it as a success produces a tight retry
  loop that reads like a working reconnect in the logs.
* **The attempt count is capped, not the delay.** An assistant in a pocket must
  still be there after six hours out of coverage, so only a refused credential
  stops the loop.

A `ConnectivityManager` callback retries immediately when the network returns,
rather than sitting out a backoff the returning Wi-Fi has made pointless.

---

## 6. Transport security — read this one

`/ws/phone-audio` and `/ws/phone-out` carry **unencrypted PCM**. MARK LIII's
AES layer does not touch them. Unless `config/certs/` is populated, the server
speaks plain HTTP.

**The tunnel is what protects the audio.** Run the server on a Tailscale
tailnet: WireGuard encrypts everything between phone and VPS, and the tailnet is
not reachable from the internet. Speaking to JARVIS over plain HTTP on an
untrusted network means speaking in the clear.

The Android client enforces this in `network_security_config.xml`: cleartext is
allowed for `*.ts.net` and loopback only. The **debug** variant allows
everything so a first test on the LAN needs no edit — and is never shipped.

---

## 7. Known gaps

| Gap | Consequence | Where a fix would go |
|---|---|---|
| ~~No interrupt endpoint~~ | **Fixed.** Not by a route: `interrupt` travels on `/ws` and reaches the same `ui.on_interrupt` the HUD button calls | — |
| Uplink dropped while speaking | No voice barge-in | server-side, `_relay_phone_audio` — would modify `main.py` |
| `POST /api/wake` is inert | Cannot wake without sending a command | `dashboard.set_wake_callback` from `server/run_headless.py` |
| ~~No confirm button~~ | **Fixed.** `confirmation_response` is the reply channel; see §4.1 | — |
| Port 8000 hardcoded | `PORT` is a constant in `dashboard/server.py` | would modify an existing file |
| Gemini drops every 2–3 min | the Live session closes with 1008 and reconnects; inaudible, but constant | a dedicated phase — see [`../server/README.md`](../server/README.md#known-bugs) |
