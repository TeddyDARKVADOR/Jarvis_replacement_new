package com.jarvis.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * JARVIS's voice, arriving as 24 kHz PCM and going to whatever the phone is
 * playing through.
 *
 * **The rate is the server's, not a constant here.** `/ws/phone-out` announces
 * it in its first frame, and [start] builds the track from that. main.py could
 * change RECEIVE_SAMPLE_RATE tomorrow and this would follow; hardcoding 24000
 * would produce audio at the wrong pitch, which is the kind of bug that gets
 * diagnosed as "the model sounds strange".
 *
 * **Why a queue and a writer thread.** `AudioTrack.write` blocks when the
 * buffer is full — that is how it paces playback — and the bytes arrive on
 * OkHttp's reader thread. Writing directly would stall the socket, back up the
 * server's send queue and eventually cost frames. One handover decouples them.
 *
 * **Bluetooth and wired headsets need no code here.** USAGE_ASSISTANT with
 * CONTENT_TYPE_SPEECH lets Android route to whatever the user is wearing, and
 * it is what tells the system this is an assistant speaking rather than music —
 * so it ducks the podcast instead of fighting it. The alternative, a SCO
 * headset call, would drag the audio down to 8 kHz telephony and need
 * BLUETOOTH_CONNECT for a worse result.
 */
class AudioPlayer(
    private val onPlayed: (bytes: Int) -> Unit = {},
) {

    private val queue = LinkedBlockingQueue<ByteArray>(QUEUE_CAPACITY)

    @Volatile private var track: AudioTrack? = null
    @Volatile private var running = false
    @Volatile private var sampleRate = 0
    private var worker: Thread? = null

    val isPlaying: Boolean get() = running
    val currentSampleRate: Int get() = sampleRate

    /** Idempotent for the same rate; rebuilds the track if the rate changed. */
    fun start(rate: Int): Boolean {
        if (running && rate == sampleRate) return true
        if (running) stop()

        val minBuffer = AudioTrack.getMinBufferSize(
            rate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) {
            Log.e(TAG, "getMinBufferSize returned $minBuffer for ${rate}Hz")
            return false
        }

        val built = try {
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                // ×2 absorbs a late batch without a gap. Much more than that and
                // an interrupt would keep talking after the user has cut in,
                // because the buffer still holds the old sentence.
                .setBufferSizeInBytes(minBuffer * 2)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
        } catch (e: Exception) {
            Log.e(TAG, "AudioTrack refused: ${e.message}")
            return false
        }

        if (built.state != AudioTrack.STATE_INITIALIZED) {
            built.release()
            return false
        }

        sampleRate = rate
        track = built
        running = true
        built.play()

        worker = Thread({ loop(built) }, "jarvis-speaker").apply {
            priority = Thread.MAX_PRIORITY
            start()
        }
        return true
    }

    /** Non-blocking. Drops the oldest audio only if the queue is genuinely full. */
    fun write(pcm: ByteArray) {
        if (!running) return
        if (!queue.offer(pcm)) {
            // Reached only in a pathological case now — see QUEUE_CAPACITY. When
            // it does happen, the oldest goes: keeping it would preserve every
            // byte and grow the delay without bound.
            queue.poll()
            queue.offer(pcm)
        }
    }

    /**
     * Throw away everything not yet played. This is what an interrupt means —
     * both the queue and AudioTrack's own buffer, or the last half-second of
     * the abandoned sentence still comes out of the speaker.
     */
    fun flush() {
        queue.clear()
        track?.let {
            try {
                it.pause()
                it.flush()
                it.play()
            } catch (e: IllegalStateException) {
                Log.w(TAG, "flush on a track that is not playing: ${e.message}")
            }
        }
    }

    fun stop() {
        running = false
        queue.clear()
        try {
            worker?.join(800)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        worker = null
        track?.let {
            try {
                it.pause()
                it.flush()
                it.stop()
            } catch (_: IllegalStateException) {
            }
            it.release()
        }
        track = null
        sampleRate = 0
    }

    private fun loop(t: AudioTrack) {
        while (running) {
            val chunk = try {
                queue.poll(200, TimeUnit.MILLISECONDS)
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                null
            } ?: continue

            var offset = 0
            while (offset < chunk.size && running) {
                val written = t.write(chunk, offset, chunk.size - offset,
                                      AudioTrack.WRITE_BLOCKING)
                if (written <= 0) {
                    Log.w(TAG, "write() returned $written")
                    break
                }
                offset += written
            }
            onPlayed(chunk.size)
        }
    }

    private companion object {
        const val TAG = "AudioPlayer"

        /**
         * ~400 batches of ≤200 ms — about 80 seconds of speech.
         *
         * **This was 25 (five seconds), and five seconds was wrong.** Measured
         * against the running server: MARK LIII delivered 1.52 s of audio in
         * 0.41 s of wall time — a burst 3.7× faster than real time, because
         * Gemini generates faster than speech and `_play_audio` forwards
         * immediately without pacing. Nothing paces this stream until the
         * AudioTrack here does.
         *
         * So the queue has to absorb the whole gap between arrival and
         * playback: a twenty-second answer lands in about five seconds and
         * leaves fifteen seconds of audio waiting. At 25 slots the queue
         * overflowed a few seconds in and dropped the OLDEST frames — cutting
         * the middle out of JARVIS's sentence while the end played normally.
         * The kind of fault that gets blamed on the network.
         *
         * 400 × 9600 B is under 4 MB in the worst case, and `flush()` still
         * empties it instantly on an interrupt, so a large queue costs nothing
         * that matters.
         */
        const val QUEUE_CAPACITY = 400
    }
}
