package com.jarvis.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.JarvisState
import java.util.Locale

/**
 * Everything the home screen used to show, and a little more.
 *
 * This is the screen that diagnoses. Keeping it whole and keeping it *here* is
 * what let the home screen become about JARVIS: nothing had to be deleted to
 * make room, it only had to stop being the first thing anyone saw.
 */
@Composable
fun DebugScreen(onBack: () -> Unit) {
    val snap by JarvisState.state.collectAsState()

    Column(
        Modifier
            .fillMaxSize()
            .background(Color(0xFF05070C))
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp),
    ) {
        TextButton(onClick = onBack, modifier = Modifier.padding(top = 16.dp)) {
            Text("‹  Settings", color = Color(0xFF6B7A93), fontSize = 14.sp)
        }

        Text("DEVELOPER", color = Color(0xFF6B7A93), fontSize = 12.sp,
             letterSpacing = 3.sp, modifier = Modifier.padding(bottom = 18.dp))

        Card {
            Line("Link", snap.link.name)
            Line("MARK LIII", snap.assistant.name)
            Line("Service", if (snap.serviceRunning) "running" else "stopped")
            if (snap.attempt > 0) {
                Line("Retry", "attempt ${snap.attempt}, ${snap.nextRetrySeconds}s")
            }
        }

        Gap()
        Card {
            Line("TX 16 kHz", "${bytes(snap.bytesSent)} · ${snap.framesSent} frames")
            Line("RX ${rate(snap.downlinkRate)}",
                 "${bytes(snap.bytesReceived)} · ${snap.framesReceived} frames")
            Line("Played", "${bytes(snap.bytesPlayed)} · ${seconds(snap)}")
            Line("Dropped", snap.framesDropped.toString())
            Line("Microphone", if (snap.micOpen) "open" else "closed")
        }

        Gap()
        Card {
            Line("Notifications", "${snap.notificationsReceived} received")
            Line("Shown", snap.notificationsShown.toString())
            // Climbing while "Shown" stays flat is dedup working, not a fault:
            // the server re-offers recent notifications on every reconnect.
            Line("Duplicate", snap.notificationsDuplicate.toString())
            Line("Dropped", snap.notificationsDropped.toString(),
                 valueColour = if (snap.notificationsDropped > 0) Color(0xFFF87171)
                               else Color(0xFFCBD7EA))
        }

        Gap()
        Card {
            Line("Wake word", snap.wakeWordName.ifBlank { "—" })
            Line("Score / gate", String.format(Locale.US, "%.3f", snap.wakeWordScore) +
                 "  /  " + if (snap.gateOpen) "OPEN → server" else "closed (local)")
            Line("Mic level", String.format(Locale.US, "%.2f", snap.micLevel))
            Line("Speaker level", String.format(Locale.US, "%.2f", snap.speakerLevel))
        }

        snap.lastError?.let {
            Gap()
            Card(tint = Color(0xFF241416)) {
                Line("Last error", it, valueColour = Color(0xFFF87171))
            }
        }

        Gap()
        Text("LOG", color = Color(0xFF6B7A93), fontSize = 11.sp, letterSpacing = 2.sp)
        Spacer(Modifier.height(8.dp))
        Column(
            Modifier
                .fillMaxWidth()
                .background(Color(0xFF0A101A), RoundedCornerShape(12.dp))
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            if (snap.log.isEmpty()) {
                Text("—", color = Color(0xFF3F4A5E), fontSize = 12.sp)
            } else {
                snap.log.takeLast(40).forEach {
                    Text(it, color = Color(0xFF7E8CA3), fontSize = 11.sp,
                         fontFamily = FontFamily.Monospace, lineHeight = 15.sp)
                }
            }
        }

        Spacer(Modifier.height(40.dp))
    }
}

// ── small pieces ─────────────────────────────────────────────────────────────

@Composable
private fun Card(tint: Color = Color(0xFF0A101A), content: @Composable () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(tint, RoundedCornerShape(12.dp))
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(9.dp),
    ) { content() }
}

@Composable
private fun Line(label: String, value: String, valueColour: Color = Color(0xFFCBD7EA)) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Color(0xFF6B7A93), fontSize = 12.sp)
        Text(value, color = valueColour, fontSize = 12.sp,
             fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun Gap() = Spacer(Modifier.height(12.dp))

private fun bytes(n: Long): String = when {
    n >= 1_048_576 -> String.format(Locale.US, "%.1f MB", n / 1_048_576.0)
    n >= 1024 -> String.format(Locale.US, "%.1f KB", n / 1024.0)
    else -> "$n B"
}

private fun rate(hz: Int): String = if (hz > 0) "$hz Hz" else "—"

private fun seconds(snap: com.jarvis.JarvisSnapshot): String {
    val hz = snap.downlinkRate
    if (hz <= 0) return "—"
    // PCM16 mono: two bytes per sample.
    return String.format(Locale.US, "%.1f s", snap.bytesPlayed / 2.0 / hz)
}
