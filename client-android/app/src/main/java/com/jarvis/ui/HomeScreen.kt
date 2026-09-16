package com.jarvis.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.AssistantState
import com.jarvis.JarvisState
import com.jarvis.LinkState

/**
 * Deliberately plain. This screen exists to answer four questions while the
 * audio path is being brought up — am I connected, is the microphone on, are
 * bytes moving in both directions, and what went wrong — and it will be
 * replaced once they all answer themselves.
 */
@Composable
fun HomeScreen(
    micGranted: Boolean,
    notificationsGranted: Boolean,
    initialHost: String,
    initialPort: Int,
    initialUseTls: Boolean,
    initialDeviceToken: String,
    initialWakeWord: Boolean,
    onSaveSettings: (host: String, port: Int, tls: Boolean, token: String,
                     wakeWord: Boolean) -> Unit,
    onRequestPermissions: () -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onStartMic: () -> Unit,
    onStopMic: () -> Unit,
    onInterrupt: () -> Unit,
    /** Sends a decision and nothing else. The action runs on the server or not
     *  at all; this callback must never do the thing being confirmed. */
    onConfirm: (id: String, confirmed: Boolean) -> Unit,
) {
    val snap by JarvisState.state.collectAsState()

    var host by remember { mutableStateOf(initialHost) }
    var port by remember { mutableStateOf(initialPort.toString()) }
    var tls by remember { mutableStateOf(initialUseTls) }
    var token by remember { mutableStateOf(initialDeviceToken) }
    var wakeWord by remember { mutableStateOf(initialWakeWord) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF07090F))
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("JARVIS", color = Color(0xFFDDE3ED), fontSize = 28.sp,
             fontWeight = FontWeight.Bold)
        Text("MARK LIII client · prototype", color = Color(0xFF5E6A7E), fontSize = 13.sp)

        StatusCard(snap.link, snap.assistant, snap.nextRetrySeconds, snap.attempt)

        // Above everything else it could be confused with: this is the one thing
        // on the screen that MARK LIII is actually waiting on.
        snap.confirmationId?.let { id ->
            ConfirmationCard(
                title = snap.confirmationTitle,
                detail = snap.confirmationDetail,
                onAnswer = { ok -> onConfirm(id, ok) },
            )
        }

        if (snap.lastError != null) {
            Text(snap.lastError!!, color = Color(0xFFF87171), fontSize = 13.sp)
        }

        // ── counters ─────────────────────────────────────────────────────────
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Counter("TX 16 kHz", "${snap.bytesSent.kb()} · ${snap.framesSent} frames")
                Counter("RX ${snap.downlinkRate.hz()}",
                        "${snap.bytesReceived.kb()} · ${snap.framesReceived} frames")
                // Played is deliberately its own line: RX climbing while Played
                // stays flat is a speaker problem, not a network one, and the
                // two need different fixes.
                Counter("Played", "${snap.bytesPlayed.kb()} · ${snap.secondsPlayed()}")
                Counter("Dropped", snap.framesDropped.toString())
                Counter("Microphone", if (snap.micOpen) "open" else "closed")
                MicMeter(snap.micLevel)
                if (snap.wakeWordName.isNotBlank()) {
                    Counter("Wake word", snap.wakeWordName)
                    if (snap.wakeWordName.startsWith("openWakeWord")) {
                        // The live score is what makes the threshold tunable
                        // instead of guessed: say the word and watch it move.
                        Counter(
                            "  score / gate",
                            "%.3f / %s".format(
                                snap.wakeWordScore,
                                if (snap.gateOpen) "OPEN → server" else "closed (local)",
                            ),
                        )
                    }
                }
            }
        }

        // ── controls ─────────────────────────────────────────────────────────
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(onClick = onConnect, enabled = !snap.serviceRunning) { Text("CONNECT") }
            OutlinedButton(onClick = onDisconnect, enabled = snap.serviceRunning) {
                Text("DISCONNECT")
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(
                onClick = onStartMic,
                enabled = snap.serviceRunning && micGranted && !snap.micOpen,
            ) { Text("START MIC") }
            OutlinedButton(onClick = onStopMic, enabled = snap.micOpen) { Text("STOP MIC") }
        }

        // Enabled whenever there is a link, not only while SPEAKING: `assistant`
        // is the server's last reported state and can lag the voice the user is
        // actually hearing. A button that is greyed out at the moment someone
        // reaches for it is worse than one that occasionally does nothing.
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = onInterrupt, enabled = snap.connected) {
                Text("INTERRUPT")
            }
        }

        if (!micGranted || !notificationsGranted) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "JARVIS needs the microphone, and a notification so you can " +
                            "see when it is listening.",
                        color = Color(0xFFDDE3ED), fontSize = 13.sp,
                    )
                    Button(onClick = onRequestPermissions) { Text("GRANT") }
                }
            }
        }

        HorizontalDivider(color = Color(0xFF1B2230))

        // ── settings ─────────────────────────────────────────────────────────
        Text("Server", color = Color(0xFFDDE3ED), fontWeight = FontWeight.SemiBold)
        OutlinedTextField(
            value = host, onValueChange = { host = it },
            label = { Text("Host (Tailscale name or IP)") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        Row(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = port, onValueChange = { port = it.filter(Char::isDigit).take(5) },
                label = { Text("Port") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(0.45f),
            )
            Switch(checked = tls, onCheckedChange = { tls = it })
            Text("TLS", color = Color(0xFF5E6A7E), fontSize = 13.sp)
        }
        OutlinedTextField(
            value = token, onValueChange = { token = it },
            label = { Text("Device token") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "From the server: python -m server.run_headless --pairing",
            color = Color(0xFF5E6A7E), fontSize = 11.sp, fontFamily = FontFamily.Monospace,
        )
        Row(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Switch(checked = wakeWord, onCheckedChange = { wakeWord = it })
            Column {
                Text("Local wake word (\"Hey Jarvis\")",
                     color = Color(0xFFDDE3ED), fontSize = 13.sp)
                Text(
                    if (wakeWord) "Audio stays on the phone until the word is heard"
                    else "Audio streams continuously while the mic is on",
                    color = Color(0xFF5E6A7E), fontSize = 11.sp,
                )
            }
        }
        Button(
            onClick = {
                onSaveSettings(host, port.toIntOrNull() ?: 8000, tls, token, wakeWord)
            },
            enabled = !snap.serviceRunning,
        ) { Text("SAVE") }

        HorizontalDivider(color = Color(0xFF1B2230))

        Text("Log", color = Color(0xFFDDE3ED), fontWeight = FontWeight.SemiBold)
        snap.log.takeLast(12).reversed().forEach {
            Text(it, color = Color(0xFF8A96A8), fontSize = 12.sp,
                 fontFamily = FontFamily.Monospace)
        }
    }
}

@Composable
private fun StatusCard(
    link: LinkState,
    assistant: AssistantState,
    retryIn: Int,
    attempt: Int,
) {
    val (label, colour) = when (link) {
        LinkState.CONNECTED -> "CONNECTED" to Color(0xFF4ADE80)
        LinkState.CONNECTING -> "CONNECTING" to Color(0xFFFACC15)
        LinkState.RECONNECTING ->
            (if (retryIn > 0) "RECONNECTING · ${retryIn}s (try $attempt)" else "RECONNECTING") to
                Color(0xFFFB923C)
        LinkState.ERROR -> "ERROR" to Color(0xFFF87171)
        LinkState.DISCONNECTED -> "DISCONNECTED" to Color(0xFF5E6A7E)
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label, color = colour, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        if (assistant != AssistantState.UNKNOWN) {
            Text("MARK LIII: ${assistant.name}", color = Color(0xFF8A96A8), fontSize = 13.sp)
        }
    }
}

/**
 * The gate for an irreversible action.
 *
 * It shows and it reports a decision. It does not execute, and it cannot: the
 * only thing it has is an opaque id to hand back. Everything about whether the
 * action runs — the pending request, the 90 s expiry, the action itself — lives
 * on the server.
 *
 * CANCEL is the ordinary-weight button and CONFIRM the outlined one, against
 * the usual convention, because the destructive choice should not be the one
 * the thumb lands on.
 */
@Composable
private fun ConfirmationCard(
    title: String,
    detail: String,
    onAnswer: (Boolean) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color(0xFF2A1A12), RoundedCornerShape(12.dp))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("CONFIRMATION REQUIRED", color = Color(0xFFFB923C),
             fontSize = 12.sp, fontWeight = FontWeight.Bold)
        Text(title, color = Color(0xFFEDE3DA), fontSize = 17.sp,
             fontWeight = FontWeight.Bold)
        if (detail.isNotBlank()) {
            Text(detail, color = Color(0xFFB09C8C), fontSize = 13.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = { onAnswer(false) }) { Text("CANCEL") }
            OutlinedButton(onClick = { onAnswer(true) }) { Text("CONFIRM") }
        }
    }
}

@Composable
private fun Counter(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Color(0xFF8A96A8), fontSize = 13.sp)
        Text(value, color = Color(0xFFDDE3ED), fontSize = 13.sp,
             fontFamily = FontFamily.Monospace)
    }
}

@Composable
private fun MicMeter(level: Float) {
    Box(
        Modifier
            .fillMaxWidth()
            .height(6.dp)
            .background(Color(0xFF1B2230), RoundedCornerShape(3.dp))
    ) {
        Box(
            Modifier
                .fillMaxWidth(level.coerceIn(0f, 1f))
                .height(6.dp)
                .background(Color(0xFF4ADE80), RoundedCornerShape(3.dp))
        )
    }
}

/** Bytes of mono 16-bit PCM → how long that is out loud. */
private fun com.jarvis.JarvisSnapshot.secondsPlayed(): String {
    if (downlinkRate <= 0 || bytesPlayed == 0L) return "0.0 s"
    return String.format("%.1f s", bytesPlayed / 2.0 / downlinkRate)
}

private fun Long.kb(): String =
    if (this < 1024) "$this B" else if (this < 1024 * 1024) "${this / 1024} KB"
    else String.format("%.1f MB", this / 1024.0 / 1024.0)

private fun Int.hz(): String = if (this == 0) "—" else "$this Hz"
