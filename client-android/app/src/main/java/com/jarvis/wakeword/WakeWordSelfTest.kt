package com.jarvis.wakeword

import android.content.Context
import com.jarvis.JarvisLog
import kotlin.math.abs

/**
 * Checks the Kotlin pipeline against openWakeWord itself — on the device.
 *
 * **Why this exists.** The port could not be run anywhere but a phone, and a
 * mistake in it does not crash: wrong tensor order, a missed `/10 + 2`, a ring
 * buffer read newest-first, and the detector simply never fires. That failure is
 * indistinguishable from "the wake word does not work very well", and it would
 * be debugged for hours.
 *
 * So the reference implementation was run against the same .tflite files in
 * Python, on a fixed pseudo-random audio clip, and its scores were written to
 * `assets/wakeword/selftest_scores.txt`. This replays the same clip through the
 * same code path the microphone uses and compares.
 *
 * Only frames 40 and later are compared: before that, openWakeWord's own output
 * is shaped by the four seconds of random noise it primes its feature buffer
 * with at construction, and matching that would mean copying a wart. From frame
 * 40 the two agreed to 0.00000000 on the desktop.
 *
 * A pass means the plumbing is right. It says nothing about how well the model
 * hears *you* — that is the threshold, and that needs a voice.
 */
object WakeWordSelfTest {

    data class Result(
        val ran: Boolean,
        val passed: Boolean,
        val compared: Int,
        val maxDeviation: Float,
        val detail: String,
    )

    /** Absolute slack. Scores on noise sit around 1e-4, so this is tight enough
     *  to catch a wrong pipeline and loose enough to survive ARM-vs-x86 float
     *  differences and a different XNNPACK build. */
    private const val TOLERANCE = 2e-5f

    fun run(context: Context): Result {
        val detector = OpenWakeWordDetector(context)
        try {
            if (!detector.ensureModels()) {
                return Result(false, false, 0, 0f, "models could not be loaded")
            }

            val audio = context.assets.open("${OpenWakeWordDetector.ASSET_DIR}/selftest_audio.pcm")
                .use { it.readBytes() }
            val expected = context.assets
                .open("${OpenWakeWordDetector.ASSET_DIR}/selftest_scores.txt")
                .use { it.reader().readLines() }
                .filter { it.isNotBlank() }
                .associate { line ->
                    val (idx, score) = line.trim().split(" ")
                    idx.toInt() to score.toFloat()
                }

            detector.resetForTest()
            val chunks = audio.size / 2 / OpenWakeWordDetector.CHUNK
            var compared = 0
            var worst = 0f
            var worstAt = -1

            for (c in 0 until chunks) {
                val chunk = ShortArray(OpenWakeWordDetector.CHUNK)
                val base = c * OpenWakeWordDetector.CHUNK * 2
                for (i in chunk.indices) {
                    chunk[i] = (((audio[base + i * 2 + 1].toInt() shl 8)) or
                        (audio[base + i * 2].toInt() and 0xFF)).toShort()
                }
                val got = detector.stepForTest(chunk) ?: continue
                val want = expected[c] ?: continue
                val deviation = abs(got - want)
                if (deviation > worst) {
                    worst = deviation
                    worstAt = c
                }
                compared++
            }

            if (compared == 0) {
                return Result(true, false, 0, 0f, "no frame could be compared")
            }
            val passed = worst <= TOLERANCE
            val detail = "compared $compared frames, worst deviation " +
                "%.9f at frame %d (tolerance %.9f)".format(worst, worstAt, TOLERANCE)
            return Result(true, passed, compared, worst, detail)
        } catch (e: Exception) {
            return Result(false, false, 0, 0f, "${e.javaClass.simpleName}: ${e.message}")
        } finally {
            detector.close()
        }
    }

    /** Runs the check and reports it to logcat and the on-screen log. */
    fun runAndReport(context: Context) {
        val r = run(context)
        when {
            !r.ran -> JarvisLog.warn("MIC", "wake word self-test did not run — ${r.detail}")
            r.passed -> JarvisLog.mic("wake word self-test PASS — ${r.detail}")
            else -> JarvisLog.warn(
                "MIC",
                "wake word self-test FAIL — ${r.detail}. The pipeline does not match " +
                    "the reference; detection cannot be trusted.",
            )
        }
    }
}
