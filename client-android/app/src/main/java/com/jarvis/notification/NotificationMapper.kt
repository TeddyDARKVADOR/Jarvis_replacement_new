package com.jarvis.notification

import android.app.NotificationManager
import org.json.JSONObject

/**
 * Turns a `notification` event into something Android can display.
 *
 * Pure, and deliberately so: no Context, no system service, nothing that needs
 * a device. Parsing and channel choice are the two things most likely to be
 * wrong, and keeping them free of Android means they can be reasoned about —
 * and later unit-tested — without an emulator.
 *
 * ## Android presents, it does not re-decide
 *
 * The priority arrives already decided. `context/policy.py` weighed it against
 * the user's situation — asleep, driving, in a meeting — using signals this
 * process does not have. Re-ranking here would mean two policies disagreeing
 * about the same message, and the one with less information winning.
 *
 * So the mapping below is a presentation table, not a policy: each server
 * priority gets the Android behaviour that expresses it, and nothing more.
 */
object NotificationMapper {

    /** Wire type, mirroring `server/notify.py`'s EVENT_TYPE. */
    const val EVENT_TYPE = "notification"

    // One channel per behaviour, three in total. Android channels are permanent
    // once created and a user can retune each one, so they are a promise about
    // behaviour rather than a label — which is why there are three and not one
    // per feature.
    const val CHANNEL_CRITICAL = "jarvis_critical"
    const val CHANNEL_IMPORTANT = "jarvis_important"
    const val CHANNEL_GENERAL = "jarvis_general"

    data class Spec(
        val id: String,
        val priority: String,
        val title: String,
        val text: String,
        val sentAt: Long,
        val channel: String,
        /** Platform importance, used only when creating the channel. */
        val importance: Int,
    )

    /**
     * Parse an event. Returns null for anything that must not be shown.
     *
     * Null covers four cases on purpose — malformed, wrong type, empty, and
     * TRIVIAL — because the caller's response to all four is identical: count
     * it as dropped and move on. Distinguishing them would only give the caller
     * a decision it does not need to make.
     */
    fun parse(json: JSONObject): Spec? {
        if (json.optString("type") != EVENT_TYPE) return null

        val id = json.optString("id").trim()
        val text = json.optString("text").trim()
        // No id means no way to avoid showing it twice, and a notification with
        // no body is a buzz carrying no information. Neither is worth showing.
        if (id.isEmpty() || text.isEmpty()) return null

        val priority = json.optString("priority").trim().uppercase().ifEmpty { "USEFUL" }
        if (priority == "TRIVIAL") return null      // ANODIN : jamais de notification

        val title = json.optString("title").trim().ifEmpty { "JARVIS" }
        // Server seconds (float) → millis. A server that sends nothing leaves 0,
        // and the caller then treats the notification as undateable rather than
        // as having arrived in 1970.
        val sentAt = (json.optDouble("ts", 0.0) * 1000.0).toLong()

        val channel = channelFor(priority)
        return Spec(
            id = id,
            priority = priority,
            title = title,
            text = text,
            sentAt = sentAt,
            channel = channel,
            importance = importanceFor(channel),
        )
    }

    fun channelFor(priority: String): String = when (priority.uppercase()) {
        "CRITICAL" -> CHANNEL_CRITICAL
        "IMPORTANT" -> CHANNEL_IMPORTANT
        else -> CHANNEL_GENERAL          // USEFUL, et tout niveau inconnu
    }

    /**
     * CRITICAL gets HIGH — the level that produces a heads-up banner and a
     * sound. That is the only Android behaviour that matches "interrompre".
     *
     * Note what is NOT done here: `setBypassDnd(true)`. It requires notification
     * policy access, a special grant this app does not hold and does not ask
     * for. So a CRITICAL notification is as loud as Android allows an ordinary
     * app to be, and Do-Not-Disturb still silences it. The server-side table
     * already knows this — it routes CRITICAL in a meeting to a notification
     * rather than to speech precisely because it cannot count on being heard.
     */
    fun importanceFor(channel: String): Int = when (channel) {
        CHANNEL_CRITICAL -> NotificationManager.IMPORTANCE_HIGH
        CHANNEL_IMPORTANT -> NotificationManager.IMPORTANCE_DEFAULT
        else -> NotificationManager.IMPORTANCE_LOW    // discret : ni son ni banniere
    }

    /**
     * A stable Android notification id derived from the server's string id.
     *
     * Stable so that the same notification arriving twice REPLACES itself
     * instead of stacking. That is the second line of defence behind the
     * seen-id check — if dedup ever fails, the user sees one notification
     * updated, not two identical ones.
     *
     * Offset well clear of the foreground service's own id (5301) so this can
     * never replace the ongoing "JARVIS connected" notification and stop the
     * service looking alive.
     */
    fun androidIdFor(serverId: String): Int {
        val h = serverId.hashCode()
        return BASE_NOTIFICATION_ID + (if (h == Int.MIN_VALUE) 0 else Math.abs(h)) % 100_000
    }

    private const val BASE_NOTIFICATION_ID = 100_000
}
