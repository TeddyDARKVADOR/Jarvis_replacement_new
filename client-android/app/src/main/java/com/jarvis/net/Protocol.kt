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
    const val EV_CONFIRM = "confirm"       // {"title","detail"}

    // Client → server. `text` is accepted in the clear; `enc` carries the same
    // string AES-256-CBC-encrypted under the session key from device-login.
    // The prototype sends `text`: the AES layer protects commands only and does
    // nothing for the audio sockets, so it is the tunnel that has to be
    // trusted either way. Implementing `enc` adds a second lock on one of three
    // doors.
    const val CMD_COMMAND = "command"

    /** WebSocket close code the server uses to refuse a bad token. */
    const val CLOSE_UNAUTHORISED = 4001
}
