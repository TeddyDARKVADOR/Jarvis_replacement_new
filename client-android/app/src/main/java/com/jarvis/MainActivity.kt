package com.jarvis

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import com.jarvis.auth.AuthManager
import com.jarvis.net.ServerEndpoint
import com.jarvis.service.JarvisForegroundService
import com.jarvis.ui.HomeScreen

/**
 * The only screen. It draws state and starts or stops the service — it owns no
 * socket, no microphone and no audio, so it can be destroyed and recreated
 * (rotation, back, task switch) without interrupting anything.
 *
 * **Permissions are requested here and nowhere else**, because a permission
 * dialog needs a visible Activity. The service refuses to record without them
 * rather than assuming; see JarvisForegroundService.startMic.
 */
class MainActivity : ComponentActivity() {

    private lateinit var auth: AuthManager

    private var micGranted by mutableStateOf(false)
    private var notificationsGranted by mutableStateOf(false)

    private val requestPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { refreshPermissions() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        auth = AuthManager(this)
        refreshPermissions()

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                HomeScreen(
                    micGranted = micGranted,
                    notificationsGranted = notificationsGranted,
                    initialHost = auth.endpoint.host,
                    initialPort = auth.endpoint.port,
                    initialUseTls = auth.endpoint.useTls,
                    initialDeviceToken = auth.deviceToken,
                    initialWakeWord = auth.wakeWordEnabled,
                    onSaveSettings = { host, port, tls, token, wakeWord ->
                        auth.endpoint = ServerEndpoint(host.trim(), port, tls)
                        auth.deviceToken = token
                        auth.wakeWordEnabled = wakeWord
                        JarvisState.log(
                            "Settings saved for $host:$port · wake word " +
                                if (wakeWord) "on" else "off"
                        )
                    },
                    onRequestPermissions = ::askForPermissions,
                    onConnect = {
                        JarvisForegroundService.send(this, JarvisForegroundService.ACTION_START)
                    },
                    onDisconnect = {
                        JarvisForegroundService.send(this, JarvisForegroundService.ACTION_STOP)
                    },
                    onStartMic = {
                        JarvisForegroundService.send(this, JarvisForegroundService.ACTION_MIC_ON)
                    },
                    onStopMic = {
                        JarvisForegroundService.send(this, JarvisForegroundService.ACTION_MIC_OFF)
                    },
                    onInterrupt = {
                        JarvisForegroundService.send(this, JarvisForegroundService.ACTION_INTERRUPT)
                    },
                    onConfirm = { id, confirmed ->
                        // Carries the user's answer to the service, which sends
                        // it on. The Activity decides nothing and runs nothing.
                        JarvisForegroundService.sendConfirmation(this, id, confirmed)
                    },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // The user may have just come back from the system settings screen, and
        // that is the most likely reason for leaving this app at all.
        refreshPermissions()
    }

    private fun askForPermissions() {
        val wanted = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // Android 13+: without it the ongoing notification never appears,
            // and the service is not allowed to be invisible.
            wanted += Manifest.permission.POST_NOTIFICATIONS
        }
        requestPermissions.launch(wanted.toTypedArray())
    }

    private fun refreshPermissions() {
        micGranted = granted(Manifest.permission.RECORD_AUDIO)
        notificationsGranted =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                granted(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                true   // implicit before Android 13
            }
    }

    private fun granted(permission: String): Boolean =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED
}
