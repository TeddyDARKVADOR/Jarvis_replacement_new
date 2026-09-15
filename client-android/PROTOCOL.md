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
| `confirm` / `confirm_hide` | `title`, `detail` | headless |

`jarvis_state` is finer-grained than `status` and exists as a separate type on
purpose: the existing web client only understands `active`/`sleeping`, and
pushing new values through `status` would make it display the wrong thing.

### Client → server

```json
{"type": "command", "text": "what is the weather"}
```

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
| No interrupt endpoint | Cannot cut JARVIS off from the phone; `interrupt()` is wired to a desktop button only | a new route in `server/api.py`, calling `ui.on_interrupt` |
| Uplink dropped while speaking | No voice barge-in | server-side, `_relay_phone_audio` — would modify `main.py` |
| `POST /api/wake` is inert | Cannot wake without sending a command | `dashboard.set_wake_callback` from `server/run_headless.py` |
| No confirm button | Irreversible actions expire unconfirmed after 90 s | `confirm` event is already sent; needs a reply channel |
| Port 8000 hardcoded | `PORT` is a constant in `dashboard/server.py` | would modify an existing file |
| Gemini drops every 2–3 min | the Live session closes with 1008 and reconnects; inaudible, but constant | a dedicated phase — see [`../server/README.md`](../server/README.md#known-bugs) |
