package com.jarvis

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/**
 * What the app knows, in one place, observable from Compose.
 *
 * **Why a singleton and not a bound Service.** The foreground service owns the
 * sockets and the audio; the Activity only draws. Binding them with AIDL or a
 * Binder would add a lifecycle to get wrong for a screen that shows five
 * numbers. A process-wide StateFlow is the smaller correct thing: the service
 * writes, the UI collects, and an Activity that is destroyed and recreated
 * (rotation, back, task switch) picks the state back up with nothing to
 * reconnect.
 *
 * The one rule: **only the service writes.** The UI reads, and asks for changes
 * by starting or stopping the service.
 */
enum class LinkState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    RECONNECTING,
    ERROR,
}

/** What MARK LIII says it is doing, straight from its own `set_state` stream. */
enum class AssistantState {
    UNKNOWN, LISTENING, THINKING, SPEAKING, SLEEPING
}

data class JarvisSnapshot(
    val link: LinkState = LinkState.DISCONNECTED,
    val assistant: AssistantState = AssistantState.UNKNOWN,
    val micOpen: Boolean = false,
    val serviceRunning: Boolean = false,

    /** Raw PCM pushed to the server, in bytes. */
    val bytesSent: Long = 0,
    /** Raw PCM that arrived on the socket. */
    val bytesReceived: Long = 0,
    /**
     * Raw PCM that reached the speaker.
     *
     * Separate from [bytesReceived] on purpose, and it is the whole diagnostic:
     * received climbing while played stays flat means the socket is fine and
     * the AudioTrack is not, which is a completely different bug from nothing
     * arriving at all. Conflating the two hides exactly the case worth seeing.
     */
    val bytesPlayed: Long = 0,
    val framesSent: Long = 0,
    val framesReceived: Long = 0,
    val framesPlayed: Long = 0,

    /** Uplink frames dropped because the socket was behind. */
    val framesDropped: Long = 0,

    /** Sample rate the server announced on /ws/phone-out. */
    val downlinkRate: Int = 0,

    /** Microphone level 0..1, for the meter. */
    val micLevel: Float = 0f,

    /** Wake-word engine in use, and its last score. */
    val wakeWordName: String = "",
    val wakeWordScore: Float = 0f,
    /** True while audio is allowed to leave the phone. */
    val gateOpen: Boolean = false,

    val attempt: Int = 0,
    val nextRetrySeconds: Int = 0,
    val lastError: String? = null,

    /** Last few lines from MARK LIII, newest last. */
    val log: List<String> = emptyList(),
) {
    val connected: Boolean get() = link == LinkState.CONNECTED
}

object JarvisState {

    private val _state = MutableStateFlow(JarvisSnapshot())
    val state: StateFlow<JarvisSnapshot> = _state

    fun setLink(link: LinkState, error: String? = null) = _state.update {
        it.copy(link = link, lastError = error ?: it.lastError.takeIf { _ -> link == LinkState.ERROR })
    }

    fun setAssistant(state: AssistantState) = _state.update { it.copy(assistant = state) }

    fun setMicOpen(open: Boolean) = _state.update {
        it.copy(micOpen = open, micLevel = if (open) it.micLevel else 0f)
    }

    fun setServiceRunning(running: Boolean) = _state.update {
        if (running) it.copy(serviceRunning = true)
        // A stopped service owns nothing any more: leaving the byte counters up
        // while the link shows DISCONNECTED reads as "it is still doing
        // something", which is exactly the confusion this screen exists to
        // remove.
        else JarvisSnapshot(log = it.log, lastError = it.lastError)
    }

    fun setRetry(attempt: Int, seconds: Int) = _state.update {
        it.copy(attempt = attempt, nextRetrySeconds = seconds)
    }

    fun setDownlinkRate(rate: Int) = _state.update { it.copy(downlinkRate = rate) }

    fun setMicLevel(level: Float) = _state.update { it.copy(micLevel = level) }

    fun setWakeWord(name: String) = _state.update { it.copy(wakeWordName = name) }

    fun setWakeScore(score: Float) = _state.update { it.copy(wakeWordScore = score) }

    fun setGateOpen(open: Boolean) = _state.update { it.copy(gateOpen = open) }

    fun countSent(bytes: Int) = _state.update {
        it.copy(bytesSent = it.bytesSent + bytes, framesSent = it.framesSent + 1)
    }

    fun countReceived(bytes: Int) = _state.update {
        it.copy(bytesReceived = it.bytesReceived + bytes,
                framesReceived = it.framesReceived + 1)
    }

    fun countPlayed(bytes: Int) = _state.update {
        it.copy(bytesPlayed = it.bytesPlayed + bytes,
                framesPlayed = it.framesPlayed + 1)
    }

    fun countDropped() = _state.update { it.copy(framesDropped = it.framesDropped + 1) }

    fun setError(message: String?) = _state.update { it.copy(lastError = message) }

    fun log(line: String) = _state.update {
        it.copy(log = (it.log + line).takeLast(40))
    }
}
