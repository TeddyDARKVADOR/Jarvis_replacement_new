package com.jarvis

import android.os.SystemClock
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

/**
 * One side of one turn.
 *
 * [atMillis] is 0 when the server did not date it. The wire format is an
 * ISO-8601 *string* (`datetime.now().isoformat()` in main.py), not a number —
 * parsed once on arrival so the list does not re-parse a date on every scroll.
 */
data class Message(
    val fromJarvis: Boolean,
    val text: String,
    val atMillis: Long = 0L,
    /**
     * Set only for `content` events — the structured panel the desktop shows
     * under the HUD (a search result, a list, a file summary). It used to be
     * flattened into one grey log line truncated at 200 characters, which threw
     * away the one kind of message the server had already taken the trouble to
     * give a shape.
     */
    val title: String? = null,
)

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

    /**
     * Notification counters, for the debug view.
     *
     * Four numbers rather than three, because [notificationsDuplicate] is the
     * one that tells you the system is working: the server re-offers recent
     * notifications on every reconnect, so duplicates climbing while shown
     * stays flat is dedup doing its job. Received climbing with nothing else
     * moving is the bug — events arriving and never reaching the shade.
     */
    val notificationsReceived: Long = 0,
    val notificationsShown: Long = 0,
    val notificationsDuplicate: Long = 0,
    val notificationsDropped: Long = 0,

    /** Sample rate the server announced on /ws/phone-out. */
    val downlinkRate: Int = 0,

    /** Microphone level 0..1, for the meter. */
    val micLevel: Float = 0f,

    /**
     * Loudness of JARVIS's own voice, 0..1, as it is played.
     *
     * Taken from the playback loop and not from the socket: audio arrives in
     * bursts far faster than real time, so a level measured on arrival would run
     * ahead of the voice and then flatline while JARVIS was still speaking.
     */
    val speakerLevel: Float = 0f,

    /** Wake-word engine in use, and its last score. */
    val wakeWordName: String = "",
    val wakeWordScore: Float = 0f,
    /** True while audio is allowed to leave the phone. */
    val gateOpen: Boolean = false,

    val attempt: Int = 0,
    val nextRetrySeconds: Int = 0,
    val lastError: String? = null,

    /**
     * Last few lines from MARK LIII, newest last.
     *
     * Everything, flattened to strings: system lines, notes, errors. This is the
     * debug view's feed and it is not what the home screen shows — see
     * [messages].
     */
    val log: List<String> = emptyList(),

    /**
     * The conversation, as turns rather than log lines.
     *
     * Separate from [log] because they answer different questions. `log` is
     * "what happened", including the plumbing; this is "what was said", and it
     * is the only one a person reads. The server replays its last 50 events on
     * connect, so this fills itself in on reconnect with nothing to request.
     */
    val messages: List<Message> = emptyList(),

    /**
     * The irreversible action MARK LIII is waiting on, null when there is none.
     *
     * Deliberately three fields on the existing snapshot rather than a second
     * state machine: a confirmation is something JARVIS is *doing*, concurrent
     * with LISTENING or SPEAKING, not a mode the app enters.
     *
     * [confirmationId] is the part that matters. It is the server's id for this
     * request and it goes back untouched; the app never invents one, never
     * reuses one, and never decides anything with it. If it is stale by the time
     * the user taps, the server discards the answer — which is the point.
     */
    val confirmationId: String? = null,
    val confirmationTitle: String = "",
    val confirmationDetail: String = "",

    /**
     * `SystemClock.elapsedRealtime()` when the pending confirmation dies, 0 if
     * none. The server sends `timeout_s` with the request and it was being
     * ignored — so the buttons stayed lit long after an answer would be
     * discarded. A gate that looks live and is not is worse than no gate.
     *
     * elapsedRealtime, not wall time: a clock change or an NTP correction must
     * not make a 90-second countdown jump.
     */
    val confirmationDeadline: Long = 0L,

    /**
     * When the wake word last opened the gate — `elapsedRealtime()`, 0 if never.
     * Purely for the acknowledgement flash: the moment "Hey Jarvis" is heard is
     * the one the whole product turns on, and it had no representation at all.
     */
    val wokeAt: Long = 0L,
) {
    val connected: Boolean get() = link == LinkState.CONNECTED
    val awaitingConfirmation: Boolean get() = confirmationId != null

    /** The line the home screen puts under the core, or null while JARVIS has
     *  said nothing yet. Only JARVIS's own words: showing the user their own
     *  sentence back is the one thing they already know. */
    val lastSpokenLine: String?
        get() = messages.lastOrNull { it.fromJarvis }?.text
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

    fun setSpeakerLevel(level: Float) = _state.update { it.copy(speakerLevel = level) }

    /** Append a turn. Capped: the home screen shows one line and the history
     *  screen a scroll, and neither is a reason to hold a whole day in RAM. */
    fun addMessage(message: Message) = _state.update {
        it.copy(messages = (it.messages + message).takeLast(MAX_MESSAGES))
    }

    fun setWakeWord(name: String) = _state.update { it.copy(wakeWordName = name) }

    fun setWakeScore(score: Float) = _state.update { it.copy(wakeWordScore = score) }

    fun setGateOpen(open: Boolean) = _state.update {
        // Stamp only on the rising edge. The gate re-opens on every interaction
        // while the window is alive, and flashing the core each time would turn
        // an acknowledgement into a flicker.
        if (open && !it.gateOpen) {
            it.copy(gateOpen = true, wokeAt = SystemClock.elapsedRealtime())
        } else {
            it.copy(gateOpen = open)
        }
    }

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

    fun countNotificationReceived() = _state.update {
        it.copy(notificationsReceived = it.notificationsReceived + 1)
    }

    fun countNotificationShown() = _state.update {
        it.copy(notificationsShown = it.notificationsShown + 1)
    }

    fun countNotificationDuplicate() = _state.update {
        it.copy(notificationsDuplicate = it.notificationsDuplicate + 1)
    }

    fun countNotificationDropped() = _state.update {
        it.copy(notificationsDropped = it.notificationsDropped + 1)
    }

    fun setError(message: String?) = _state.update { it.copy(lastError = message) }

    fun log(line: String) = _state.update {
        it.copy(log = (it.log + line).takeLast(40))
    }

    /** A `confirm` event arrived. Replaces any banner already up: the server
     *  keeps exactly one pending request, so showing two would be a lie. */
    fun setConfirmation(
        id: String,
        title: String,
        detail: String,
        timeoutSeconds: Int,
    ) = _state.update {
        it.copy(
            confirmationId = id,
            confirmationTitle = title,
            confirmationDetail = detail,
            // A server that sends no timeout gets no countdown rather than a
            // guessed one: showing an invented deadline would be worse than
            // showing none.
            confirmationDeadline = if (timeoutSeconds > 0) {
                SystemClock.elapsedRealtime() + timeoutSeconds * 1000L
            } else 0L,
        )
    }

    /** The request is over — answered here, answered elsewhere, or expired.
     *  Clearing is all this does; nothing is executed or cancelled locally. */
    fun clearConfirmation() = _state.update {
        it.copy(confirmationId = null, confirmationTitle = "",
                confirmationDetail = "", confirmationDeadline = 0L)
    }

    private const val MAX_MESSAGES = 60
}
