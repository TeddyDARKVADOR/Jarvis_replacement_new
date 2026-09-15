package com.jarvis.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import com.jarvis.net.Protocol
import kotlin.math.min
import kotlin.math.sqrt

/**
 * The microphone, in the format MARK LIII already speaks.
 *
 * 16 kHz, mono, 16-bit — not a choice made here. It is what `main.py` streams
 * from its own microphone (SEND_SAMPLE_RATE) and what `/ws/phone-audio` hands
 * to Gemini untouched, so the server sees one format whichever microphone is
 * talking and there is no resampling anywhere in the path.
 *
 * **`VOICE_RECOGNITION`, not `MIC` and not `VOICE_COMMUNICATION`** — taken from
 * the old Jarvis's VoiceCapture.kt, which documents the reasoning: `MIC` leaves
 * the OEM's processing in place, and `VOICE_COMMUNICATION` applies telephony
 * echo cancellation and noise reduction that deform the signal. Gemini does its
 * own turn detection on the waveform; handing it a waveform the phone has
 * already "improved" makes that worse, not better.
 *
 * **Raw PCM, not SpeechRecognizer.** The old app used Android's recogniser and
 * therefore got words, never signal — which is why its wake word had to be a
 * lexical filter over the transcript and why its restart logic runs to a
 * thousand lines. Reading AudioRecord directly removes that entire class of
 * problem: there is no recogniser session to die, no `error_client` loop, and
 * no engine to fight for ownership of the microphone.
 */
class AudioRecorder(
    private val onFrame: (frame: ByteArray, length: Int) -> Unit,
    private val onLevel: (Float) -> Unit = {},
) {

    @Volatile private var recorder: AudioRecord? = null
    @Volatile private var running = false
    private var worker: Thread? = null

    val isRecording: Boolean get() = running

    /**
     * Caller must hold RECORD_AUDIO. Returns false if the microphone could not
     * be opened — another app holds it, or the permission was revoked between
     * the check and here.
     */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true

        val minBuffer = AudioRecord.getMinBufferSize(
            Protocol.UPLINK_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) {
            Log.e(TAG, "getMinBufferSize returned $minBuffer")
            return false
        }

        val record = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                Protocol.UPLINK_SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                // ×4 so a scheduling hiccup on a loaded phone does not overrun
                // the hardware buffer and punch a hole in the sentence.
                minBuffer * 4,
            )
        } catch (e: Exception) {
            Log.e(TAG, "AudioRecord refused: ${e.message}")
            return false
        }

        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            Log.e(TAG, "AudioRecord did not initialise")
            return false
        }

        recorder = record
        running = true
        record.startRecording()

        worker = Thread({ loop(record) }, "jarvis-mic").apply {
            priority = Thread.MAX_PRIORITY   // audio thread: never starved by UI
            start()
        }
        return true
    }

    fun stop() {
        if (!running) return
        running = false
        try {
            worker?.join(1_000)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        worker = null
        recorder?.let {
            try {
                it.stop()
            } catch (_: IllegalStateException) {
                // Already stopped; releasing is still the right next step.
            }
            it.release()
        }
        recorder = null
        onLevel(0f)
    }

    private fun loop(record: AudioRecord) {
        val frame = ByteArray(Protocol.UPLINK_FRAME_BYTES)
        while (running) {
            val read = record.read(frame, 0, frame.size)
            if (read <= 0) {
                // ERROR_INVALID_OPERATION / ERROR_DEAD_OBJECT: the microphone
                // was taken away (a phone call, another app). Stop cleanly; the
                // service decides whether to try again.
                if (read < 0) {
                    Log.w(TAG, "read() returned $read — microphone lost")
                    running = false
                }
                continue
            }
            onLevel(levelOf(frame, read))
            onFrame(frame, read)
        }
    }

    /**
     * 0..1 loudness, using the same floor and ceiling as the desktop HUD
     * (`_pcm_level` in main.py) so the bar on the phone and the waveform on the
     * PC mean the same thing for the same voice.
     */
    private fun levelOf(buf: ByteArray, length: Int): Float {
        var sum = 0.0
        var count = 0
        var i = 0
        val end = min(length - 1, buf.size - 1)
        while (i < end) {
            val sample = ((buf[i + 1].toInt() shl 8) or (buf[i].toInt() and 0xFF)).toShort()
            sum += sample.toDouble() * sample.toDouble()
            count++
            i += 2
        }
        if (count == 0) return 0f
        val rms = sqrt(sum / count)
        if (rms <= LEVEL_FLOOR) return 0f
        return ((rms - LEVEL_FLOOR) / (LEVEL_FULL - LEVEL_FLOOR)).coerceIn(0.0, 1.0).toFloat()
    }

    private companion object {
        const val TAG = "AudioRecorder"
        const val LEVEL_FLOOR = 60.0
        const val LEVEL_FULL = 2600.0
    }
}
