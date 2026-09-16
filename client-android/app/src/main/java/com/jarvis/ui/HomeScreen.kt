package com.jarvis.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.AssistantState
import com.jarvis.JarvisState
import com.jarvis.LinkState

/**
 * The one screen that is about JARVIS rather than about the app.
 *
 * It shows a core, a word, and a sentence. Every number this used to display —
 * bytes, frames, sockets — moved to Settings → Developer, because a person
 * looking at their assistant is not diagnosing it, and a screen that reports
 * `RX 691 KB` is a screen about a client rather than about JARVIS.
 */
@Composable
fun HomeScreen(
    configured: Boolean,
    micGranted: Boolean,
    animationsEnabled: Boolean,
    onRequestPermissions: () -> Unit,
    onOpenSettings: () -> Unit,
    onConnect: () -> Unit,
    onStartMic: () -> Unit,
    onStopMic: () -> Unit,
    onInterrupt: () -> Unit,
    onConfirm: (id: String, confirmed: Boolean) -> Unit,
) {
    val snap by JarvisState.state.collectAsState()

    // One number feeds the core, from whichever side is making sound. While
    // JARVIS speaks, the microphone is either gated or picking up JARVIS itself,
    // so preferring the speaker here is what keeps the core honest.
    val level = if (snap.assistant == AssistantState.SPEAKING) {
        snap.speakerLevel
    } else {
        snap.micLevel
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF05070C))
            .padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(28.dp))
        Text(
            "JARVIS",
            color = Color(0xFFD7E1F2),
            fontSize = 24.sp,
            fontWeight = FontWeight.Light,
            letterSpacing = 8.sp,
        )

        Spacer(Modifier.weight(1f))

        JarvisCore(
            link = snap.link,
            assistant = snap.assistant,
            level = level,
            animated = animationsEnabled,
            // Tapping the core interrupts. It is the largest target on the
            // screen and the one thing the user is already looking at when they
            // want JARVIS to stop talking.
            onTap = if (snap.connected) onInterrupt else null,
        )

        Spacer(Modifier.height(22.dp))

        CaptionLine(snap.assistant, snap.link, snap.lastSpokenLine)

        // Weighted 1 above and 0.45 below, not 1 and 1. Optical centre, not
        // geometric: the core plus its caption is one object, and an object
        // centred by measurement in a column that has controls under it looks
        // like it has slipped upwards, with a hole beneath the caption.
        Spacer(Modifier.weight(0.45f))

        snap.confirmationId?.let { id ->
            ConfirmationCard(
                title = snap.confirmationTitle,
                detail = snap.confirmationDetail,
                onAnswer = { ok -> onConfirm(id, ok) },
            )
            Spacer(Modifier.height(20.dp))
        }

        StatusPill(snap.link, snap.assistant)

        Spacer(Modifier.height(20.dp))

        when {
            !configured -> Hint("No server yet.", "SET UP", onOpenSettings)
            !micGranted -> Hint("JARVIS cannot hear you.", "ALLOW MIC", onRequestPermissions)
            !snap.serviceRunning -> Hint(null, "WAKE JARVIS", onConnect)
            else -> Controls(
                micOpen = snap.micOpen,
                onStartMic = onStartMic,
                onStopMic = onStopMic,
                onInterrupt = onInterrupt,
            )
        }

        Spacer(Modifier.height(28.dp))
    }
}

/**
 * What JARVIS is doing, in words, and the last thing it said.
 *
 * Two lines and never more. The sentence is capped rather than scrolled: a wall
 * of text under the core turns this back into a transcript viewer, and there is
 * a whole screen for that one tab away.
 */
@Composable
private fun CaptionLine(
    assistant: AssistantState,
    link: LinkState,
    lastLine: String?,
) {
    val caption = when {
        link == LinkState.DISCONNECTED -> "Offline."
        link == LinkState.ERROR -> "Cannot reach JARVIS."
        link == LinkState.CONNECTING -> "Waking up…"
        link == LinkState.RECONNECTING -> "Reaching out…"
        assistant == AssistantState.LISTENING -> "I'm listening."
        assistant == AssistantState.THINKING -> "Thinking…"
        assistant == AssistantState.SLEEPING -> "Say \"Hey Jarvis\"."
        else -> null
    }

    Box(
        modifier = Modifier.fillMaxWidth().heightIn(min = 72.dp),
        contentAlignment = Alignment.TopCenter,
    ) {
        AnimatedContent(
            targetState = caption ?: lastLine.orEmpty(),
            transitionSpec = {
                // Deliberately gentle. A typewriter would read as a machine
                // pretending to be a terminal; this reads as something arriving.
                (fadeIn(tween(420)) + slideInVertically(tween(420)) { it / 6 })
                    .togetherWith(fadeOut(tween(160)))
            },
            label = "caption",
        ) { text ->
            Text(
                text = text,
                color = if (caption != null) Color(0xFF8FA0BC) else Color(0xFFCBD7EA),
                fontSize = if (caption != null) 15.sp else 17.sp,
                lineHeight = 24.sp,
                textAlign = TextAlign.Center,
                maxLines = 3,
                // Cut cleanly. Three lines that stop mid-word read as a layout
                // fault; an ellipsis reads as "there is more", which there is —
                // one tab away, in full.
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

/** `● CONNECTED` and nothing else. The one piece of link state on this screen. */
@Composable
private fun StatusPill(link: LinkState, assistant: AssistantState) {
    val (label, colour) = when (link) {
        LinkState.CONNECTED -> when (assistant) {
            AssistantState.LISTENING -> "LISTENING" to Color(0xFF6EE7B7)
            AssistantState.THINKING -> "THINKING" to Color(0xFFC4B5FD)
            AssistantState.SPEAKING -> "SPEAKING" to Color(0xFF7DD3FC)
            AssistantState.SLEEPING -> "ASLEEP" to Color(0xFF64748B)
            AssistantState.UNKNOWN -> "READY" to Color(0xFF93C5FD)
        }
        LinkState.CONNECTING -> "CONNECTING" to Color(0xFFFBBF6B)
        LinkState.RECONNECTING -> "RECONNECTING" to Color(0xFFFBBF6B)
        LinkState.ERROR -> "CONNECTION ERROR" to Color(0xFFF87171)
        LinkState.DISCONNECTED -> "OFFLINE" to Color(0xFF55617A)
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).background(colour, CircleShape))
        Spacer(Modifier.size(9.dp))
        Text(label, color = colour, fontSize = 12.sp,
             fontWeight = FontWeight.Medium, letterSpacing = 2.sp)
    }
}

@Composable
private fun Controls(
    micOpen: Boolean,
    onStartMic: () -> Unit,
    onStopMic: () -> Unit,
    onInterrupt: () -> Unit,
) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        // "MIC", not "LISTEN". The pill above can read LISTENING — that is MARK
        // LIII waiting for input — while this microphone is closed, and two
        // controls a thumb apart that both say some form of "listen" while
        // meaning different things is a caption nobody can be expected to parse.
        if (micOpen) {
            OutlinedButton(onClick = onStopMic) { Text("MUTE") }
        } else {
            Button(onClick = onStartMic) { Text("MIC") }
        }
        OutlinedButton(onClick = onInterrupt) { Text("INTERRUPT") }
    }
}

@Composable
private fun Hint(message: String?, action: String, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        if (message != null) {
            Text(message, color = Color(0xFF6B7A93), fontSize = 13.sp)
            Spacer(Modifier.height(10.dp))
        }
        Button(onClick = onClick) { Text(action) }
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
fun ConfirmationCard(
    title: String,
    detail: String,
    onAnswer: (Boolean) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color(0xFF241812), RoundedCornerShape(16.dp))
            .padding(18.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("CONFIRMATION REQUIRED", color = Color(0xFFFB923C),
             fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
        Text(title, color = Color(0xFFEDE3DA), fontSize = 17.sp,
             fontWeight = FontWeight.Bold)
        if (detail.isNotBlank()) {
            Text(detail, color = Color(0xFFB09C8C), fontSize = 13.sp, lineHeight = 19.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { onAnswer(false) },
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF3C2A20),
                    contentColor = Color(0xFFEDE3DA),
                ),
            ) { Text("CANCEL") }
            OutlinedButton(onClick = { onAnswer(true) }) { Text("CONFIRM") }
        }
    }
}
