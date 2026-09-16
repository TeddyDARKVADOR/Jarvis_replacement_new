package com.jarvis.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.SystemClock
import android.util.Log
import com.jarvis.LinkState
import com.jarvis.auth.AuthManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.random.Random

/**
 * When to try again — the only place in the app that decides that.
 *
 * **Lifted from the old Jarvis Android, whose `SpeechErrorPolicy` was written
 * against a real, measured failure.** Its microphone restart loop fed itself:
 * an error triggered a retry, the retry came too soon, Android answered
 * `error_client`, which triggered another retry. The fix was not a longer sleep
 * but three separate categories of failure and a growing delay between
 * attempts. The same three categories apply here:
 *
 * | Old Jarvis (microphone)   | Here (WebSocket)                          |
 * |---------------------------|-------------------------------------------|
 * | `restartSilently`         | ordinary close — reconnect, say nothing   |
 * | `restartWithBackoff`      | network down, server restarting           |
 * | `stopAndReport`           | the device token is refused               |
 *
 * **What is deliberately NOT carried over: `maxConsecutive`.** That cap made
 * sense for a microphone whose failure means the user is being ignored while
 * looking at the screen. A 24/7 assistant on a phone in a pocket has the
 * opposite need — a tunnel that is down for six hours must still be up when the
 * phone comes back into coverage. So the delay is capped instead of the number
 * of attempts, and only a *refused credential* stops the loop.
 *
 * **`minSessionLife`, also from the old code, and the least obvious part.** A
 * connection that dies two seconds after opening is not a success followed by a
 * failure; it is one failure in two acts. Resetting the backoff on it produces
 * a tight loop that looks like a working reconnect in the logs and hammers the
 * server. Only a connection that *lived* clears the counter.
 */
class ReconnectManager(
    context: Context,
    private val client: JarvisClient,
    private val scope: CoroutineScope,
    private val onLink: (LinkState, String?) -> Unit,
    private val onRetry: (attempt: Int, secondsLeft: Int) -> Unit,
    private val onNote: (String) -> Unit = {},
) {

    private val connectivity =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    @Volatile private var running = false
    @Volatile private var connected = false
    @Volatile private var attempts = 0
    @Volatile private var connectedAt = 0L

    /**
     * The countdown between attempts — and *only* that.
     *
     * It used to double as the handle on the in-flight attempt, which deadlocked
     * the loop: `connectNow` assigned its own coroutine here, `client.connect()`
     * threw (it performs the device-login synchronously, so a server that is down
     * throws rather than failing a socket later), and the `catch` called
     * `scheduleRetry` *from inside that coroutine*. The guard below then saw
     * `retryJob.isActive` — itself — decided a retry was already pending, and
     * scheduled nothing. The app sat in RECONNECTING for ever while the server
     * was up and reachable, and `start()` refused to help because `running` was
     * still true. Observed on a VPS restart and on a phone losing its network.
     */
    private var retryJob: Job? = null

    /** The attempt in flight. Never consulted by [scheduleRetry]. */
    private var connectJob: Job? = null

    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    // ── lifecycle ────────────────────────────────────────────────────────────

    fun start() {
        if (running) {
            // Already trying. Treat a second CONNECT as "stop waiting, try now"
            // rather than as a no-op: this button is dead exactly when the user
            // most wants it, staring at a stalled RECONNECTING.
            if (!connected) {
                attempts = 0
                connectNow("connect pressed while retrying")
            }
            return
        }
        running = true
        attempts = 0
        registerNetworkWatch()
        connectNow("start")
    }

    fun stop() {
        running = false
        connected = false
        retryJob?.cancel()
        retryJob = null
        connectJob?.cancel()
        connectJob = null
        unregisterNetworkWatch()
        client.disconnect()
        onRetry(0, 0)
        onLink(LinkState.DISCONNECTED, null)
    }

    // ── reported by JarvisClient ─────────────────────────────────────────────

    fun noteConnected() {
        connected = true
        connectedAt = SystemClock.elapsedRealtime()
        retryJob?.cancel()
        retryJob = null
        onRetry(attempts, 0)
        onLink(LinkState.CONNECTED, null)
    }

    fun noteDisconnected(reason: String, fatal: Boolean) {
        val lived = connected &&
            SystemClock.elapsedRealtime() - connectedAt >= MIN_SESSION_LIFE_MS
        connected = false

        if (!running) return

        if (fatal) {
            onLink(LinkState.ERROR, reason)
            onNote("Stopped: $reason")
            running = false
            unregisterNetworkWatch()
            return
        }

        if (lived) {
            // A real session ended. Start the escalation from the bottom again
            // so a server restart costs one second, not thirty.
            attempts = 0
        }
        scheduleRetry(reason)
    }

    // ── the loop ─────────────────────────────────────────────────────────────

    private fun connectNow(why: String) {
        // Cancel the countdown, if one is pending — we are attempting right now.
        // (When this runs *from* that countdown's last line, cancelling it is
        // harmless: the new job below is launched on `scope`, not as its child.)
        retryJob?.cancel()
        retryJob = null
        connectJob?.cancel()
        connectJob = scope.launch(Dispatchers.IO) {
            onLink(
                if (attempts == 0) LinkState.CONNECTING else LinkState.RECONNECTING,
                null,
            )
            try {
                client.connect()
                // Success is not declared here: the sockets are only *requested*.
                // noteConnected() arrives when both are genuinely open.
            } catch (e: AuthManager.AuthRejected) {
                noteDisconnected(e.message ?: "device token refused", fatal = true)
            } catch (e: Exception) {
                Log.i(TAG, "connect failed ($why): ${e.message}")
                scheduleRetry(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    private fun scheduleRetry(reason: String) {
        if (!running) return
        if (retryJob?.isActive == true && !connected) {
            // A countdown is already running. Three sockets can report the same
            // outage within milliseconds of each other; without this they would
            // each schedule their own attempt and the backoff would mean
            // nothing. This deliberately does NOT look at `connectJob`: a failed
            // attempt calls us from inside its own coroutine, and treating that
            // as "a retry is pending" is what used to kill the loop.
            return
        }
        attempts++
        val seconds = backoffSeconds(attempts)
        onLink(LinkState.RECONNECTING, reason)
        onNote("Reconnecting in ${seconds}s (attempt $attempts) — $reason")

        retryJob?.cancel()
        retryJob = scope.launch {
            var left = seconds
            while (left > 0 && isActive) {
                onRetry(attempts, left)
                delay(1_000)
                left--
            }
            onRetry(attempts, 0)
            if (isActive) connectNow("retry #$attempts")
        }
    }

    /**
     * 1, 2, 4, 8, 15, 30 s, then 30 s for ever, ±20 %.
     *
     * The jitter is not decoration: after a VPS reboot every device that was
     * connected wakes on the same schedule and retries in step. Spreading them
     * costs nothing and stops the server being hit by a synchronised wave.
     */
    private fun backoffSeconds(attempt: Int): Int {
        val steps = intArrayOf(1, 2, 4, 8, 15, 30)
        val base = steps[(attempt - 1).coerceIn(0, steps.lastIndex)]
        val jitter = 1.0 + Random.nextDouble(-0.2, 0.2)
        return (base * jitter).toInt().coerceAtLeast(1)
    }

    // ── "the Wi-Fi is back" ──────────────────────────────────────────────────

    private fun registerNetworkWatch() {
        if (networkCallback != null) return
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                // Without this the app sits out a thirty-second backoff that the
                // returning Wi-Fi has just made pointless. This is the whole
                // difference between "reconnects eventually" and "is there when
                // you walk back in the door".
                if (running && !connected) {
                    onNote("Network available — reconnecting now")
                    attempts = 0
                    connectNow("network available")
                }
            }

            override fun onLost(network: Network) {
                if (running && connected) onNote("Network lost")
            }
        }
        try {
            connectivity.registerDefaultNetworkCallback(cb)
            networkCallback = cb
        } catch (e: Exception) {
            // Some OEM builds throttle callback registration. Losing the fast
            // path is survivable — the backoff still reconnects.
            Log.w(TAG, "network callback unavailable: ${e.message}")
        }
    }

    private fun unregisterNetworkWatch() {
        networkCallback?.let {
            try {
                connectivity.unregisterNetworkCallback(it)
            } catch (_: Exception) {
            }
        }
        networkCallback = null
    }

    private companion object {
        const val TAG = "ReconnectManager"

        /** Below this, a connection did not live: see the class note. */
        const val MIN_SESSION_LIFE_MS = 3_000L
    }
}
