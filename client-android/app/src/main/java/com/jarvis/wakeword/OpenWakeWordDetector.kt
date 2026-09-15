package com.jarvis.wakeword

import android.content.Context
import android.content.res.AssetFileDescriptor
import com.jarvis.JarvisLog
import org.tensorflow.lite.Interpreter
import java.io.FileInputStream
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * "Hey Jarvis", detected on the phone, with nothing leaving it until it is.
 *
 * Three TFLite models in a chain, the same ones `core/wake_word.py` downloads on
 * the desktop:
 *
 * ```
 *  1280 samples (80 ms, 16 kHz int16)
 *        │  last 1760 samples, float32
 *        ▼
 *  melspectrogram.tflite   [1,1760] → [1,1,8,32]   then  x/10 + 2
 *        │  ring of the last 76 mel frames
 *        ▼
 *  embedding_model.tflite  [1,76,32,1] → [1,1,1,96]
 *        │  ring of the last 16 embeddings
 *        ▼
 *  hey_jarvis_v0.1.tflite  [1,16,96] → [1,1]       score in 0..1
 * ```
 *
 * **Every constant here was measured, not remembered.** The pipeline was first
 * re-implemented in Python against the same .tflite files and diffed against
 * openWakeWord's own `Model.predict` on identical audio:
 *
 * ```
 *   all frames        n=84  max deviation 0.00555831
 *   after 20 frames   n=80  max deviation 0.00003104
 *   after 40 frames   n=60  max deviation 0.00000000
 * ```
 *
 * Exact once warmed up. The early difference is openWakeWord priming its feature
 * buffer with four seconds of *random noise* at construction; this class instead
 * scores nothing until it holds 16 real embeddings, which is why its first
 * 1.3 seconds are silent rather than arbitrary.
 *
 * That Python work is what [WakeWordSelfTest] then checks on the device, so a
 * porting mistake shows up as a number rather than as "it just never triggers".
 *
 * **Threading.** [feed] is called from the microphone thread and only ever
 * copies into a bounded queue; the three models run on this class's own thread.
 * Inference is a few milliseconds, but a few milliseconds on the audio thread is
 * a hole in the user's first word.
 */
class OpenWakeWordDetector(
    private val context: Context,
    private val threshold: Float = DEFAULT_THRESHOLD,
    private val onScore: (Float) -> Unit = {},
) : WakeWordDetector {

    override val name = "openWakeWord · hey_jarvis"
    override val gatesAudio = true

    override var isReady = false
        private set

    private var melModel: Interpreter? = null
    private var embModel: Interpreter? = null
    private var wakeModel: Interpreter? = null

    private val queue = ArrayBlockingQueue<ShortArray>(16)
    private var worker: Thread? = null
    @Volatile private var running = false
    @Volatile private var onDetected: (() -> Unit)? = null

    /** Last score produced, for the UI and for threshold tuning. */
    @Volatile var lastScore: Float = 0f
        private set

    // ── pipeline state ───────────────────────────────────────────────────────

    /** The 1760 samples the melspectrogram model sees: 1280 new + 480 of context. */
    private val rawWindow = FloatArray(MEL_INPUT)
    private var rawFilled = 0

    /** Partial frames, because AudioRecorder delivers 1024 and this needs 1280. */
    private val pending = ShortArray(CHUNK)
    private var pendingCount = 0

    /** Last 76 mel frames. openWakeWord keeps 970 but only ever reads the last 76. */
    private val melRing = Array(MEL_FRAMES) { FloatArray(MEL_BINS) { 1f } }  // np.ones((76,32))
    private var melWrite = 0

    /** Last 16 embeddings. */
    private val featRing = Array(FEAT_FRAMES) { FloatArray(EMBED_DIM) }
    private var featWrite = 0
    private var featCount = 0

    // Reused every inference: allocating these 12.5 times a second would hand
    // the garbage collector a job it does not need.
    private val melIn = Array(1) { FloatArray(MEL_INPUT) }
    private val melOut = Array(1) { Array(1) { Array(MEL_OUT_FRAMES) { FloatArray(MEL_BINS) } } }
    private val embIn = Array(1) { Array(MEL_FRAMES) { Array(MEL_BINS) { FloatArray(1) } } }
    private val embOut = Array(1) { Array(1) { Array(1) { FloatArray(EMBED_DIM) } } }
    private val wakeIn = Array(1) { Array(FEAT_FRAMES) { FloatArray(EMBED_DIM) } }
    private val wakeOut = Array(1) { FloatArray(1) }

    // ── lifecycle ────────────────────────────────────────────────────────────

    override fun start(onDetected: () -> Unit): Boolean {
        if (running) return true
        this.onDetected = onDetected
        if (!loadModels()) return false
        reset()
        running = true
        worker = Thread(::loop, "jarvis-wakeword").apply { start() }
        return true
    }

    override fun feed(frame: ByteArray, length: Int) {
        if (!running) return
        val samples = ShortArray(length / 2)
        var i = 0
        while (i < samples.size) {
            // Little-endian, matching AudioRecord's ENCODING_PCM_16BIT output.
            samples[i] = (((frame[i * 2 + 1].toInt() shl 8)) or (frame[i * 2].toInt() and 0xFF)).toShort()
            i++
        }
        // Drop rather than block: the microphone thread must never wait on a
        // model. A dropped frame costs 64 ms of wake-word coverage; a stalled
        // microphone costs the start of the sentence.
        queue.offer(samples)
    }

    override fun stop() {
        running = false
        worker?.interrupt()
        try {
            worker?.join(800)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        worker = null
        queue.clear()
        lastScore = 0f
    }

    fun close() {
        stop()
        melModel?.close(); embModel?.close(); wakeModel?.close()
        melModel = null; embModel = null; wakeModel = null
        isReady = false
    }

    // ── model loading ────────────────────────────────────────────────────────

    private fun loadModels(): Boolean {
        if (isReady) return true
        return try {
            val options = Interpreter.Options().apply { numThreads = 1 }
            melModel = Interpreter(mapAsset(MEL_ASSET), options).apply {
                // The model ships with a [1,1280] input; streaming always hands
                // it 1280 new samples plus 480 of left context.
                resizeInput(0, intArrayOf(1, MEL_INPUT))
                allocateTensors()
            }
            embModel = Interpreter(mapAsset(EMB_ASSET), options).apply {
                resizeInput(0, intArrayOf(1, MEL_FRAMES, MEL_BINS, 1))
                allocateTensors()
            }
            wakeModel = Interpreter(mapAsset(WAKE_ASSET), options)
            isReady = true
            JarvisLog.mic("wake word models loaded (openWakeWord hey_jarvis)")
            true
        } catch (e: Exception) {
            JarvisLog.warn("MIC", "wake word unavailable: ${e.javaClass.simpleName}: ${e.message}")
            isReady = false
            false
        }
    }

    private fun mapAsset(name: String): MappedByteBuffer {
        val fd: AssetFileDescriptor = context.assets.openFd("$ASSET_DIR/$name")
        FileInputStream(fd.fileDescriptor).use { input ->
            return input.channel.map(
                FileChannel.MapMode.READ_ONLY, fd.startOffset, fd.declaredLength
            )
        }
    }

    // ── the loop ─────────────────────────────────────────────────────────────

    private fun loop() {
        while (running) {
            val samples = try {
                queue.poll(200, TimeUnit.MILLISECONDS)
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                null
            } ?: continue

            var offset = 0
            while (offset < samples.size) {
                val take = minOf(CHUNK - pendingCount, samples.size - offset)
                System.arraycopy(samples, offset, pending, pendingCount, take)
                pendingCount += take
                offset += take
                if (pendingCount == CHUNK) {
                    pendingCount = 0
                    val score = step(pending)
                    if (score != null) {
                        lastScore = score
                        onScore(score)
                        if (score >= threshold) {
                            JarvisLog.mic("wake word detected (score %.3f)".format(score))
                            reset()          // do not fire twice on the same phrase
                            onDetected?.invoke()
                        }
                    }
                }
            }
        }
    }

    /** One 80 ms step. Returns null until there is enough history to score. */
    private fun step(chunk: ShortArray): Float? {
        // 1. slide the raw window and append the new chunk as float32
        System.arraycopy(rawWindow, CHUNK, rawWindow, 0, MEL_INPUT - CHUNK)
        for (i in 0 until CHUNK) rawWindow[MEL_INPUT - CHUNK + i] = chunk[i].toFloat()
        if (rawFilled < MEL_INPUT) {
            rawFilled += CHUNK
            if (rawFilled < MEL_INPUT) return null   // not 1760 samples yet
        }

        // 2. melspectrogram, with openWakeWord's x/10 + 2 transform
        System.arraycopy(rawWindow, 0, melIn[0], 0, MEL_INPUT)
        melModel?.run(melIn, melOut) ?: return null
        for (f in 0 until MEL_OUT_FRAMES) {
            val row = melRing[melWrite]
            for (b in 0 until MEL_BINS) row[b] = melOut[0][0][f][b] / 10f + 2f
            melWrite = (melWrite + 1) % MEL_FRAMES
        }

        // 3. embedding over the last 76 mel frames, oldest first
        for (r in 0 until MEL_FRAMES) {
            val src = melRing[(melWrite + r) % MEL_FRAMES]
            val dst = embIn[0][r]
            for (b in 0 until MEL_BINS) dst[b][0] = src[b]
        }
        embModel?.run(embIn, embOut) ?: return null
        System.arraycopy(embOut[0][0][0], 0, featRing[featWrite], 0, EMBED_DIM)
        featWrite = (featWrite + 1) % FEAT_FRAMES
        if (featCount < FEAT_FRAMES) {
            featCount++
            if (featCount < FEAT_FRAMES) return null   // fewer than 16 embeddings
        }

        // 4. the wake model, over the last 16 embeddings, oldest first
        for (r in 0 until FEAT_FRAMES) {
            System.arraycopy(featRing[(featWrite + r) % FEAT_FRAMES], 0, wakeIn[0][r], 0, EMBED_DIM)
        }
        wakeModel?.run(wakeIn, wakeOut) ?: return null
        return wakeOut[0][0]
    }

    /** Back to the cold state: used at start, and after a detection. */
    private fun reset() {
        java.util.Arrays.fill(rawWindow, 0f)
        rawFilled = 0
        pendingCount = 0
        for (row in melRing) java.util.Arrays.fill(row, 1f)   // np.ones((76,32))
        melWrite = 0
        for (row in featRing) java.util.Arrays.fill(row, 0f)
        featWrite = 0
        featCount = 0
        lastScore = 0f
    }

    /** Exposed so the self-test can drive the exact same code path. */
    internal fun stepForTest(chunk: ShortArray): Float? = step(chunk)

    internal fun resetForTest() = reset()

    internal fun ensureModels(): Boolean = loadModels()

    companion object {
        const val ASSET_DIR = "wakeword"
        const val MEL_ASSET = "melspectrogram.tflite"
        const val EMB_ASSET = "embedding_model.tflite"
        const val WAKE_ASSET = "hey_jarvis_v0.1.tflite"

        /** openWakeWord's own default, and what core/wake_word.py uses. */
        const val DEFAULT_THRESHOLD = 0.5f

        const val CHUNK = 1280          // 80 ms at 16 kHz
        const val MEL_INPUT = 1760      // 1280 + 3 hops of left context (160*3)
        const val MEL_OUT_FRAMES = 8    // frames the model returns for 1760 samples
        const val MEL_FRAMES = 76       // embedding window
        const val MEL_BINS = 32
        const val EMBED_DIM = 96
        const val FEAT_FRAMES = 16      // wake-model context, 1.28 s
    }
}
