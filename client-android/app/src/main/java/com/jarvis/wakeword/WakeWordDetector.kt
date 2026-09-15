package com.jarvis.wakeword

/**
 * "Is someone talking to JARVIS?" — behind one interface, so the engine can be
 * replaced without touching the microphone, the sockets or the service.
 *
 * **The contract, and the one rule that matters.** [feed] is called from the
 * audio thread, once per 64 ms frame. It must copy or queue and return. Running
 * inference there stalls the microphone, and a stalled microphone loses the
 * beginning of the sentence the user just started — the exact words that say
 * what they want. Both `core/wake_word.py` on the desktop and the old Jarvis
 * Android landed on the same shape for the same reason: cheap push on the audio
 * thread, model on its own thread.
 *
 * **Why the old Jarvis's wake word cannot be reused.** Its detector was a
 * lexical filter over Android's speech transcript — fifteen spellings of
 * "Jarvis" matched against recognised words — because `SpeechRecognizer` never
 * exposes the signal. This app reads raw PCM, so that approach has nothing to
 * match on. The trade is a good one: acoustic detection works with no
 * transcription running, which is what makes continuous local listening cheap
 * enough to leave on.
 *
 * **What ships today.** [AlwaysOpen], which is not a wake word at all: the
 * microphone streams for as long as the user leaves it on. That is the honest
 * prototype behaviour, and it makes the rest of the chain testable now.
 *
 * **Adding a real engine** is a new class in this package and one line in
 * [WakeWordEngines]. Neither of the two candidates can be completed here,
 * because both need a file this repository cannot generate:
 *
 *   • Picovoice Porcupine — `ai.picovoice:porcupine-android`, plus an AccessKey
 *     from the Picovoice console and a `.ppn` keyword file. "Jarvis" is one of
 *     its built-in keywords. Best accuracy, free tier, account required.
 *   • openWakeWord via TFLite — the same `hey_jarvis` ONNX/TFLite model
 *     `core/wake_word.py` already downloads on the desktop, run through
 *     `org.tensorflow:tensorflow-lite`. No account, no per-device key; more
 *     work, and the melspectrogram and embedding models have to be shipped in
 *     assets/ alongside it.
 */
interface WakeWordDetector {

    /** Shown in the UI so it is always clear which engine is running. */
    val name: String

    /** False when the engine's model or key is missing. */
    val isReady: Boolean

    /**
     * True if the microphone should stay local until [start]'s callback fires.
     * False means "there is no wake word": audio goes to the server for as long
     * as the microphone is on.
     */
    val gatesAudio: Boolean

    /** @return false if the engine could not be started. */
    fun start(onDetected: () -> Unit): Boolean

    /** Audio thread. Cheap and non-blocking — see the interface note. */
    fun feed(frame: ByteArray, length: Int)

    fun stop()
}

/**
 * No wake word: the gate is open from the moment the microphone is.
 *
 * Not a placeholder to be embarrassed about — it is the correct behaviour for
 * push-to-talk, and it is what makes the audio path testable end to end before
 * any model is involved.
 */
class AlwaysOpen : WakeWordDetector {

    override val name = "always open (no wake word)"
    override val isReady = true
    override val gatesAudio = false

    private var opened = false

    override fun start(onDetected: () -> Unit): Boolean {
        opened = true
        onDetected()
        return true
    }

    override fun feed(frame: ByteArray, length: Int) = Unit

    override fun stop() {
        opened = false
    }
}

object WakeWordEngines {

    /**
     * The engine the service uses.
     *
     * [enabled] is a user setting, and it defaults to **off**. Not timidity:
     * with the wake word on, nothing reaches the server until "Hey Jarvis" is
     * heard, which is precisely what makes the continuous-audio tests — screen
     * off, network loss, long runs — impossible to interpret. Those come first;
     * the wake word is switched on once they pass.
     *
     * Falls back to [AlwaysOpen] if the models cannot be loaded, so a missing
     * asset degrades to push-to-talk instead of a deaf assistant.
     */
    fun default(
        context: android.content.Context,
        enabled: Boolean,
        onScore: (Float) -> Unit = {},
    ): WakeWordDetector {
        if (!enabled) return AlwaysOpen()
        val engine = OpenWakeWordDetector(context, onScore = onScore)
        return if (engine.ensureModels()) engine else AlwaysOpen()
    }
}
