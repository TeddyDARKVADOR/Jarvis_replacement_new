package com.jarvis.net

import java.net.URLEncoder

/**
 * The wire contract with MARK LIII — read off the server, not invented here.
 *
 * Every endpoint below already existed before this app did. Three of them ship
 * with the desktop assistant (`dashboard/server.py`), two were added by the
 * headless layer (`server/api.py`). Nothing new was defined for Android, and
 * nothing here is a second protocol layered on top of MARK LIII's own.
 *
 * ```
 *  POST /api/device-login   {"device_token"} → {"ok","token","key"}
 *  GET  /health             liveness, no auth
 *  GET  /status             Bearer <token>
 *  WS   /ws                 ?token=   JSON events, both directions
 *  WS   /ws/phone-audio     ?token=   binary PCM 16 kHz  →  Gemini
 *  WS   /ws/phone-out       ?token=   binary PCM 24 kHz  ←  Gemini
 * ```
 *
 * **Why the token is a query parameter on the sockets and a header on HTTP.**
 * Not a choice — a WebSocket handshake cannot carry a custom header from a
 * browser, so `dashboard/server.py` reads `?token=` on /ws and /ws/phone-audio.
 * Matching it on /ws/phone-out means one convention for all three sockets.
 */
data class ServerEndpoint(
    val host: String,
    val port: Int = 8000,
    /** MARK LIII serves TLS only when `config/certs/` is populated. Over
     *  Tailscale the tunnel already encrypts; see network_security_config.xml. */
    val useTls: Boolean = false,
) {
    private val httpScheme get() = if (useTls) "https" else "http"
    private val wsScheme get() = if (useTls) "wss" else "ws"

    val httpBase: String get() = "$httpScheme://$host:$port"
    val wsBase: String get() = "$wsScheme://$host:$port"

    val deviceLogin: String get() = "$httpBase/api/device-login"
    val health: String get() = "$httpBase/health"
    val status: String get() = "$httpBase/status"

    fun events(bearer: String) = "$wsBase/ws?token=${bearer.enc()}"
    fun micUplink(bearer: String) = "$wsBase/ws/phone-audio?token=${bearer.enc()}"
    fun audioDownlink(bearer: String) = "$wsBase/ws/phone-out?token=${bearer.enc()}"

    // ── device identity and routing (server/device_api.py) ───────────────────
    //
    // Two routes added beside the five above, never replacing them. A server
    // that predates them answers 404, the device channel never opens, and the
    // app behaves exactly as it did before — which is how this ships without
    // having to update both ends at the same moment.
    val deviceRegister: String get() = "$httpBase/api/device-register"

    fun deviceChannel(bearer: String, deviceId: String) =
        "$wsBase/ws/device?token=${bearer.enc()}&device_id=${deviceId.enc()}"

    val isUsable: Boolean get() = host.isNotBlank() && port in 1..65535

    private fun String.enc(): String = URLEncoder.encode(this, "UTF-8")
}

object Protocol {

    // ── Uplink: phone microphone → MARK LIII → Gemini ────────────────────────
    //
    // 16 kHz, mono, signed 16-bit little-endian, raw. No header, no framing,
    // no container: `dashboard/server.py` does receive_bytes() and hands the
    // buffer straight to Gemini as {"mime_type": "audio/pcm"}. It is also what
    // main.py's own desktop microphone sends, so the server sees one format
    // whichever microphone is talking.
    const val UPLINK_SAMPLE_RATE = 16_000

    // 1024 samples = 2048 bytes ≈ 64 ms, which is main.py's CHUNK_SIZE exactly.
    // Smaller frames would multiply syscalls and WebSocket overhead for no
    // latency gain — Gemini's turn detection works on speech, not on frames.
    const val UPLINK_FRAME_SAMPLES = 1024
    const val UPLINK_FRAME_BYTES = UPLINK_FRAME_SAMPLES * 2

    // ── Downlink: Gemini → MARK LIII → phone speaker ─────────────────────────
    //
    // 24 kHz mono PCM16. Announced by the server in the first (text) frame of
    // /ws/phone-out, so this is only the value to assume until it arrives:
    //   {"type":"audio_format","sample_rate":24000,"channels":1,
    //    "encoding":"pcm_s16le"}
    // Arrives in batches of up to ~9600 bytes (≈200 ms), the size _play_audio
    // accumulates before writing.
    const val DOWNLINK_SAMPLE_RATE_DEFAULT = 24_000

    const val CHANNELS = 1

    // ── Heartbeat ────────────────────────────────────────────────────────────
    //
    // There is no application-level heartbeat in the protocol, and none is
    // added: uvicorn[standard] pings on its own, and OkHttp answers those
    // automatically. What matters is the OTHER direction — a phone that loses
    // Wi-Fi gets no TCP reset, so without our own ping the socket looks healthy
    // forever. OkHttp's pingInterval is the whole mechanism (JarvisClient).
    const val PING_SECONDS = 20L

    // ── Message types seen on /ws ────────────────────────────────────────────
    //
    // Server → client. The first four predate this app (they drive the web
    // dashboard); the last three come from the headless UI.
    const val EV_LOG = "log"               // {"speaker":"user"|"jarvis","text","ts"}
    const val EV_STATUS = "status"         // {"state":"active"|"sleeping"}
    const val EV_SYS = "sys"               // {"text"} — system line
    const val EV_FILE = "file_received"    // {"name","size","saved_to"}
    const val EV_JARVIS_STATE = "jarvis_state"  // {"state":"LISTENING"|…}
    const val EV_CONTENT = "content"       // {"title","text"}
    const val EV_CONFIRM = "confirm"       // {"id","title","detail","timeout_s"}
    const val EV_CONFIRM_HIDE = "confirm_hide"  // no payload — take the banner down

    /** Something JARVIS decided belongs in the notification shade, not in speech.
     *
     *  `{"id","priority","title","text","ts"}` — see `server/notify.py`. The
     *  priority is already decided against the user's situation by
     *  `context/policy.py`; the client presents it and does not re-rank it.
     *
     *  **Re-sent on purpose.** The server re-offers every notification still
     *  inside its TTL whenever a client connects, so one that arrived while the
     *  phone was out of coverage is not lost. `id` is what makes that safe:
     *  [com.jarvis.notification.JarvisNotificationManager] remembers what it has
     *  shown, across restarts, and drops a repeat. A client that ignores `id`
     *  will show duplicates. */
    const val EV_NOTIFICATION = "notification"

    // Client → server. `text` is accepted in the clear; `enc` carries the same
    // string AES-256-CBC-encrypted under the session key from device-login.
    // The prototype sends `text`: the AES layer protects commands only and does
    // nothing for the audio sockets, so it is the tunnel that has to be
    // trusted either way. Implementing `enc` adds a second lock on one of three
    // doors.
    const val CMD_COMMAND = "command"

    /** Stop JARVIS mid-sentence. No payload — "stop talking" has no parameters.
     *
     *  The server drops what it has queued for us, but it cannot reach into
     *  this process: the client must also call `AudioPlayer.flush()`, or the
     *  audio already received goes on playing and the button looks broken. */
    const val CMD_INTERRUPT = "interrupt"

    /** The answer to [EV_CONFIRM]: `{"id", "confirmed"}`.
     *
     *  `id` is mandatory and must be the one that arrived with the request. The
     *  server applies an answer only to the request it names — a banner still on
     *  screen after an expiry, or after the request was replaced, cannot confirm
     *  something the user never read. Sending a decision is all this does; the
     *  action itself runs on the server or not at all. */
    const val CMD_CONFIRMATION_RESPONSE = "confirmation_response"

    /** What this phone can see about its own situation: `{"state": {...}}`.
     *
     *  Read by the server's optional `context/` package, which decides from it
     *  whether JARVIS may speak right now (see `context/README.md`). Every
     *  field inside `state` is optional and the server merges what arrives into
     *  what it already had, so a phone that cannot read a signal omits it
     *  rather than sending a placeholder — and an older phone sending half the
     *  fields is a supported case, not a version mismatch.
     *
     *  Unhandled by a server without `context/` installed: the branch in
     *  `dashboard/server.py` swallows it. Sending it is therefore always safe. */
    const val CMD_DEVICE_STATE = "device_state"

    /** WebSocket close code the server uses to refuse a bad token. */
    const val CLOSE_UNAUTHORISED = 4001

    // ── /ws/device ───────────────────────────────────────────────────────────
    //
    // Why commands go here when it is open: `/ws` hands the server a bare
    // string. The token that authenticated the socket is discarded in
    // dashboard/server.py before the command reaches Gemini, so by then nothing
    // says which machine spoke — and "ouvre le navigateur" from this phone is
    // indistinguishable from the same words typed on the PC. On this channel
    // the origin travels with the command, which is what lets the server run it
    // HERE rather than asking.

    /** This device's type, as the server's DeviceType enum spells it. */
    const val DEVICE_TYPE = "android"

    /**
     * Capabilities this client can actually execute: none, today.
     *
     * Deliberately empty rather than aspirational. A capability is a promise
     * the server routes on — declaring `phone.camera` before it exists would
     * send camera commands to a client that can only refuse them, on a device
     * the user deliberately chose. They get added one at a time, as they land.
     */
    val CAPABILITIES: List<String> = emptyList()

    const val EV_DEVICE_COMMAND = "device_command"
    const val CMD_DEVICE_RESULT = "device_result"
    const val CMD_MIC_STATE = "mic"
}
