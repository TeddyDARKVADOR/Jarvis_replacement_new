package com.jarvis.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.JarvisState
import com.jarvis.Message
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The conversation.
 *
 * Fed entirely by what the server already sends: `log` events, and the last 50
 * of them replayed on every connect. Nothing is requested, nothing is stored on
 * disk, and the app holds no memory of its own — MARK LIII's memory is the only
 * memory, and a second copy here would be a second thing to be wrong.
 */
@Composable
fun HistoryScreen() {
    val snap by JarvisState.state.collectAsState()
    val listState = rememberLazyListState()

    // Follow the conversation. Someone who has scrolled up is reading, so only
    // jump when a new turn arrives while they are already at the end.
    LaunchedEffect(snap.messages.size) {
        if (snap.messages.isNotEmpty()) {
            listState.animateScrollToItem(snap.messages.lastIndex)
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(Color(0xFF05070C))
            .padding(horizontal = 20.dp),
    ) {
        Text(
            "HISTORY",
            color = Color(0xFF6B7A93),
            fontSize = 12.sp,
            letterSpacing = 3.sp,
            modifier = Modifier.padding(top = 28.dp, bottom = 18.dp),
        )

        if (snap.messages.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "Nothing said yet.",
                    color = Color(0xFF3F4A5E),
                    fontSize = 14.sp,
                    modifier = Modifier.padding(bottom = 80.dp),
                )
            }
        } else {
            LazyColumn(
                state = listState,
                verticalArrangement = Arrangement.spacedBy(14.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(snap.messages) { Turn(it) }
            }
        }
    }
}

@Composable
private fun Turn(message: Message) {
    val jarvis = message.fromJarvis
    Column(
        Modifier
            .fillMaxWidth()
            .background(
                if (jarvis) Color(0xFF0D1522) else Color.Transparent,
                RoundedCornerShape(14.dp),
            )
            .padding(if (jarvis) 14.dp else 2.dp),
    ) {
        Row(
            who = if (jarvis) "JARVIS" else "YOU",
            colour = if (jarvis) Color(0xFF7DD3FC) else Color(0xFF6B7A93),
            atMillis = message.atMillis,
        )
        Text(
            message.text,
            color = if (jarvis) Color(0xFFCBD7EA) else Color(0xFF93A2B8),
            fontSize = 15.sp,
            lineHeight = 22.sp,
            modifier = Modifier.padding(top = 5.dp),
        )
    }
}

@Composable
private fun Row(who: String, colour: Color, atMillis: Long) {
    androidx.compose.foundation.layout.Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(who, color = colour, fontSize = 10.sp,
             fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
        if (atMillis > 0) {
            Text(
                SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(atMillis)),
                color = Color(0xFF3F4A5E),
                fontSize = 10.sp,
            )
        }
    }
}
