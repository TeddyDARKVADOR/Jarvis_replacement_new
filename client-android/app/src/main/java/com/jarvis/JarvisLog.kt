package com.jarvis

import android.util.Log
import android.os.SystemClock

/**
 * One tagged line per event worth seeing, and not one per audio frame.
 *
 * ```
 * adb logcat -s JARVIS
 * ```
 *
 * **Why the rate limiting is the point.** The uplink produces ~15 frames a
 * second and the downlink arrives in bursts; a log line per frame would push
 * every interesting line — the reconnect, the failure, the format mismatch —
 * off the buffer within seconds. So frames are counted and summarised on a
 * timer, and everything else is logged as it happens.
 *
 * Tags match what the server prints, so a problem can be followed across both
 * sides without translating vocabulary:
 * ```
 * [NET]     socket lifecycle
 * [AUDIO]   TX / RX / playback
 * [SERVICE] foreground service lifecycle
 * ```
 */
object JarvisLog {

    private const val TAG = "JARVIS"
    private const val SUMMARY_INTERVAL_MS = 5_000L

    /** Also mirror lines into the on-screen log. Off for per-frame summaries. */
    private var lastSummary = 0L

    fun net(message: String, toScreen: Boolean = true) = emit("NET", message, toScreen)

    fun audio(message: String, toScreen: Boolean = true) = emit("AUDIO", message, toScreen)

    fun service(message: String, toScreen: Boolean = true) = emit("SERVICE", message, toScreen)

    fun mic(message: String, toScreen: Boolean = true) = emit("MIC", message, toScreen)

    fun warn(tag: String, message: String) {
        Log.w(TAG, "[$tag] $message")
        JarvisState.log("[$tag] $message")
    }

    /**
     * Called on every frame; prints at most one line every 5 s.
     *
     * Logcat only — never the on-screen log, which shows the conversation and
     * would be drowned by throughput lines.
     */
    fun throughput(txBytes: Long, rxBytes: Long, playedBytes: Long, dropped: Long) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastSummary < SUMMARY_INTERVAL_MS) return
        lastSummary = now
        Log.i(
            TAG,
            "[AUDIO] TX 16k ${txBytes / 1024}KB · RX 24k ${rxBytes / 1024}KB · " +
                "played ${playedBytes / 1024}KB · dropped $dropped",
        )
    }

    private fun emit(tag: String, message: String, toScreen: Boolean) {
        Log.i(TAG, "[$tag] $message")
        if (toScreen) JarvisState.log("[$tag] $message")
    }
}
