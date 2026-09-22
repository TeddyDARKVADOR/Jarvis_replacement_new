package com.jarvis.device

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.provider.Settings
import android.util.Log
import java.text.Normalizer

/**
 * Opens an application on this phone, by the name a person would say.
 *
 * The server already has an `open_app` action — this is the same capability on
 * a second pair of hands, not a second feature. `server/targeting.py` routes on
 * the *name*: once this phone declares `open_app`, "ouvre Chrome" said to the
 * phone opens Chrome here, and "ouvre Chrome sur mon PC" opens it there,
 * decided by the four rules and not by which tool the model happened to pick.
 *
 * ## Why this needs SYSTEM_ALERT_WINDOW, and why the capability is withheld
 * ## without it
 *
 * Since Android 10 an app cannot start an activity while it is in the
 * background. JARVIS is a foreground *service* — a notification, not a window —
 * so every launch from a voice command is a background launch, and the platform
 * drops it. It does not throw: `startActivity` returns normally, the logcat
 * shows one line from ActivityTaskManager, and nothing happens on screen. An
 * assistant that says "c'est ouvert" while nothing opened is the exact failure
 * this codebase refuses to ship.
 *
 * `SYSTEM_ALERT_WINDOW` is the one documented, stable exemption. The app never
 * draws an overlay with it — it is held solely so the launch the user asked for
 * actually reaches the screen.
 *
 * It is granted from Settings, not from a dialog, so it can be absent for a
 * long time. That is why [available] exists and why
 * [DeviceCapabilities.granted] filters on it: an ungranted phone declares no
 * `open_app` at all, the server routes to the PC or asks, and nobody is told
 * that something happened when it did not. It is the same rule
 * `client_desktop/device.py` follows when it refuses to declare an action whose
 * dependency is missing — a capability is proven, never asserted.
 *
 * ## Package visibility
 *
 * On Android 11+ `queryIntentActivities` returns only what the manifest's
 * `<queries>` block makes visible. The block there asks for launchable
 * activities and nothing else: this class needs to know what can be opened, and
 * has no business enumerating anything that cannot.
 */
object AppLauncher {

    private const val TAG = "JarvisAppLauncher"

    data class Entry(val label: String, val packageName: String)

    /** Whether a launch started from the service would actually reach the screen. */
    fun available(context: Context): Boolean =
        try {
            Settings.canDrawOverlays(context)
        } catch (e: Exception) {
            // A manufacturer ROM that refuses the query is a ROM where we cannot
            // prove the launch works, which is the same answer as "no".
            Log.w(TAG, "canDrawOverlays refused: ${e.message}")
            false
        }

    /** Everything on this phone that has a launcher entry. */
    fun launchable(context: Context): List<Entry> {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val pm = context.packageManager
        val resolved: List<ResolveInfo> = try {
            pm.queryIntentActivities(intent, 0)
        } catch (e: Exception) {
            Log.w(TAG, "queryIntentActivities failed: ${e.message}")
            return emptyList()
        }
        return resolved.mapNotNull { info ->
            val pkg = info.activityInfo?.packageName ?: return@mapNotNull null
            val label = try {
                info.loadLabel(pm).toString()
            } catch (e: Exception) {
                pkg
            }
            Entry(label = label, packageName = pkg)
        }.distinctBy { it.packageName }
    }

    /**
     * The app the user meant, or null.
     *
     * Four passes, narrowest first, and ties broken by the shortest label then
     * alphabetically. The tie-break is not cosmetic: a phone with "Chrome" and
     * "Chrome Beta" installed must resolve "Chrome" the same way every time, or
     * the same sentence opens a different app on different days.
     *
     * Accents and case are stripped on both sides, so "téléphone" matches
     * "Telephone". Spaces are kept out of the comparison entirely — "Play
     * Store" and "PlayStore" are the same request.
     */
    fun resolve(query: String, entries: List<Entry>): Entry? {
        val want = normalise(query)
        if (want.isEmpty() || entries.isEmpty()) return null

        fun pick(candidates: List<Entry>): Entry? =
            candidates.minWithOrNull(
                compareBy({ it.label.length }, { it.label.lowercase() })
            )

        pick(entries.filter { normalise(it.label) == want })?.let { return it }
        pick(entries.filter { normalise(it.label).startsWith(want) })?.let { return it }
        pick(entries.filter { normalise(it.label).contains(want) })?.let { return it }
        // Last resort: the package name. Catches "youtube" against a label the
        // manufacturer renamed, and costs nothing when the passes above hit.
        pick(entries.filter { normalise(it.packageName).contains(want) })?.let { return it }
        return null
    }

    /**
     * Open [appName]. Returns the sentence the server hands back to the model —
     * always a statement of what happened, never of what was attempted.
     */
    fun open(context: Context, appName: String): String {
        val wanted = appName.trim()
        if (wanted.isEmpty()) return "Aucun nom d'application n'a ete donne."

        if (!available(context)) {
            // Refusing here as well as in `granted()` is deliberate: the
            // permission can be revoked between the registration and the
            // command, and the honest answer then is that it did not happen.
            return "Impossible d'ouvrir une application : l'autorisation " +
                "« Affichage par-dessus les autres applications » n'est pas " +
                "accordee a JARVIS."
        }

        val entries = launchable(context)
        val match = resolve(wanted, entries)
            ?: return "Aucune application nommee « $wanted » sur ce telephone."

        val intent = try {
            context.packageManager.getLaunchIntentForPackage(match.packageName)
        } catch (e: Exception) {
            null
        } ?: return "« ${match.label} » n'a pas d'ecran a ouvrir."

        return try {
            context.startActivity(
                intent.addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED
                )
            )
            Log.i(TAG, "launched ${match.packageName}")
            "${match.label} est ouvert."
        } catch (e: Exception) {
            Log.w(TAG, "startActivity refused for ${match.packageName}: ${e.message}")
            "L'ouverture de « ${match.label} » a echoue : ${e.message}"
        }
    }

    /** Lowercase, accent-free, and without anything that is not a letter or digit. */
    private fun normalise(raw: String): String {
        val decomposed = Normalizer.normalize(raw, Normalizer.Form.NFD)
        val sb = StringBuilder(decomposed.length)
        for (ch in decomposed) {
            if (ch.isLetterOrDigit()) sb.append(ch.lowercaseChar())
        }
        return sb.toString()
    }
}
