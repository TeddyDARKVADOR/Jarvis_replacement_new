package com.jarvis.net

import android.util.Log
import com.jarvis.auth.AuthManager
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * The three sockets, and nothing else.
 *
 * This class is transport only: it knows how to open, close, send and receive.
 * It holds no retry policy, no backoff and no opinion about when to try again —
 * that is [ReconnectManager]. Keeping the two apart is what makes the policy
 * testable and the transport boring.
 *
 * ```
 *   /ws              events, JSON        open while connected
 *   /ws/phone-out    PCM 24 kHz in       open while connected
 *   /ws/phone-audio  PCM 16 kHz out      open only while the mic is live
 * ```
 *
 * **Why the microphone socket is opened and closed, not held.** The server
 * treats a connection on /ws/phone-audio as "the phone microphone is live" and
 * broadcasts it (dashboard/server.py:616); `_relay_phone_audio` then decides the
 * phone is streaming and silences the desktop microphone. Holding that socket
 * open while saying nothing would leave MARK LIII permanently convinced a
 * microphone is present. Opening it for the duration of the interaction says
 * exactly what is true.
 *
 * **Generations.** Every connect() bumps a counter that each socket callback
 * carries. A callback from an older generation is dropped on the floor. This is
 * lifted straight from the old Jarvis's voice machine, where the same problem —
 * a late callback from a session that had already been replaced — was what let
 * a dead session report itself as alive. Here it is what stops our own
 * deliberate disconnect() from being reported upward as a network failure.
 */
class JarvisClient(
    private val auth: AuthManager,
    private val listener: Listener,
) {

    interface Listener {
        /** Events and downlink are both up. */
        fun onConnected()

        /** The link is gone. [fatal] means retrying cannot help (bad token). */
        fun onDisconnected(reason: String, fatal: Boolean)

        /** One JSON message from /ws. */
        fun onEvent(json: JSONObject)

        /** One PCM batch from /ws/phone-out. */
        fun onAudio(pcm: ByteArray)

        /** The sample rate the server announced before sending any audio. */
        fun onAudioFormat(sampleRate: Int)

        /** Uplink frame dropped because the socket was behind. */
        fun onUplinkDropped()
    }

    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        // The single most important line in this file for a phone. Losing Wi-Fi
        // does not close a TCP socket; it leaves it silent. Without a ping the
        // client believes it is connected until something tries to send — which,
        // if JARVIS happens not to be speaking, may be minutes.
        .pingInterval(Protocol.PING_SECONDS, TimeUnit.SECONDS)
        .build()

    private val generation = AtomicInteger(0)

    @Volatile private var events: WebSocket? = null
    @Volatile private var downlink: WebSocket? = null
    @Volatile private var uplink: WebSocket? = null

    @Volatile private var eventsOpen = false
    @Volatile private var downlinkOpen = false
    @Volatile private var announced = false

    val isMicOpen: Boolean get() = uplink != null

    /**
     * Blocking: performs the HTTP device-login, then opens the two permanent
     * sockets. Call from a background thread.
     *
     * Returns normally once the sockets have been *requested*; [Listener
     * .onConnected] fires when both are actually open.
     */
    @Throws(Exception::class)
    fun connect() {
        val gen = generation.incrementAndGet()
        closeSockets("reconnecting")

        val session = auth.login()          // throws AuthRejected on a bad token
        val ep = auth.endpoint

        eventsOpen = false
        downlinkOpen = false
        announced = false

        events = http.newWebSocket(
            Request.Builder().url(ep.events(session.bearer)).build(),
            SocketListener(gen, Channel.EVENTS),
        )
        downlink = http.newWebSocket(
            Request.Builder().url(ep.audioDownlink(session.bearer)).build(),
            SocketListener(gen, Channel.DOWNLINK),
        )
    }

    fun disconnect() {
        generation.incrementAndGet()        // every in-flight callback is now stale
        closeSockets("client disconnect")
    }

    /** Opens /ws/phone-audio. Returns false if there is no session to open it with. */
    fun openMic(): Boolean {
        if (uplink != null) return true
        val session = auth.session ?: return false
        uplink = http.newWebSocket(
            Request.Builder().url(auth.endpoint.micUplink(session.bearer)).build(),
            SocketListener(generation.get(), Channel.UPLINK),
        )
        return true
    }

    fun closeMic() {
        uplink?.close(NORMAL_CLOSE, "mic off")
        uplink = null
    }

    /**
     * Send one PCM frame. Returns false when the frame was dropped.
     *
     * Drop-newest here, unlike the server's drop-oldest for playback, and the
     * asymmetry is deliberate: a late frame of the user's voice is worse than a
     * missing one — it would arrive after the sentence it belongs to and make
     * Gemini's turn detection wrong. Playback has the opposite need, which is
     * why `server/audio_bridge.py` drops the other end of its queue.
     */
    fun sendAudio(frame: ByteArray, length: Int): Boolean {
        val ws = uplink ?: return false
        if (ws.queueSize() > MAX_UPLINK_QUEUE_BYTES) {
            listener.onUplinkDropped()
            return false
        }
        return ws.send(frame.toByteString(0, length))
    }

    /** A typed command, on the same channel the web dashboard uses. */
    fun sendText(text: String): Boolean {
        val ws = events ?: return false
        val msg = JSONObject()
            .put("type", Protocol.CMD_COMMAND)
            .put("text", text)
        return ws.send(msg.toString())
    }

    // ── internals ────────────────────────────────────────────────────────────

    private enum class Channel { EVENTS, DOWNLINK, UPLINK }

    private fun closeSockets(reason: String) {
        listOf(uplink, downlink, events).forEach {
            try {
                it?.close(NORMAL_CLOSE, reason)
            } catch (_: Exception) {
                // Already closing or cancelled; there is nothing to recover.
            }
        }
        uplink = null
        downlink = null
        events = null
        eventsOpen = false
        downlinkOpen = false
    }

    private inner class SocketListener(
        private val gen: Int,
        private val channel: Channel,
    ) : WebSocketListener() {

        private val stale: Boolean get() = gen != generation.get()

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (stale) {
                webSocket.close(NORMAL_CLOSE, "stale")
                return
            }
            when (channel) {
                Channel.EVENTS -> eventsOpen = true
                Channel.DOWNLINK -> downlinkOpen = true
                Channel.UPLINK -> Unit
            }
            if (eventsOpen && downlinkOpen) listener.onConnected()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (stale) return
            try {
                val json = JSONObject(text)
                // The first frame on /ws/phone-out announces the PCM format, so
                // the player is built from what the server actually plays at
                // rather than from a constant that could drift.
                if (channel == Channel.DOWNLINK && json.optString("type") == "audio_format") {
                    announced = true
                    listener.onAudioFormat(
                        json.optInt("sample_rate", Protocol.DOWNLINK_SAMPLE_RATE_DEFAULT)
                    )
                    return
                }
                listener.onEvent(json)
            } catch (e: Exception) {
                Log.w(TAG, "unparsable message on $channel: ${e.message}")
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (stale || channel != Channel.DOWNLINK) return
            if (!announced) {
                // Audio before the format frame should not happen, but assuming
                // silence would be worse than assuming the documented default.
                announced = true
                listener.onAudioFormat(Protocol.DOWNLINK_SAMPLE_RATE_DEFAULT)
            }
            listener.onAudio(bytes.toByteArray())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(NORMAL_CLOSE, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (stale) return
            if (code == Protocol.CLOSE_UNAUTHORISED) {
                auth.forgetSession()
                // Not fatal: the bearer is server-RAM state, so a server that
                // restarted simply does not know this one any more. Logging in
                // again is exactly the right response — and if the *device*
                // token is what is wrong, AuthManager.login will say so with
                // AuthRejected, which is where the fatal verdict belongs.
                report("unauthorised (code 4001) — logging in again", fatal = false)
                return
            }
            report("$channel closed ($code ${reason.ifBlank { "no reason" }})", fatal = false)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (stale) return
            report("$channel failed: ${t.javaClass.simpleName}: ${t.message}", fatal = false)
        }

        private fun report(reason: String, fatal: Boolean) {
            if (channel == Channel.UPLINK) {
                // The microphone socket dying is not the link dying: it is
                // opened and closed on purpose all day. Give the microphone back
                // and let the next interaction reopen it.
                uplink = null
                Log.i(TAG, "uplink: $reason")
                return
            }
            closeSockets("link lost")
            listener.onDisconnected(reason, fatal)
        }
    }

    private companion object {
        const val TAG = "JarvisClient"
        const val NORMAL_CLOSE = 1000

        // ~2 s of 16 kHz PCM. Past this the socket is not merely slow, it is
        // broken, and every extra frame adds delay to a conversation.
        const val MAX_UPLINK_QUEUE_BYTES = 64 * 1024L
    }
}
