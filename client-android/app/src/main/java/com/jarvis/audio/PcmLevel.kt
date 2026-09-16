package com.jarvis.audio

import kotlin.math.min
import kotlin.math.sqrt

/**
 * Loudness of a PCM16 buffer, 0..1.
 *
 * One implementation for both directions on purpose. The microphone meter and
 * JARVIS's own voice drive the same animation, and if they used different floors
 * the core would breathe differently depending on who was talking — which reads
 * as a bug in the animation rather than as a difference in the audio.
 *
 * The floor and ceiling are the desktop HUD's (`_pcm_level` in main.py), so the
 * same voice moves the phone and the PC by the same amount.
 */
object PcmLevel {

    const val FLOOR = 60.0
    const val FULL = 2600.0

    fun of(buf: ByteArray, length: Int = buf.size): Float {
        var sum = 0.0
        var count = 0
        var i = 0
        val end = min(length - 1, buf.size - 1)
        while (i < end) {
            val sample = ((buf[i + 1].toInt() shl 8) or (buf[i].toInt() and 0xFF)).toShort()
            sum += sample.toDouble() * sample.toDouble()
            count++
            // Every fourth sample is plenty for a meter and a quarter of the
            // work: at 24 kHz a 200 ms batch is 4800 samples, and this runs on
            // the playback thread between two blocking writes.
            i += 8
        }
        if (count == 0) return 0f
        val rms = sqrt(sum / count)
        if (rms <= FLOOR) return 0f
        return ((rms - FLOOR) / (FULL - FLOOR)).coerceIn(0.0, 1.0).toFloat()
    }
}
