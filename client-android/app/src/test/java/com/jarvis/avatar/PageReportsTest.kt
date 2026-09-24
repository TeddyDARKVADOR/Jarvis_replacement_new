package com.jarvis.avatar

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The page's reports reach the host once each.
 *
 * `onFailed` used to call itself: inside the bridge method, `onFailed(json)`
 * resolved to the method rather than the callback, so a page that failed
 * re-posted its report forever and the host never heard of it.
 */
class PageReportsTest {
    private val queue = ArrayDeque<Runnable>()
    private val logged = mutableListOf<String>()

    /** Runs what was posted, as the main looper would, and never more than [max]. */
    private fun drain(max: Int = 100): Int {
        var n = 0
        while (queue.isNotEmpty() && n < max) { queue.removeFirst().run(); n++ }
        return n
    }

    @Test fun aFailureReachesTheHostOnceAndStops() {
        val failures = mutableListOf<String>()
        val reports = PageReports({}, { failures += it }, post = { queue.addLast(it) },
                                  log = { _, m -> logged += m })
        reports.onFailed("""{"reason":"timeout"}""")
        assertEquals(1, drain())
        assertEquals(listOf("""{"reason":"timeout"}"""), failures)
        assertEquals(0, queue.size)
        assertEquals(1, logged.size)
    }

    @Test fun readinessReachesTheHostOnce() {
        var ready = 0
        val reports = PageReports({ ready++ }, {}, post = { queue.addLast(it) }, log = { _, _ -> })
        reports.onReady("{}")
        assertEquals(1, drain())
        assertEquals(1, ready)
    }
}
