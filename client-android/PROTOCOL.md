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

`jarvis_state` is finer-grained than `status` and exists as a separate type on
purpose: the existing web client only understands `active`/`sleeping`, and
pushing new values through `status` would make it display the wrong thing.

### Client → server

```json
{"type": "command",  "text": "what is the weather"}
{"type": "interrupt"}
{"type": "confirmation_response", "id": "<id from `confirm`>", "confirmed": true}
```

An unrecognised `type` is ignored and the socket stays open, so a client may be
older or newer than the server it is talking to.

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

## 6.1 Device identity and routing — added, optional

Two routes added by `server/device_api.py`, beside the five above. **Nothing
here replaces anything.** A server without them answers 404 to the registration,
the channel never opens, and a client behaves exactly as this document
describes up to §6.

```
POST /api/device-register   Bearer <token>
  {"device_id", "device_type", "display_name", "capabilities", "protocol_version"}
  → {"ok": true, "device": {...}, "devices": [...]}

WS   /ws/device?token=<bearer>&device_id=<id>
```

### Why a command belongs here rather than on `/ws`

`/ws` hands the server a bare string: `dashboard/server.py` discards the token
that authenticated the socket before the command reaches Gemini. By then nothing
says which machine spoke, so "ouvre le navigateur" from the phone and from the
PC are the same nine bytes — and the server has no honest way to pick a device.

On this channel the origin travels with the command, which is what lets the
server run it **on the device that asked**. Clients send `command` here when it
is open and fall back to `/ws` when it is not; the cost of the fallback is a
clarifying question, never a wrong device.

| Direction | `type` | Payload |
|---|---|---|
| server → client | `device_command` | `id`, `target_device_id`, `action`, `parameters` |
| client → server | `device_result` | `id`, `result` — the `id` must be the one that arrived |
| client → server | `command` | `text` — same as `/ws`, but with an origin |
| client → server | `mic` | `open` — a voice turn has no text to read an origin from |
| client → server | `ping` | answered with `pong` |

### Capabilities are a promise

`capabilities` is a list of **action names** — the same names
`core/action_loader.py` discovers, never invented strings. The server routes on
them: a device that declares `computer_control` will be sent mouse commands. So
a client declares only what it can actually execute *right now*. The Android
client declares an empty list today and refuses any `device_command` it
receives, which is the correct behaviour rather than an unfinished one.

### Two barriers

The server resolves the target before dispatching; the client refuses a
`device_command` whose `target_device_id` is not its own, and refuses an action
outside its declared capabilities. A wrong-device execution therefore needs two
simultaneous faults rather than one.

---

## 7. Known gaps

| Gap | Consequence | Where a fix would go |
|---|---|---|
| No interrupt endpoint | Cannot cut JARVIS off from the phone; `interrupt()` is wired to a desktop button only | a new route in `server/api.py`, calling `ui.on_interrupt` |
| Uplink dropped while speaking | No voice barge-in | server-side, `_relay_phone_audio` — would modify `main.py` |
| `POST /api/wake` is inert | Cannot wake without sending a command | `dashboard.set_wake_callback` from `server/run_headless.py` |
| No confirm button | Irreversible actions expire unconfirmed after 90 s | `confirm` event is already sent; needs a reply channel |
| Port 8000 hardcoded | `PORT` is a constant in `dashboard/server.py` | would modify an existing file |
| Gemini drops every 2–3 min | the Live session closes with 1008 and reconnects; inaudible, but constant | a dedicated phase — see [`../server/README.md`](../server/README.md#known-bugs) |
