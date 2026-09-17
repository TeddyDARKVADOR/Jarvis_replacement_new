package com.jarvis.notification

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import com.jarvis.JarvisState
import com.jarvis.MainActivity
import com.jarvis.R
import org.json.JSONObject

/**
 * Shows a server notification, once.
 *
 * Owns exactly one job. It is not in [com.jarvis.service.JarvisForegroundService]
 * because that class already carries the link, the microphone, the wake word and
 * the ongoing notification; and it is not in
 * [com.jarvis.net.JarvisClient] because the client's job ends when JSON comes
 * off the socket. The service hands an event over and forgets about it.
 *
 * ## Showing it once is the hard part
 *
 * The server replays recent events to a client that connects — the last 50
 * messages, plus every notification still inside its TTL (PROTOCOL.md §4). That
 * is what stops a notification being lost while the phone is out of coverage,
 * and it is also what would show the same alert on every single reconnect.
 *
 * So this class, not the server, is the authority on "already shown", because
 * it is the only one that knows. It remembers ids in [android.content.SharedPreferences],
 * which survives the app being killed and restarted — an in-memory set would
 * forget everything exactly when Android is most likely to have restarted the
 * service, which is the moment a replay arrives.
 *
 * The list is bounded: only the most recent [SEEN_LIMIT] ids are kept. An id
 * older than that has also aged out of the server's TTL, so it can never come
 * back to be re-shown.
 *
 * ## Dismissal is respected
 *
 * A dismissed notification whose id is still remembered is not re-posted on the
 * next reconnect. Dismissing is the user saying they have read it; a server
 * replay is not new information and must not override that.
 */
class JarvisNotificationManager(private val context: Context) {

    private val manager: NotificationManager? =
        context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Mirror of the stored list, so the common case touches no disk. */
    private val seen: ArrayDeque<String> = ArrayDeque(loadSeen())

    // ── channels ─────────────────────────────────────────────────────────────

    /**
     * Create the three channels. Idempotent — Android ignores a channel that
     * already exists, and will not lower an importance the user has raised or
     * lowered by hand.
     *
     * Called at service start rather than at first notification: a channel that
     * only appears once something has been sent cannot be tuned in advance, and
     * the user would have no way to pre-mute JARVIS's discreet notifications.
     */
    fun ensureChannels() {
        val mgr = manager ?: return
        val specs = listOf(
            Triple(NotificationMapper.CHANNEL_CRITICAL, "JARVIS critique",
                "Interruptions : securite, argent, perte de donnees."),
            Triple(NotificationMapper.CHANNEL_IMPORTANT, "JARVIS important",
                "Ce qui merite d'etre vu dans l'heure."),
            Triple(NotificationMapper.CHANNEL_GENERAL, "JARVIS general",
                "Informations utiles, sans urgence. Discret."),
        )
        for ((id, name, description) in specs) {
            runCatching {
                mgr.createNotificationChannel(
                    NotificationChannel(id, name, NotificationMapper.importanceFor(id))
                        .apply { this.description = description }
                )
            }
        }
    }

    // ── display ──────────────────────────────────────────────────────────────

    /**
     * Handle one `notification` event. Safe to call from any thread and with
     * any JSON — it never throws.
     *
     * @return true when something was actually shown.
     */
    fun handle(json: JSONObject): Boolean {
        JarvisState.countNotificationReceived()

        val spec = NotificationMapper.parse(json)
        if (spec == null) {
            JarvisState.countNotificationDropped()
            return false
        }
        synchronized(seen) {
            if (seen.contains(spec.id)) {
                // Un replay serveur, pas une information neuve.
                JarvisState.countNotificationDuplicate()
                return false
            }
        }

        val mgr = manager
        if (mgr == null) {
            JarvisState.countNotificationDropped()
            return false
        }

        val shown = runCatching {
            mgr.notify(NotificationMapper.androidIdFor(spec.id), build(spec))
        }.isSuccess

        if (!shown) {
            // Le cas le plus probable : POST_NOTIFICATIONS refuse sur Android 13+.
            JarvisState.countNotificationDropped()
            JarvisState.log("Notification refusee par Android (permission ?)")
            Log.w(TAG, "notify failed for ${spec.id}")
            return false
        }

        // Remembered only after a successful post, so a notification Android
        // refused can still arrive on the next replay instead of being
        // remembered as delivered and silently lost.
        remember(spec.id)
        JarvisState.countNotificationShown()
        JarvisState.log("Notification ${spec.priority} : ${spec.title}")
        return true
    }

    private fun build(spec: NotificationMapper.Spec): Notification {
        // A distinct requestCode per notification: with the same code, Android
        // reuses the first PendingIntent and every tap would carry the first
        // notification's extras.
        val open = PendingIntent.getActivity(
            context,
            NotificationMapper.androidIdFor(spec.id),
            Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
                // Nothing reads this yet. It is here so that opening on the
                // relevant conversation later needs no protocol change — the
                // id that identifies the notification server-side is already
                // travelling with the tap.
                .putExtra(EXTRA_NOTIFICATION_ID, spec.id),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val builder = Notification.Builder(context, spec.channel)
            .setContentTitle(spec.title)
            .setContentText(spec.text)
            // The app's own name sits in the sub-text, so the headline belongs
            // to the message rather than being "JARVIS" every single time.
            .setSubText("JARVIS")
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setContentIntent(open)
            // Tapping means read. Leaving it in the shade after a tap is how
            // an assistant accumulates a column of stale alerts.
            .setAutoCancel(true)
            .setStyle(Notification.BigTextStyle().bigText(spec.text))

        if (spec.sentAt > 0) {
            builder.setWhen(spec.sentAt).setShowWhen(true)
        }
        if (spec.channel == NotificationMapper.CHANNEL_CRITICAL) {
            builder.setCategory(Notification.CATEGORY_ALARM)
        }
        return builder.build()
    }

    // ── dedup store ──────────────────────────────────────────────────────────

    private fun remember(id: String) {
        val snapshot: List<String>
        synchronized(seen) {
            seen.addLast(id)
            while (seen.size > SEEN_LIMIT) seen.removeFirst()
            snapshot = seen.toList()
        }
        runCatching {
            prefs.edit().putString(KEY_SEEN, snapshot.joinToString(SEPARATOR)).apply()
        }
    }

    private fun loadSeen(): List<String> = runCatching {
        prefs.getString(KEY_SEEN, "")
            .orEmpty()
            .split(SEPARATOR)
            .filter { it.isNotBlank() }
            .takeLast(SEEN_LIMIT)
    }.getOrDefault(emptyList())

    /** Developer-mode escape hatch: forget every id so replays show again. */
    fun forgetSeen() {
        synchronized(seen) { seen.clear() }
        runCatching { prefs.edit().remove(KEY_SEEN).apply() }
    }

    companion object {
        private const val TAG = "JarvisNotify"
        private const val PREFS = "jarvis_notifications"
        private const val KEY_SEEN = "seen_ids"
        private const val SEPARATOR = ","

        /** Comfortably more than the server's own bounded recent list (50), so
         *  nothing can age out here while it is still replayable there. */
        private const val SEEN_LIMIT = 200

        const val EXTRA_NOTIFICATION_ID = "jarvis_notification_id"
    }
}
