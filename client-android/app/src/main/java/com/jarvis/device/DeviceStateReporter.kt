package com.jarvis.device

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Reports what this phone can see about its own situation, so MARK LIII can
 * decide whether now is a good moment to speak.
 *
 * The server side is `context/` (see `context/README.md`). This class is the
 * only thing on the phone that knows about it, and the server treats every
 * field as optional — so a signal this phone cannot read is a field that is
 * simply absent, never an error at either end.
 *
 * ## No new permissions
 *
 * Everything below is readable with what `AndroidManifest.xml` already
 * declares. That is a constraint the class was written to, not a coincidence:
 * the manifest documents what it deliberately refuses to ask for, and a
 * context feature is not a good enough reason to reopen that decision.
 *
 * | Signal            | How                                     | Permission |
 * |-------------------|-----------------------------------------|------------|
 * | battery, charging | sticky `ACTION_BATTERY_CHANGED`         | none       |
 * | screen on/off     | `PowerManager.isInteractive`            | none       |
 * | headset           | `AudioManager.getDevices(OUTPUTS)`      | none       |
 * | ringer            | `AudioManager.getRingerMode`            | none       |
 * | do-not-disturb    | `getCurrentInterruptionFilter`          | none¹      |
 * | network kind      | `ConnectivityManager`                   | already declared |
 *
 * ¹ Returns `INTERRUPTION_FILTER_UNKNOWN` unless the app holds notification
 *   policy access. That case reports nothing rather than guessing `false`, and
 *   the server's rules then fall back to the ringer mode.
 *
 * ## What is NOT reported, and what it costs
 *
 * `activity` (the `IN_VEHICLE` signal the server's DRIVING rule prefers) needs
 * `ACTIVITY_RECOGNITION` plus Play Services, and `place` needs location. Both
 * are left out. The consequence is honest and worth knowing: **driving is
 * detected only by the name of the connected Bluetooth audio device**, so the
 * car's name has to be in `car_bluetooth_names` on the server for that row of
 * the policy table to fire at all.
 *
 * ## Why it reports on a heartbeat and not only on change
 *
 * The server expires device facts after 300 s (`device_ttl_s`) precisely so a
 * phone that silently dropped off Wi-Fi cannot leave a stale "headset
 * connected" behind. That expiry only works if a healthy phone keeps saying so,
 * hence [HEARTBEAT_MS] at well under half the TTL. Change events alone would
 * make a phone that changed nothing for six minutes look like a phone that
 * left.
 */
class DeviceStateReporter(
    private val context: Context,
    private val scope: CoroutineScope,
    /** Returns false when the events socket is down; the payload is then dropped. */
    private val send: (JSONObject) -> Boolean,
) {

    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
    private val power = context.getSystemService(Context.POWER_SERVICE) as? PowerManager

    private var job: Job? = null
    private var receiver: BroadcastReceiver? = null

    /** When the screen last went dark. 0 = it has not, since we started. */
    @Volatile private var screenOffSince: Long = 0L

    /** Last payload actually sent, to avoid repeating an unchanged one. */
    @Volatile private var lastSent: String = ""
    @Volatile private var lastSentAt: Long = 0L

    // ── lifecycle ────────────────────────────────────────────────────────────

    fun start() {
        if (job != null) return
        screenOffSince = if (power?.isInteractive == false) SystemClock.elapsedRealtime() else 0L
        registerReceiver()
        job = scope.launch {
            while (isActive) {
                report(force = false)
                delay(TICK_MS)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        receiver?.let {
            runCatching { context.unregisterReceiver(it) }
            receiver = null
        }
        lastSent = ""
        lastSentAt = 0L
    }

    /** Push immediately — called when the link comes back, so the server is not
     *  left waiting a full tick for facts it just lost on reconnect. */
    fun reportNow() {
        scope.launch { report(force = true) }
    }

    // ── collection ───────────────────────────────────────────────────────────

    private fun report(force: Boolean) {
        val payload = runCatching { collect() }.getOrNull() ?: return
        val body = payload.toString()
        val now = SystemClock.elapsedRealtime()
        val stale = (now - lastSentAt) >= HEARTBEAT_MS
        if (!force && !stale && body == lastSent) return

        val message = JSONObject()
            .put("type", TYPE_DEVICE_STATE)
            .put("state", payload)
        if (send(message)) {
            lastSent = body
            lastSentAt = now
        }
    }

    /**
     * Build the payload, one guarded read at a time.
     *
     * Each block is individually wrapped: a manufacturer that throws from one
     * system service must cost us that one field, not the whole report. A
     * partial report is useful — the server merges it into what it already has.
     */
    private fun collect(): JSONObject {
        val out = JSONObject()

        runCatching {
            val interactive = power?.isInteractive ?: return@runCatching
            out.put("screen_on", interactive)
            // Not true input idleness — that needs PACKAGE_USAGE_STATS. This is
            // "how long the screen has been dark", which is the signal the
            // server's sleep rule actually wants, and it is reported as 0 while
            // the screen is on rather than pretending to know more.
            out.put("idle_seconds", if (interactive || screenOffSince == 0L) 0L
                                    else (SystemClock.elapsedRealtime() - screenOffSince) / 1000L)
        }

        runCatching {
            val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
                ?: return@runCatching
            val level = battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = battery.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
            if (level >= 0 && scale > 0) out.put("battery_percent", level * 100 / scale)
            val status = battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
            if (status != -1) {
                out.put("battery_charging",
                    status == BatteryManager.BATTERY_STATUS_CHARGING ||
                        status == BatteryManager.BATTERY_STATUS_FULL)
            }
        }

        runCatching {
            val am = audio ?: return@runCatching
            val devices = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            val names = mutableListOf<String>()
            var headset = false
            for (device in devices) {
                if (device.type in HEADSET_TYPES) {
                    headset = true
                    val name = device.productName?.toString()?.trim().orEmpty()
                    if (name.isNotEmpty() && name !in names) names += name
                }
            }
            out.put("headset", headset)
            if (names.isNotEmpty()) {
                out.put("headset_name", names.first())
                // The server matches car names against this list. On Android 12+
                // a Bluetooth product name may come back generic without
                // BLUETOOTH_CONNECT — which is why the car has to be named in
                // the server's own list rather than detected from here alone.
                out.put("bluetooth_devices", org.json.JSONArray(names))
            }
        }

        runCatching {
            val am = audio ?: return@runCatching
            out.put("ringer", when (am.ringerMode) {
                AudioManager.RINGER_MODE_NORMAL -> "NORMAL"
                AudioManager.RINGER_MODE_VIBRATE -> "VIBRATE"
                AudioManager.RINGER_MODE_SILENT -> "SILENT"
                else -> "UNKNOWN"
            })
        }

        runCatching {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE)
                as? android.app.NotificationManager ?: return@runCatching
            when (nm.currentInterruptionFilter) {
                android.app.NotificationManager.INTERRUPTION_FILTER_UNKNOWN -> Unit  // on se tait
                android.app.NotificationManager.INTERRUPTION_FILTER_ALL -> out.put("dnd", false)
                else -> out.put("dnd", true)
            }
        }

        runCatching {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE)
                as? ConnectivityManager ?: return@runCatching
            val caps = cm.getNetworkCapabilities(cm.activeNetwork)
            out.put("network", when {
                caps == null -> "offline"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                else -> "other"
            })
        }

        return out
    }

    // ── change events ────────────────────────────────────────────────────────

    /**
     * Screen and headset changes are worth reporting the instant they happen:
     * they are the two that change what JARVIS may do, and waiting up to a tick
     * to learn a headset went in is exactly the delay a user would notice.
     */
    private fun registerReceiver() {
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(AudioManager.ACTION_HEADSET_PLUG)
            addAction(AudioManager.RINGER_MODE_CHANGED_ACTION)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                addAction(android.app.NotificationManager
                    .ACTION_INTERRUPTION_FILTER_CHANGED)
            }
        }
        val rx = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context?, intent: Intent?) {
                when (intent?.action) {
                    Intent.ACTION_SCREEN_OFF -> screenOffSince = SystemClock.elapsedRealtime()
                    Intent.ACTION_SCREEN_ON -> screenOffSince = 0L
                }
                reportNow()
            }
        }
        // targetSdk 34+ requires the export flag even though every action above
        // is a system broadcast. NOT_EXPORTED is the correct answer: nothing in
        // this app should be able to forge a screen-off.
        ContextCompat.registerReceiver(context, rx, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        receiver = rx
    }

    companion object {
        /** Wire type, mirroring `Protocol.CMD_DEVICE_STATE`. */
        const val TYPE_DEVICE_STATE = "device_state"

        /** How often the collector looks. Cheap: every read is a local lookup. */
        private const val TICK_MS = 30_000L

        /** Resend an unchanged payload at least this often. Must stay well under
         *  the server's `device_ttl_s` (300 s) or a quiet phone looks like a
         *  gone phone. */
        private const val HEARTBEAT_MS = 120_000L

        private val HEADSET_TYPES: Set<Int> = buildSet {
            add(AudioDeviceInfo.TYPE_WIRED_HEADSET)
            add(AudioDeviceInfo.TYPE_WIRED_HEADPHONES)
            add(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)
            add(AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
            add(AudioDeviceInfo.TYPE_USB_HEADSET)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(AudioDeviceInfo.TYPE_BLE_HEADSET)
            }
        }
    }
}
