package com.jarvis.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * One palette, dark, and no light variant.
 *
 * JARVIS is a lit core on a near-black field; a light theme would not be the
 * same design with different values, it would be a different design. Rather than
 * ship a second one badly, this one is the only one.
 *
 * The greys are blue-shifted (0xFF05070C, not 0xFF0A0A0A). On an OLED panel a
 * neutral near-black next to a saturated cyan core reads as slightly brown; a
 * few points of blue in the background is what stops that.
 */
private val Sky = Color(0xFF7DD3FC)
private val Ink = Color(0xFF05070C)
private val Panel = Color(0xFF0A101A)
private val Text = Color(0xFFCBD7EA)
private val Muted = Color(0xFF6B7A93)

private val JarvisColours = darkColorScheme(
    primary = Sky,
    onPrimary = Color(0xFF04202C),
    primaryContainer = Color(0xFF12324A),
    onPrimaryContainer = Sky,

    secondary = Color(0xFF93C5FD),
    onSecondary = Color(0xFF071A2B),

    background = Ink,
    onBackground = Text,
    surface = Panel,
    onSurface = Text,
    surfaceVariant = Color(0xFF111A28),
    onSurfaceVariant = Muted,

    outline = Color(0xFF2A3547),
    outlineVariant = Color(0xFF141C2A),

    error = Color(0xFFF87171),
    onError = Color(0xFF2A0F0F),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = JarvisColours, content = content)
