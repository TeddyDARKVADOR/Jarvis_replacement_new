package com.jarvis.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.JarvisState

/** Three destinations, and one that is reached only through Settings. */
enum class Tab { HOME, HISTORY, SETTINGS }

/**
 * The shell.
 *
 * Three tabs, no navigation library. The app has three screens and one nested
 * one; a NavHost would add a dependency, a graph and a back-stack contract to
 * express what a single `when` expresses here, and none of those three screens
 * has arguments.
 */
@Composable
fun JarvisApp(
    micGranted: Boolean,
    notificationsGranted: Boolean,
    configured: Boolean,
    host: String,
    port: Int,
    useTls: Boolean,
    deviceToken: String,
    wakeWord: Boolean,
    animations: Boolean,
    onSaveSettings: (host: String, port: Int, tls: Boolean, token: String,
                     wakeWord: Boolean, animations: Boolean) -> Unit,
    onRequestPermissions: () -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onStartMic: () -> Unit,
    onStopMic: () -> Unit,
    onInterrupt: () -> Unit,
    onConfirm: (id: String, confirmed: Boolean) -> Unit,
) {
    var tab by remember { mutableStateOf(Tab.HOME) }
    var showDebug by remember { mutableStateOf(false) }
    val snap by JarvisState.state.collectAsState()

    Column(
        Modifier
            .fillMaxSize()
            .background(Color(0xFF05070C))
            .navigationBarsPadding()
    ) {
        Box(Modifier.weight(1f)) {
            AnimatedContent(
                targetState = if (showDebug) null else tab,
                transitionSpec = {
                    fadeIn(tween(220)).togetherWith(fadeOut(tween(160)))
                },
                label = "screen",
            ) { target ->
                when (target) {
                    null -> DebugScreen(onBack = { showDebug = false })
                    Tab.HOME -> HomeScreen(
                        configured = configured,
                        micGranted = micGranted,
                        animationsEnabled = animations,
                        onRequestPermissions = onRequestPermissions,
                        onOpenSettings = { tab = Tab.SETTINGS },
                        onConnect = onConnect,
                        onStartMic = onStartMic,
                        onStopMic = onStopMic,
                        onInterrupt = onInterrupt,
                        onConfirm = onConfirm,
                    )
                    Tab.HISTORY -> HistoryScreen()
                    Tab.SETTINGS -> SettingsScreen(
                        host = host,
                        port = port,
                        useTls = useTls,
                        deviceToken = deviceToken,
                        wakeWord = wakeWord,
                        animations = animations,
                        notificationsGranted = notificationsGranted,
                        onSave = onSaveSettings,
                        onRequestNotifications = onRequestPermissions,
                        onConnect = onConnect,
                        onDisconnect = onDisconnect,
                        onOpenDebug = { showDebug = true },
                    )
                }
            }
        }

        if (!showDebug) {
            TabBar(
                current = tab,
                // A confirmation is the one thing that must not be missed while
                // the user is reading history. The dot is the whole nudge.
                badgeOnHome = snap.awaitingConfirmation,
                onSelect = { tab = it },
            )
        }
    }
}

@Composable
private fun TabBar(current: Tab, badgeOnHome: Boolean, onSelect: (Tab) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(Color(0xFF080C14))
            .padding(vertical = 12.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        TabItem("JARVIS", current == Tab.HOME, badgeOnHome) { onSelect(Tab.HOME) }
        TabItem("HISTORY", current == Tab.HISTORY, false) { onSelect(Tab.HISTORY) }
        TabItem("SETTINGS", current == Tab.SETTINGS, false) { onSelect(Tab.SETTINGS) }
    }
}

@Composable
private fun TabItem(label: String, selected: Boolean, badge: Boolean, onClick: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.clickable(onClick = onClick).padding(horizontal = 18.dp, vertical = 4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                label,
                color = if (selected) Color(0xFFCBD7EA) else Color(0xFF4A5568),
                fontSize = 11.sp,
                letterSpacing = 2.sp,
                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
            )
            if (badge) {
                Spacer(Modifier.size(6.dp))
                Box(Modifier.size(6.dp).background(Color(0xFFFB923C), CircleShape))
            }
        }
        Spacer(Modifier.height(5.dp))
        Box(
            Modifier
                .size(width = 18.dp, height = 2.dp)
                .background(
                    if (selected) Color(0xFF7DD3FC) else Color.Transparent,
                    CircleShape,
                )
        )
    }
}
