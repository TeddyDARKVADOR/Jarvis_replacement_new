package com.jarvis.ui

import android.os.SystemClock
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import com.jarvis.AssistantState
import com.jarvis.LinkState
import kotlin.math.cos
import kotlin.math.sin

/**
 * JARVIS, drawn.
 *
 * One Canvas, four primitives — a filled centre, two arcs, a ring, a halo — and
 * everything else is those five values moving. There is no particle system and
 * no shader, because this composable is on screen the entire time the app is
 * open, next to a microphone and a 24 kHz audio stream, on a phone the user
 * expects to last the day.
 *
 * **What drives it.** Two numbers and one enum:
 *
 * ```
 *   level      0..1   the voice being heard — the user's or JARVIS's
 *   rotation   0..1   a slow sweep, only where a state calls for one
 *   state             what MARK LIII says it is doing
 * ```
 *
 * `level` is deliberately the *same* input for both directions. A core that
 * pulsed differently depending on who was speaking would be showing the
 * plumbing; what it should show is that something is being said.
 */
@Composable
fun JarvisCore(
    link: LinkState,
    assistant: AssistantState,
    /** 0..1 from whichever side is currently making sound. */
    level: Float,
    animated: Boolean,
    /**
     * `elapsedRealtime()` of the last time the wake word opened the gate.
     *
     * Drives a single expanding ring. That instant is the product's whole
     * promise — the phone decided your voice may leave it — and it had no
     * representation at all: the state changed, the core did not.
     */
    wokeAt: Long = 0L,
    modifier: Modifier = Modifier,
    onTap: (() -> Unit)? = null,
) {
    val mood = remember(link, assistant) { moodOf(link, assistant) }

    // A slow continuous sweep. Started once and left running: an infinite
    // transition that is not read by the current mood costs a recomposition of
    // nothing, and starting and stopping it on every state change is what makes
    // an animation stutter at exactly the moment the user is looking at it.
    val sweep by rememberInfiniteTransition(label = "sweep")
        .animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(mood.sweepMillis, easing = LinearEasing),
                repeatMode = RepeatMode.Restart,
            ),
            label = "sweepValue",
        )

    // The breath: present even at rest, so the core never looks frozen or
    // crashed. It is slow enough to be felt rather than watched.
    val breath by rememberInfiniteTransition(label = "breath")
        .animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(mood.breathMillis, easing = LinearEasing),
                repeatMode = RepeatMode.Reverse,
            ),
            label = "breathValue",
        )

    // Voice drives the radius, but not directly: raw RMS jitters frame to frame
    // and the core would shiver. A short asymmetric smoothing — quick to grow,
    // slower to fall — tracks a syllable without chasing every sample.
    val target = if (animated) level.coerceIn(0f, 1f) else 0f
    val smoothed by animateFloatAsState(
        targetValue = target,
        animationSpec = tween(if (target > 0.02f) 90 else 260, easing = LinearEasing),
        label = "level",
    )

    val breathAmount = if (animated) breath else 0.5f
    val sweepAmount = if (animated) sweep else 0f

    // The wake acknowledgement: one ring, 0 → 1, then gone. Driven off the
    // timestamp rather than a boolean so a wake that happens while the screen is
    // off does not fire a stale animation when the user looks at it later.
    val wake = remember { Animatable(1f) }
    LaunchedEffect(wokeAt) {
        if (wokeAt > 0L && SystemClock.elapsedRealtime() - wokeAt < 1_500) {
            wake.snapTo(0f)
            wake.animateTo(1f, tween(700, easing = LinearEasing))
        }
    }

    Box(
        modifier = modifier
            .size(240.dp)
            .then(
                if (onTap != null) {
                    Modifier.pointerInput(Unit) {
                        detectTapGestures(onTap = { onTap() })
                    }
                } else Modifier
            )
    ) {
        Canvas(Modifier.size(240.dp)) {
            drawCore(mood, breathAmount, sweepAmount, smoothed, wake.value)
        }
    }
}

// ── what each state looks like ───────────────────────────────────────────────

private data class Mood(
    val colour: Color,
    val dim: Color,
    /** How far the ring sits from the centre at rest, as a fraction of radius. */
    val ringScale: Float,
    val breathMillis: Int,
    val sweepMillis: Int,
    /** 0 = no arcs, 1 = arcs at full opacity. */
    val arcs: Float,
    /** How much `level` is allowed to move the core. */
    val reactivity: Float,
)

private fun moodOf(link: LinkState, assistant: AssistantState): Mood = when {
    // The link comes first: what MARK LIII last said it was doing is not
    // interesting while the phone cannot reach it.
    link == LinkState.ERROR -> Mood(
        colour = Color(0xFFF87171), dim = Color(0xFF7F2D2D),
        ringScale = 0.78f, breathMillis = 2200, sweepMillis = 6000,
        arcs = 0f, reactivity = 0f,
    )
    link == LinkState.DISCONNECTED -> Mood(
        colour = Color(0xFF55617A), dim = Color(0xFF232A3A),
        ringScale = 0.74f, breathMillis = 5200, sweepMillis = 9000,
        arcs = 0f, reactivity = 0f,
    )
    link == LinkState.CONNECTING || link == LinkState.RECONNECTING -> Mood(
        colour = Color(0xFFFBBF6B), dim = Color(0xFF6A4A22),
        ringScale = 0.80f, breathMillis = 1800, sweepMillis = 2600,
        arcs = 0.55f, reactivity = 0f,
    )
    assistant == AssistantState.SPEAKING -> Mood(
        colour = Color(0xFF7DD3FC), dim = Color(0xFF1E4C63),
        ringScale = 0.84f, breathMillis = 1400, sweepMillis = 5200,
        arcs = 1f, reactivity = 1f,
    )
    assistant == AssistantState.THINKING -> Mood(
        colour = Color(0xFFC4B5FD), dim = Color(0xFF3B3266),
        ringScale = 0.82f, breathMillis = 1100, sweepMillis = 1700,
        arcs = 0.9f, reactivity = 0f,
    )
    assistant == AssistantState.LISTENING -> Mood(
        colour = Color(0xFF6EE7B7), dim = Color(0xFF1F5145),
        ringScale = 0.82f, breathMillis = 2400, sweepMillis = 7000,
        arcs = 0.7f, reactivity = 0.85f,
    )
    assistant == AssistantState.SLEEPING -> Mood(
        colour = Color(0xFF64748B), dim = Color(0xFF232A3A),
        ringScale = 0.76f, breathMillis = 4600, sweepMillis = 9000,
        arcs = 0f, reactivity = 0f,
    )
    // Connected, nothing reported yet: READY.
    else -> Mood(
        colour = Color(0xFF93C5FD), dim = Color(0xFF243B57),
        ringScale = 0.80f, breathMillis = 3200, sweepMillis = 8000,
        arcs = 0.35f, reactivity = 0.2f,
    )
}

// ── the drawing ──────────────────────────────────────────────────────────────

private fun DrawScope.drawCore(
    mood: Mood,
    breath: Float,
    sweep: Float,
    level: Float,
    /** 0 = the wake ring has just started, 1 = finished and invisible. */
    wake: Float,
) {
    val c = center
    val unit = size.minDimension / 2f
    val voice = level * mood.reactivity

    // Everything scales off one number so the parts stay in proportion when the
    // voice moves them.
    val pulse = 1f + (breath - 0.5f) * 0.05f + voice * 0.16f

    // Halo. Drawn first and very soft — it is what stops the core reading as a
    // flat sticker on a black rectangle.
    drawCircle(
        brush = Brush.radialGradient(
            colors = listOf(mood.dim.copy(alpha = 0.55f), Color.Transparent),
            center = c,
            radius = unit * (0.95f * pulse),
        ),
        radius = unit * (0.95f * pulse),
        center = c,
    )

    // The ring the arcs live on.
    val ringRadius = unit * mood.ringScale * pulse
    drawCircle(
        color = mood.colour.copy(alpha = 0.16f + voice * 0.20f),
        radius = ringRadius,
        center = c,
        style = Stroke(width = 1.5.dp.toPx()),
    )

    // Two arcs, opposed, sweeping. Two rather than one because a single arc
    // reads as a loading spinner, and JARVIS is not loading.
    if (mood.arcs > 0.01f) {
        val alpha = mood.arcs * (0.55f + voice * 0.45f)
        rotate(degrees = sweep * 360f, pivot = c) {
            repeat(2) { i ->
                drawArc(
                    color = mood.colour.copy(alpha = alpha),
                    startAngle = 12f + i * 180f,
                    sweepAngle = 56f,
                    useCenter = false,
                    topLeft = Offset(c.x - ringRadius, c.y - ringRadius),
                    size = androidx.compose.ui.geometry.Size(ringRadius * 2, ringRadius * 2),
                    style = Stroke(width = 2.5.dp.toPx()),
                )
            }
        }
    }

    // Orbiting marks — the "◌ ◌" of the sketch. They carry the sweep at a
    // different rate so the whole thing does not turn as one rigid object.
    if (mood.arcs > 0.01f) {
        val orbit = ringRadius * 0.98f
        repeat(3) { i ->
            val a = (sweep * -1.4f + i / 3f) * 2f * Math.PI.toFloat()
            drawCircle(
                color = mood.colour.copy(alpha = mood.arcs * (0.35f + voice * 0.5f)),
                radius = (1.8f + voice * 2.2f).dp.toPx(),
                center = Offset(c.x + cos(a) * orbit, c.y + sin(a) * orbit),
            )
        }
    }

    // Inner ring: the one the voice actually moves, so amplitude has somewhere
    // to go that is not the outline.
    drawCircle(
        color = mood.colour.copy(alpha = 0.30f + voice * 0.35f),
        radius = unit * (0.46f + voice * 0.20f) * pulse,
        center = c,
        style = Stroke(width = 1.dp.toPx()),
    )

    // "Hey Jarvis" was heard. One ring leaving the core, fading as it goes —
    // drawn over everything so it reads even mid-sentence.
    if (wake < 1f) {
        drawCircle(
            color = Color.White.copy(alpha = (1f - wake) * 0.5f),
            radius = unit * (0.30f + wake * 0.72f),
            center = c,
            style = Stroke(width = (2.5f * (1f - wake) + 0.5f).dp.toPx()),
        )
    }

    // The centre. Always lit, always the brightest thing on the screen: it is
    // the one element that says the assistant exists at all.
    drawCircle(
        brush = Brush.radialGradient(
            colors = listOf(
                Color.White.copy(alpha = 0.92f),
                mood.colour,
                mood.colour.copy(alpha = 0f),
            ),
            center = c,
            radius = unit * (0.30f + voice * 0.10f) * pulse,
        ),
        radius = unit * (0.30f + voice * 0.10f) * pulse,
        center = c,
    )
}
