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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.JarvisState

/**
 * Five things, and the way out to the technical view.
 *
 * Everything here changes behaviour. Nothing here reports it — no counters, no
 * socket state, no byte totals. Those are one tap further in, behind
 * "Developer", because a settings screen that also diagnoses is how an assistant
 * turns back into an admin console.
 */
@Composable
fun SettingsScreen(
    host: String,
    port: Int,
    useTls: Boolean,
    deviceToken: String,
    wakeWord: Boolean,
    animations: Boolean,
    notificationsGranted: Boolean,
    onSave: (host: String, port: Int, tls: Boolean, token: String,
             wakeWord: Boolean, animations: Boolean) -> Unit,
    onRequestNotifications: () -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onOpenDebug: () -> Unit,
) {
    val snap by JarvisState.state.collectAsState()

    var hostField by remember { mutableStateOf(host) }
    var portField by remember { mutableStateOf(port.toString()) }
    var tlsField by remember { mutableStateOf(useTls) }
    var tokenField by remember { mutableStateOf(deviceToken) }
    var wakeField by remember { mutableStateOf(wakeWord) }
    var animField by remember { mutableStateOf(animations) }

    fun save() = onSave(
        hostField.trim(),
        portField.toIntOrNull() ?: 8000,
        tlsField,
        tokenField.trim(),
        wakeField,
        animField,
    )

    Column(
        Modifier
            .fillMaxSize()
            .background(Color(0xFF05070C))
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp),
    ) {
        Text(
            "SETTINGS",
            color = Color(0xFF6B7A93), fontSize = 12.sp, letterSpacing = 3.sp,
            modifier = Modifier.padding(top = 28.dp, bottom = 22.dp),
        )

        // ── behaviour ────────────────────────────────────────────────────────

        Toggle(
            label = "Wake word",
            detail = "Listen for \"Hey Jarvis\" on the phone. Audio stays here " +
                     "until the word is heard.",
            checked = wakeField,
            onChange = { wakeField = it; save() },
        )

        Toggle(
            label = "Animations",
            detail = "The core still changes with JARVIS's state when this is " +
                     "off — it just stops moving.",
            checked = animField,
            onChange = { animField = it; save() },
        )

        SectionGap()

        // ── notifications ────────────────────────────────────────────────────

        Label("Notifications")
        Detail(
            if (notificationsGranted) {
                "Allowed. The ongoing notification is what keeps JARVIS running " +
                    "when the screen is off."
            } else {
                "Not allowed. Android will not let JARVIS stay connected in the " +
                    "background without it."
            }
        )
        if (!notificationsGranted) {
            Spacer(Modifier.height(10.dp))
            Button(onClick = onRequestNotifications) { Text("ALLOW") }
        }

        SectionGap()

        // ── connection ───────────────────────────────────────────────────────

        Label("Connection")
        Spacer(Modifier.height(10.dp))

        Field(hostField, { hostField = it }, "Address", "Tailscale name or IP")
        Spacer(Modifier.height(10.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Field(portField, { portField = it.filter(Char::isDigit) }, "Port",
                      number = true)
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Switch(checked = tlsField, onCheckedChange = { tlsField = it })
                Spacer(Modifier.height(0.dp))
                Text("  TLS", color = Color(0xFF8FA0BC), fontSize = 13.sp)
            }
        }
        Spacer(Modifier.height(10.dp))

        // Masked by default. It is full control of JARVIS, this screen is the
        // one people open to read their address out loud to someone, and a
        // secret that is visible by default is a secret that eventually gets
        // photographed, screen-shared or typed into the wrong box.
        var tokenVisible by remember { mutableStateOf(false) }
        Field(
            tokenField, { tokenField = it }, "Device token",
            "From the server: python -m server.run_headless --pairing",
            secret = !tokenVisible,
        )
        TextButton(onClick = { tokenVisible = !tokenVisible }) {
            Text(
                if (tokenVisible) "Hide token" else "Show token",
                color = Color(0xFF6B7A93), fontSize = 12.sp,
            )
        }

        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = { save() }) { Text("SAVE") }
            if (snap.serviceRunning) {
                OutlinedButton(onClick = onDisconnect) { Text("DISCONNECT") }
            } else {
                OutlinedButton(onClick = { save(); onConnect() }) { Text("CONNECT") }
            }
        }

        SectionGap()

        TextButton(onClick = onOpenDebug) {
            Text("Developer  ›", color = Color(0xFF6B7A93), fontSize = 14.sp)
        }

        Spacer(Modifier.height(40.dp))
    }
}

// ── small pieces ─────────────────────────────────────────────────────────────

@Composable
private fun Toggle(
    label: String,
    detail: String,
    checked: Boolean,
    onChange: (Boolean) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 10.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Column(Modifier.weight(1f).padding(end = 16.dp)) {
            Label(label)
            Detail(detail)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun Label(text: String) =
    Text(text, color = Color(0xFFCBD7EA), fontSize = 16.sp,
         fontWeight = FontWeight.Medium)

@Composable
private fun Detail(text: String) =
    Text(text, color = Color(0xFF5E6A7E), fontSize = 12.sp, lineHeight = 17.sp,
         modifier = Modifier.padding(top = 3.dp))

@Composable
private fun SectionGap() {
    Spacer(Modifier.height(18.dp))
    Spacer(
        Modifier
            .fillMaxWidth()
            .height(1.dp)
            .background(Color(0xFF141C2A))
    )
    Spacer(Modifier.height(18.dp))
}

@Composable
private fun Field(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    placeholder: String = "",
    number: Boolean = false,
    secret: Boolean = false,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        placeholder = if (placeholder.isBlank()) null else {
            { Text(placeholder, fontSize = 12.sp) }
        },
        singleLine = true,
        visualTransformation =
            if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(
            keyboardType = when {
                number -> KeyboardType.Number
                secret -> KeyboardType.Password
                else -> KeyboardType.Text
            }
        ),
        modifier = Modifier.fillMaxWidth(),
    )
}
