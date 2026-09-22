package com.jarvis.device

import android.content.Context
import android.util.Log
import com.jarvis.net.Protocol
import org.json.JSONObject

/**
 * What this phone can be asked to do, and the one place that does it.
 *
 * Two lists, and the difference between them is the whole point:
 *
 * ```
 *   Protocol.CAPABILITIES   what this BUILD implements   — a fact about the code
 *   granted(context)        what this PHONE can do now   — a fact about the device
 * ```
 *
 * Only the second is ever sent to the server. `AuthManager.registerDevice`
 * declares it, `server/devices.py` stores it, and `server/targeting.py` routes
 * on it — so a capability whose precondition is missing is one the server never
 * learns about, and the command goes to the PC or comes back as a question
 * instead of failing on a phone the user deliberately named.
 *
 * This mirrors `client_desktop/device.py` exactly, which refuses to declare an
 * action whose Python dependency is absent. The phone's dependencies are
 * permissions rather than modules; the rule is the same one.
 *
 * ## Adding the next capability
 *
 * One entry in [Protocol.CAPABILITIES], one `when` branch in [execute], one
 * precondition in [granted], and one test in `jarvis-preprod/tests/devices/`.
 * Nothing else moves — in particular `JarvisClient` does not learn what the
 * capability is, because a network class that knows about torches is a network
 * class that has to change every time the phone grows a new one.
 */
object DeviceCapabilities {

    private const val TAG = "JarvisCapabilities"

    /**
     * The subset of [Protocol.CAPABILITIES] whose precondition holds right now.
     *
     * Never throws: a phone that cannot work out what it can do must still
     * register and connect. It simply declares less.
     */
    fun granted(context: Context): List<String> =
        Protocol.CAPABILITIES.filter { capability ->
            try {
                when (capability) {
                    Protocol.CAP_OPEN_APP -> AppLauncher.available(context)
                    // An entry with no precondition here is a capability nobody
                    // thought about. Withholding it is the safe reading.
                    else -> false
                }
            } catch (e: Exception) {
                Log.w(TAG, "precondition for $capability failed: ${e.message}")
                false
            }
        }

    /** Why a declared-in-the-build capability is not offered, for the debug screen. */
    fun withheldReason(context: Context, capability: String): String? = when {
        capability !in Protocol.CAPABILITIES -> "inconnue de cette version"
        capability == Protocol.CAP_OPEN_APP && !AppLauncher.available(context) ->
            "autorisation « Affichage par-dessus les autres applications » non accordee"
        else -> null
    }

    /**
     * Run a routed command. Returns the sentence that travels back as
     * `device_result` — never null, never an exception.
     *
     * The caller has already checked the target and the declaration; this
     * checks the declaration again against what is granted *now*, because a
     * permission revoked after registration is exactly the case where the
     * server's view and the phone's truth disagree.
     */
    fun execute(context: Context, action: String, parameters: JSONObject): String {
        if (action !in granted(context)) {
            val reason = withheldReason(context, action)
            return if (reason != null)
                "Refuse : « $action » n'est pas disponible sur ce telephone ($reason)."
            else
                "Refuse : « $action » ne fait pas partie des capacites de ce telephone."
        }

        return try {
            when (action) {
                Protocol.CAP_OPEN_APP ->
                    AppLauncher.open(context, parameters.optString("app_name"))

                // Unreachable while `granted` is a filter over CAPABILITIES,
                // and kept so that adding a name without adding a branch fails
                // out loud instead of silently doing nothing.
                else -> "« $action » est declaree mais n'a pas d'implementation."
            }
        } catch (e: Exception) {
            Log.w(TAG, "$action crashed: ${e.message}")
            "L'action « $action » a echoue : ${e.message}"
        }
    }
}
