package com.jarvis.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.ServiceCompat
import com.jarvis.AssistantState
import com.jarvis.JarvisLog
import com.jarvis.JarvisState
import com.jarvis.LinkState
import com.jarvis.MainActivity
import com.jarvis.Message
import com.jarvis.R
import com.jarvis.audio.AudioPlayer
import com.jarvis.audio.AudioRecorder
import com.jarvis.auth.AuthManager
import com.jarvis.device.DeviceStateReporter
import com.jarvis.net.JarvisClient
import com.jarvis.net.Protocol
import com.jarvis.net.ReconnectManager
import com.jarvis.wakeword.WakeWordDetector
import com.jarvis.wakeword.WakeWordEngines
import com.jarvis.wakeword.WakeWordSelfTest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import org.json.JSONObject

/**
 * What keeps JARVIS alive when the screen goes off.
 *
 * **Why the app cannot simply record in the background.** Since Android 11 an
 * app that is not in the foreground and holds an AudioRecord does not get an
 * error — it gets *silence*. Everything appears to work and JARVIS never hears
 * anything. A foreground service of type `microphone` is the supported way to
 * be allowed to keep listening, and it costs an ongoing notification. That
 * notification is the point, not the price: the phone is holding the microphone
 * open, and the person carrying it is entitled to see that and to stop it in
 * one tap.
 *
 * **How this differs from the old Jarvis's service, deliberately.** That one
 * was `specialUse` and existed to stop Android killing the app for the CPU its
 * local llama-server child was burning; it did not protect the microphone, and
 * that app explicitly closed the microphone when it went to the background.
 * This service has the opposite job, so: type `microphone`, and START_STICKY
 * rather than START_NOT_STICKY — the old service had nothing to restart into
 * (its engine was gone), while this one is the whole client and being brought
 * back after a low-memory kill is exactly what should happen.
 *
 * **Nothing here bypasses a restriction.** It is started from a visible
 * Activity, after RECORD_AUDIO has been granted and the user has switched
 * "JARVIS 24/7" on. No boot receiver, no background start, no battery
 * exemption claimed in the manifest.
 */
class JarvisForegroundService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private lateinit var auth: AuthManager
    private lateinit var client: JarvisClient
    private lateinit var deviceState: DeviceStateReporter
    private lateinit var reconnect: ReconnectManager
    private lateinit var player: AudioPlayer
    private lateinit var recorder: AudioRecorder
    private lateinit var wakeWord: WakeWordDetector

    /** The user asked for the microphone. */
    @Volatile private var micRequested = false

    /** Audio may leave the phone (no wake word, or one that has just fired). */
    @Volatile private var gateOpen = false
    @Volatile private var gateUntil = 0L

    @Volatile private var lastNotificationText = ""

    /** Logs the first downlink frame once per service life. */
    @Volatile private var sawFirstAudio = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        auth = AuthManager(this)
        player = AudioPlayer(
            onPlayed = { JarvisState.countPlayed(it) },
            onLevel = { JarvisState.setSpeakerLevel(it) },
        )
        wakeWord = WakeWordEngines.default(
            context = this,
            enabled = auth.wakeWordEnabled,
            onScore = { JarvisState.setWakeScore(it) },
        )
        JarvisState.setWakeWord(wakeWord.name)
        if (auth.wakeWordEnabled) {
            // Runs once per service start, off the main thread: it replays a
            // fixed clip through the real pipeline and says whether this port
            // still matches the reference implementation.
            Thread({ WakeWordSelfTest.runAndReport(this) }, "jarvis-wakeword-selftest").start()
        }

        client = JarvisClient(auth, ClientEvents())
        deviceState = DeviceStateReporter(
            context = this,
            scope = scope,
            send = { client.sendDeviceState(it) },
        )
        reconnect = ReconnectManager(
            context = this,
            client = client,
            scope = scope,
            onLink = { state, error ->
                JarvisState.setLink(state, error)
                if (state == LinkState.CONNECTED) {
                    replayUntil = SystemClock.elapsedRealtime() + REPLAY_WINDOW_MS
                    // The server forgets this phone's state when the socket
                    // closes, so a fresh link starts from nothing until we
                    // speak. Do not make it wait a full tick.
                    deviceState.reportNow()
                }
                if (state != LinkState.CONNECTED) {
                    // The microphone socket does not survive a link change, and
                    // neither does anything queued for playback.
                    player.flush()
                }
                updateNotification()
            },
            onRetry = { attempt, seconds -> JarvisState.setRetry(attempt, seconds) },
            onNote = { JarvisState.log(it) },
        )

        recorder = AudioRecorder(
            onFrame = ::onMicFrame,
            onLevel = { level ->
                JarvisState.setMicLevel(level)
                // Speech refreshes the interaction window, so a wake word does
                // not expire in the middle of a long sentence.
                if (gateOpen && level > VOICE_FLOOR) {
                    gateUntil = SystemClock.elapsedRealtime() + INTERACTION_WINDOW_MS
                }
            },
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // A null intent means Android restarted us after a kill (START_STICKY).
        // Coming back listening is the whole point of being sticky.
        when (intent?.action ?: ACTION_START) {
            ACTION_START -> start()
            ACTION_STOP -> {
                stopEverything()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_MIC_ON -> startMic()
            ACTION_MIC_OFF -> stopMic()
            ACTION_INTERRUPT -> interrupt()
            ACTION_SAY -> sendText(intent?.getStringExtra(EXTRA_TEXT).orEmpty())
            ACTION_CONFIRM -> answerConfirmation(
                intent?.getStringExtra(EXTRA_CONFIRM_ID).orEmpty(),
                intent?.getBooleanExtra(EXTRA_CONFIRMED, false) ?: false,
            )
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopEverything()
        scope.cancel()
        super.onDestroy()
    }

    // ── lifecycle ────────────────────────────────────────────────────────────

    private fun start() {
        startForegroundNotification()
        JarvisState.setServiceRunning(true)
        JarvisLog.service("foreground service active (type=microphone)")
        if (!auth.isConfigured) {
            JarvisState.setLink(LinkState.ERROR, "No server or device token configured")
            JarvisState.log("Set the server address and device token first.")
            updateNotification()
            return
        }
        reconnect.start()
        deviceState.start()
    }

    private fun stopEverything() {
        JarvisLog.service("foreground service stopping")
        stopMic()
        deviceState.stop()
        reconnect.stop()
        player.stop()
        JarvisState.setServiceRunning(false)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
    }

    // ── microphone ───────────────────────────────────────────────────────────

    private fun startMic() {
        if (micRequested) return
        micRequested = true

        gateOpen = !wakeWord.gatesAudio
        JarvisState.setGateOpen(gateOpen)
        gateUntil = Long.MAX_VALUE
        wakeWord.start {
            // Fires immediately for AlwaysOpen; on a real engine, when the word
            // is heard.
            val wasClosed = !gateOpen
            gateOpen = true
            JarvisState.setGateOpen(true)
            // One short pulse, only on the rising edge: this is the moment the
            // user's voice starts leaving the phone, and it is the one thing
            // they should be told without having to look.
            if (wasClosed && wakeWord.gatesAudio) buzz(longArrayOf(0, 28))
            gateUntil = SystemClock.elapsedRealtime() + INTERACTION_WINDOW_MS
            client.openMic()
        }

        if (!recorder.start()) {
            micRequested = false
            JarvisState.setLink(LinkState.ERROR, "Microphone unavailable")
            JarvisState.log("The microphone could not be opened — another app may hold it.")
            updateNotification()
            return
        }
        if (gateOpen) client.openMic()
        JarvisState.setMicOpen(true)
        JarvisLog.mic("TX 16k PCM started — ${Protocol.UPLINK_FRAME_BYTES} bytes/frame, " +
            "gate ${if (gateOpen) "open" else "closed"}")
        updateNotification()
    }

    private fun stopMic() {
        if (!micRequested) return
        micRequested = false
        gateOpen = false
        JarvisState.setGateOpen(false)
        recorder.stop()
        wakeWord.stop()
        client.closeMic()
        JarvisState.setMicOpen(false)
        JarvisLog.mic("TX stopped")
        updateNotification()
    }

    /**
     * Stop JARVIS mid-sentence.
     *
     * Both halves, and the local one first. The server drops what it has queued
     * for us, but the audio already delivered is in this process — in the
     * player's queue and inside AudioTrack's own buffer — and nothing on the
     * server can reach it. Flushing after sending would leave a window in which
     * the phone is still speaking an answer the server has already abandoned.
     *
     * Harmless when JARVIS is not speaking: both sides are then empty.
     */
    private fun interrupt() {
        player.flush()
        buzz(longArrayOf(0, 18))
        val sent = client.sendInterrupt()
        JarvisLog.net(if (sent) "INTERRUPT sent" else "INTERRUPT not sent — no link")
        if (!sent) JarvisState.log("Interrupt: not connected.")
    }

    /**
     * Send the user's decision on a pending confirmation. Never acts on it.
     *
     * The banner is cleared either way — the request is answered from this
     * phone's point of view. Whether anything runs is the server's call, made
     * against its own pending request and its own expiry, and a `sys` line will
     * say what happened.
     */
    private fun answerConfirmation(id: String, confirmed: Boolean) {
        if (id.isBlank()) return
        JarvisState.clearConfirmation()
        val sent = client.sendConfirmation(id, confirmed)
        if (!sent) {
            // Nothing was decided anywhere: the socket is down, so the server
            // still holds the request and will expire it on its own.
            JarvisState.log("Confirmation not sent — no link. It will expire.")
        }
    }

    /**
     * A typed command, on the channel the web dashboard has always used.
     *
     * Echoed locally as a user turn: the server only broadcasts `speaker:user`
     * for speech it transcribed, so without this the history would show JARVIS
     * answering a question nobody appears to have asked.
     */
    private fun sendText(text: String) {
        val t = text.trim()
        if (t.isEmpty()) return
        if (!client.sendText(t)) {
            JarvisState.log("Not sent — no link.")
            return
        }
        JarvisState.addMessage(Message(false, t, System.currentTimeMillis()))
    }

    /**
     * A short haptic. The only feedback that reaches a phone in a pocket.
     *
     * Best-effort by design: a device with no vibrator, or an OEM that refuses
     * the call, must not take down the assistant over a buzz.
     */
    private fun buzz(pattern: LongArray) {
        try {
            val vib = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (getSystemService(VibratorManager::class.java))?.defaultVibrator
            } else {
                @Suppress("DEPRECATION") getSystemService(Vibrator::class.java)
            } ?: return
            if (!vib.hasVibrator()) return
            vib.vibrate(VibrationEffect.createWaveform(pattern, -1))
        } catch (_: Exception) {
        }
    }

    /**
     * When a turn happened, by this phone's clock — or 0 for the backlog.
     *
     * **The server's own `ts` is deliberately not used.** It is
     * `datetime.now().isoformat()`: a naive ISO string with no offset on it, and
     * nothing on the wire says which zone produced it. Reading it as local time
     * put JARVIS's turns two hours behind the user's on the very first test —
     * the VPS runs UTC, the phone runs Europe/Paris — and a wrong hour is worse
     * than no hour.
     *
     * A live event arrives within a second of being spoken, so the phone's own
     * clock is accurate for it. The exception is the burst the server replays on
     * connect: those are historical, their real times are unknowable from here,
     * and stamping them "now" would be the same lie in a different form. They
     * get 0, and the row simply shows no time.
     *
     * (A server that sent `datetime.now().astimezone().isoformat()` — one call,
     * backward compatible — would make all of this unnecessary.)
     */
    private fun turnTimestamp(): Long =
        if (SystemClock.elapsedRealtime() < replayUntil) 0L
        else System.currentTimeMillis()

    /**
     * Until when arriving events are the connect-time backlog rather than news.
     *
     * The server sends its last 50 messages the moment /ws opens; there is no
     * marker separating them from what happens next, so this is a window rather
     * than a flag. Generous on purpose: labelling a live turn as backlog costs
     * one missing timestamp, labelling backlog as live costs fifty wrong ones.
     */
    @Volatile private var replayUntil: Long = 0L

    /** Audio thread. Everything here is a comparison or a queue push. */
    private fun onMicFrame(frame: ByteArray, length: Int) {
        if (wakeWord.gatesAudio) {
            wakeWord.feed(frame, length)
            if (gateOpen && SystemClock.elapsedRealtime() > gateUntil) {
                gateOpen = false
                JarvisState.setGateOpen(false)
                client.closeMic()
                JarvisLog.mic("interaction window closed — back to local listening")
            }
            if (!gateOpen) return   // never leaves the phone
        }
        if (client.sendAudio(frame, length)) {
            JarvisState.countSent(length)
        } else {
            JarvisState.countDropped()
        }
        val snap = JarvisState.state.value
        JarvisLog.throughput(snap.bytesSent, snap.bytesReceived,
                             snap.bytesPlayed, snap.framesDropped)
    }

    // ── what the server says ─────────────────────────────────────────────────

    private inner class ClientEvents : JarvisClient.Listener {

        override fun onConnected() {
            reconnect.noteConnected()
            sawFirstAudio = false
            JarvisLog.net("connected to ${auth.endpoint.wsBase}")
            if (micRequested && gateOpen) client.openMic()
            updateNotification()
        }

        override fun onDisconnected(reason: String, fatal: Boolean) {
            JarvisLog.net(if (fatal) "stopped: $reason" else "lost: $reason")
            JarvisState.setMicOpen(micRequested)
            reconnect.noteDisconnected(reason, fatal)
        }

        override fun onAudioFormat(sampleRate: Int) {
            JarvisState.setDownlinkRate(sampleRate)
            if (player.start(sampleRate)) {
                JarvisLog.audio("speaker open at ${sampleRate}Hz mono 16-bit")
            } else {
                JarvisLog.warn("AUDIO", "could not open the speaker at ${sampleRate}Hz")
            }
        }

        override fun onAudio(pcm: ByteArray) {
            JarvisState.countReceived(pcm.size)
            if (!sawFirstAudio) {
                sawFirstAudio = true
                // The single most useful line in the log: it separates "the
                // server never sent anything" from "it arrived and you did not
                // hear it", which are different bugs in different projects.
                JarvisLog.audio("RX 24k PCM — first frame, ${pcm.size} bytes")
            }
            player.write(pcm)
        }

        override fun onUplinkDropped() = JarvisState.countDropped()

        override fun onEvent(json: JSONObject) {
            when (json.optString("type")) {
                Protocol.EV_JARVIS_STATE -> {
                    val state = when (json.optString("state").uppercase()) {
                        "LISTENING" -> AssistantState.LISTENING
                        "THINKING" -> AssistantState.THINKING
                        "SPEAKING" -> AssistantState.SPEAKING
                        "SLEEPING" -> AssistantState.SLEEPING
                        else -> AssistantState.UNKNOWN
                    }
                    JarvisState.setAssistant(state)
                    updateNotification()
                }
                Protocol.EV_LOG -> {
                    val fromJarvis = json.optString("speaker") == "jarvis"
                    val text = json.optString("text")
                    if (text.isNotBlank()) {
                        JarvisState.addMessage(
                            Message(fromJarvis, text, turnTimestamp())
                        )
                    }
                    JarvisState.log("${if (fromJarvis) "JARVIS" else "You"}: $text")
                }
                Protocol.EV_SYS -> JarvisState.log(json.optString("text"))
                Protocol.EV_CONTENT -> {
                    val body = json.optString("text")
                    if (body.isNotBlank()) {
                        JarvisState.addMessage(
                            Message(
                                fromJarvis = true,
                                text = body,
                                atMillis = turnTimestamp(),
                                title = json.optString("title").ifBlank { "Content" },
                            )
                        )
                    }
                    JarvisState.log("[${json.optString("title")}] ${body.take(200)}")
                }
                Protocol.EV_CONFIRM -> {
                    val id = json.optString("id")
                    val title = json.optString("title")
                    if (id.isNotBlank()) {
                        JarvisState.setConfirmation(
                            id, title, json.optString("detail"),
                            json.optInt("timeout_s", 0),
                        )
                        // The one event that must not be missed while the phone
                        // is in a pocket. Two pulses, not a buzz: distinguishable
                        // from a message without being an alarm.
                        buzz(longArrayOf(0, 40, 90, 40))
                    } else {
                        // A server older than this app: it asks, but there is no
                        // id to answer with. Say so rather than showing buttons
                        // whose answer would be discarded.
                        JarvisState.log("Confirmation needed (no id — cannot answer "
                                        + "from here): $title")
                    }
                }
                Protocol.EV_CONFIRM_HIDE -> JarvisState.clearConfirmation()
                Protocol.EV_STATUS -> Unit   // superseded by jarvis_state
                else -> Log.d(TAG, "unhandled event: $json")
            }
        }
    }

    // ── notification ─────────────────────────────────────────────────────────

    private fun startForegroundNotification() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "JARVIS link", NotificationManager.IMPORTANCE_LOW)
                .apply {
                    description = "Shown while JARVIS is connected and may be listening."
                    setShowBadge(false)
                }
        )

        // The microphone type is what Android 14+ requires to keep recording
        // once the app leaves the foreground; below API 29 the parameter does
        // not exist and 0 is the only correct value.
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
        ServiceCompat.startForeground(this, NOTIF_ID, buildNotification(), type)
    }

    private fun updateNotification() {
        val text = notificationText()
        if (text == lastNotificationText) return
        lastNotificationText = text
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        try {
            manager.notify(NOTIF_ID, buildNotification())
        } catch (e: Exception) {
            Log.w(TAG, "notify failed: ${e.message}")
        }
    }

    private fun notificationText(): String {
        val snap = JarvisState.state.value
        val link = when (snap.link) {
            LinkState.CONNECTED -> "Connected"
            LinkState.CONNECTING -> "Connecting…"
            LinkState.RECONNECTING ->
                if (snap.nextRetrySeconds > 0) "Reconnecting in ${snap.nextRetrySeconds}s"
                else "Reconnecting…"
            LinkState.ERROR -> snap.lastError ?: "Error"
            LinkState.DISCONNECTED -> "Disconnected"
        }
        val mic = if (snap.micOpen) " · microphone on" else ""
        return link + mic
    }

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1,
            Intent(this, JarvisForegroundService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )

        return Notification.Builder(this, CHANNEL)
            .setContentTitle("JARVIS")
            .setContentText(notificationText())
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(null as android.graphics.drawable.Icon?, "Stop", stop)
                    .build()
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    companion object {
        private const val TAG = "JarvisService"
        private const val CHANNEL = "jarvis_link"
        private const val NOTIF_ID = 5301

        const val ACTION_START = "com.jarvis.START"
        const val ACTION_STOP = "com.jarvis.STOP"
        const val ACTION_MIC_ON = "com.jarvis.MIC_ON"
        const val ACTION_MIC_OFF = "com.jarvis.MIC_OFF"
        const val ACTION_INTERRUPT = "com.jarvis.INTERRUPT"
        const val ACTION_CONFIRM = "com.jarvis.CONFIRM"
        const val ACTION_SAY = "com.jarvis.SAY"

        const val EXTRA_CONFIRM_ID = "confirm_id"
        const val EXTRA_CONFIRMED = "confirmed"
        const val EXTRA_TEXT = "text"

        /** How long a wake-word interaction stays open with nobody speaking. */
        private const val INTERACTION_WINDOW_MS = 30_000L
        private const val REPLAY_WINDOW_MS = 3_000L

        /** Mic level above which someone is considered to be speaking. */
        private const val VOICE_FLOOR = 0.04f

        fun send(context: Context, action: String) {
            val intent = Intent(context, JarvisForegroundService::class.java).setAction(action)
            if (action == ACTION_START) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        /** [send] with the text of a typed command. */
        fun sendSpoken(context: Context, text: String) {
            context.startService(
                Intent(context, JarvisForegroundService::class.java)
                    .setAction(ACTION_SAY)
                    .putExtra(EXTRA_TEXT, text)
            )
        }

        /** [send] with the two extras a confirmation answer needs. */
        fun sendConfirmation(context: Context, id: String, confirmed: Boolean) {
            context.startService(
                Intent(context, JarvisForegroundService::class.java)
                    .setAction(ACTION_CONFIRM)
                    .putExtra(EXTRA_CONFIRM_ID, id)
                    .putExtra(EXTRA_CONFIRMED, confirmed)
            )
        }
    }
}
