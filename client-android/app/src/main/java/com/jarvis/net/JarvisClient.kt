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

    /**
     * The routed-command channel, when the server has one.
     *
     * Null against an Oracle that predates device routing, and everything keeps
     * working — see [Protocol.deviceChannel]. It is the *only* optional socket:
     * a failure to open it is never reported as a disconnection, because
     * identity is a refinement and the assistant itself is not.
     */
    @Volatile private var deviceChannel: WebSocket? = null

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

        // Declare who we are, then open the channel that carries it. Both are
        // best-effort: `registerDevice` swallows a 404 and returns false, and
        // `onConnected` does not wait on this socket — so a server without
        // device routing produces exactly the behaviour it did before.
        if (auth.registerDevice(session.bearer)) {
            deviceChannel = http.newWebSocket(
                Request.Builder()
                    .url(ep.deviceChannel(session.bearer, auth.deviceId))
                    .build(),
                SocketListener(gen, Channel.DEVICE),
            )
        }
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
        reportMic(true)
        return true
    }

    fun closeMic() {
        uplink?.close(NORMAL_CLOSE, "mic off")
        uplink = null
        reportMic(false)
    }

    /**
     * Tell the server the microphone opened or closed.
     *
     * A voice turn carries no text for the server to read an origin from, so
     * the open microphone is what says who is about to speak. Without it,
     * "ouvre le navigateur" spoken into this phone would reach Gemini with no
     * origin at all and the server would have to ask which device it meant.
     */
    private fun reportMic(open: Boolean) {
        val ws = deviceChannel ?: return
        try {
            ws.send(
                JSONObject()
                    .put("type", Protocol.CMD_MIC_STATE)
                    .put("open", open)
                    .toString()
            )
        } catch (_: Exception) {
            // Best effort: losing the hint costs a clarifying question, never
            // the microphone.
        }
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

    /**
     * A typed command.
     *
     * Sent on the device channel when it is open, and on `/ws` otherwise. The
     * difference is the origin: `/ws` hands the server a bare string and the
     * token that authenticated the socket is discarded before the command
     * reaches Gemini, so nothing downstream can tell this phone from the PC.
     * On the device channel the origin travels with the command, which is what
     * makes "ouvre le navigateur" open a browser HERE.
     */
    fun sendText(text: String): Boolean {
        val msg = JSONObject()
            .put("type", Protocol.CMD_COMMAND)
            .put("text", text)
        deviceChannel?.let { if (it.send(msg.toString())) return true }
        val ws = events ?: return false
        return ws.send(msg.toString())
    }

    /**
     * Stop JARVIS mid-sentence.
     *
     * Only half of an interrupt: this tells the server to stop producing and to
     * throw away what it has queued for us. Audio already delivered is sitting
     * in this process, and the caller must clear it — see
     * [com.jarvis.audio.AudioPlayer.flush]. Whoever owns both does both.
     */
    fun sendInterrupt(): Boolean {
        val ws = events ?: return false
        return ws.send(JSONObject().put("type", Protocol.CMD_INTERRUPT).toString())
    }

    /**
     * Answer a pending confirmation.
     *
     * [id] must be the one that came with the request. A decision travels; an
     * action does not. If the request has expired or been replaced, the server
     * discards this and nothing happens — which is the intended outcome, not a
     * failure to handle here.
     */
    fun sendConfirmation(id: String, confirmed: Boolean): Boolean {
        val ws = events ?: return false
        val msg = JSONObject()
            .put("type", Protocol.CMD_CONFIRMATION_RESPONSE)
            .put("id", id)
            .put("confirmed", confirmed)
        return ws.send(msg.toString())
    }

    // ── internals ────────────────────────────────────────────────────────────

    private enum class Channel { EVENTS, DOWNLINK, UPLINK, DEVICE }

    private fun closeSockets(reason: String) {
        listOf(uplink, downlink, events, deviceChannel).forEach {
            try {
                it?.close(NORMAL_CLOSE, reason)
            } catch (_: Exception) {
                // Already closing or cancelled; there is nothing to recover.
            }
        }
        uplink = null
        downlink = null
        events = null
        deviceChannel = null
        eventsOpen = false
        downlinkOpen = false
    }

    /**
     * Answer a command the server routed to this phone.
     *
     * Today that answer is always a refusal, and that is correct rather than
     * unfinished: [Protocol.CAPABILITIES] is empty, so the server should never
     * send one. Replying instead of ignoring it means a misrouted command fails
     * *visibly*, with a sentence JARVIS can say, rather than timing out forty-
     * five seconds later with nothing to explain it.
     *
     * This is the second barrier the design asks for. When phone capabilities
     * land, they get dispatched here — one `when` branch each.
     */
    private fun answerDeviceCommand(json: JSONObject) {
        val ws = deviceChannel ?: return
        val id = json.optString("id")
        val action = json.optString("action")
        val target = json.optString("target_device_id")

        val result = when {
            target.isNotBlank() && target != auth.deviceId ->
                "Refusé : cette commande est destinée à $target, pas à ce téléphone."
            action !in Protocol.CAPABILITIES ->
                "Refusé : « $action » ne fait pas partie des capacités de ce téléphone."
            else ->
                "Refusé : « $action » n'est pas encore implémenté sur ce téléphone."
        }
        try {
            ws.send(
                JSONObject()
                    .put("type", Protocol.CMD_DEVICE_RESULT)
                    .put("id", id)
                    .put("result", result)
                    .toString()
            )
        } catch (_: Exception) {
            // The socket went away mid-answer; the server's own timeout covers it.
        }
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
                // Neither gates CONNECTED. The microphone is not part of being
                // connected, and the device channel is optional by design.
                Channel.UPLINK, Channel.DEVICE -> Unit
            }
            if (channel == Channel.DEVICE) reportMic(uplink != null)
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
                // The device channel carries addressing, not conversation.
                // Passing its traffic to `onEvent` would put "device_command"
                // through the same path as a log line and the UI would have to
                // learn to ignore it.
                if (channel == Channel.DEVICE) {
                    if (json.optString("type") == Protocol.EV_DEVICE_COMMAND) {
                        answerDeviceCommand(json)
                    }
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
            if (channel == Channel.DEVICE) {
                // Neither is the device channel. It is optional by design — a
                // server without device routing never opens it at all — so
                // tearing the whole link down when it drops would turn a
                // missing refinement into a disconnection. Commands fall back
                // to `/ws` on their own; the cost is a clarifying question.
                deviceChannel = null
                Log.i(TAG, "device channel: $reason")
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
