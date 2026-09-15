package com.jarvis.auth

import android.content.Context
import com.jarvis.net.ServerEndpoint
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Where the phone keeps its identity, and how it turns it into a bearer token.
 *
 * **One credential, issued by the server, exchanged for a short-lived token.**
 * `server/auth.py` writes `config/device_credentials.json` on the VPS and seeds
 * it into the dashboard's own `_device_sessions`. The phone stores that
 * device_token and calls the endpoint that already existed:
 *
 *     POST /api/device-login  {"device_token": "…"} → {"ok", "token", "key"}
 *
 * The bearer it gets back is what goes on every socket. It lives in the
 * server's RAM, so a server restart invalidates it and the phone simply logs in
 * again — which is precisely why the *device* token is the thing worth storing
 * and the bearer is not.
 *
 * **No Gemini key is ever on this phone.** The device token is authority over
 * JARVIS, not over the API key; the key stays in `config/api_keys.json` on the
 * server and is never sent anywhere.
 *
 * **Storage.** SharedPreferences in the app's private directory: unreadable by
 * other apps, included in cloud backup unless the app opts out. Not hardware-
 * backed. For a prototype paired to a personal tailnet that is the honest
 * trade; moving it to EncryptedSharedPreferences or the Keystore is a
 * self-contained change to this file and nothing else.
 */
class AuthManager(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    data class Session(val bearer: String, val sessionKey: String)

    var endpoint: ServerEndpoint
        get() = ServerEndpoint(
            host = prefs.getString(KEY_HOST, "") ?: "",
            port = prefs.getInt(KEY_PORT, 8000),
            useTls = prefs.getBoolean(KEY_TLS, false),
        )
        set(value) = prefs.edit()
            .putString(KEY_HOST, value.host)
            .putInt(KEY_PORT, value.port)
            .putBoolean(KEY_TLS, value.useTls)
            .apply()

    var deviceToken: String
        get() = prefs.getString(KEY_DEVICE, "") ?: ""
        set(value) = prefs.edit().putString(KEY_DEVICE, value.trim()).apply()

    val isConfigured: Boolean
        get() = endpoint.isUsable && deviceToken.isNotBlank()

    /**
     * Local wake word on or off. Lives here because this class already owns the
     * app's only SharedPreferences file; a second settings store for one boolean
     * would be worse than the slight mismatch with the class name.
     *
     * Off by default — see WakeWordEngines.default.
     */
    var wakeWordEnabled: Boolean
        get() = prefs.getBoolean(KEY_WAKEWORD, false)
        set(value) = prefs.edit().putBoolean(KEY_WAKEWORD, value).apply()

    /** Last successful exchange. Kept only in memory — see the class note. */
    @Volatile
    var session: Session? = null
        private set

    /**
     * Blocking. Call from a background thread.
     *
     * Throws [AuthRejected] when the server refuses the device token — a
     * distinct type on purpose, because that is the one failure the reconnect
     * loop must NOT retry: hammering a rejected credential never becomes a
     * success, it only fills the log.
     */
    @Throws(Exception::class)
    fun login(): Session {
        val ep = endpoint
        require(ep.isUsable) { "No server configured" }
        val token = deviceToken
        require(token.isNotBlank()) { "No device token" }

        val body = JSONObject().put("device_token", token).toString()
            .toRequestBody("application/json".toMediaType())

        http.newCall(Request.Builder().url(ep.deviceLogin).post(body).build())
            .execute().use { response ->
                if (response.code == 401) {
                    throw AuthRejected(
                        "The server does not know this device token. Re-pair " +
                            "with `python -m server.run_headless --pairing`."
                    )
                }
                if (!response.isSuccessful) {
                    throw IllegalStateException("device-login failed: HTTP ${response.code}")
                }
                val json = JSONObject(response.body.string())
                if (!json.optBoolean("ok")) {
                    throw IllegalStateException("device-login refused: $json")
                }
                val s = Session(
                    bearer = json.getString("token"),
                    sessionKey = json.optString("key", ""),
                )
                session = s
                return s
            }
    }

    fun forgetSession() {
        session = null
    }

    class AuthRejected(message: String) : Exception(message)

    private companion object {
        const val PREFS = "jarvis_client"
        const val KEY_HOST = "host"
        const val KEY_PORT = "port"
        const val KEY_TLS = "tls"
        const val KEY_DEVICE = "device_token"
        const val KEY_WAKEWORD = "wake_word_enabled"
    }
}
