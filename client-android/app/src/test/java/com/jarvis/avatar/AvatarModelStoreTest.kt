package com.jarvis.avatar

import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okio.Buffer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.security.MessageDigest

/**
 * The model's journey to the phone, against a fake server: every rule the
 * decision asked for — download, SHA-256, absent model, resumption after a
 * failure, offline with a verified copy — without an emulator.
 */
class AvatarModelStoreTest {
    @get:Rule val tmp = TemporaryFolder()
    private lateinit var server: MockWebServer
    private var now = 1_000_000L
    private val model = ByteArray(5000) { (it % 251).toByte() }
    private val sha = MessageDigest.getInstance("SHA-256").digest(model).joinToString("") { "%02x".format(it) }

    @Before fun up() { server = MockWebServer(); server.start() }
    @After fun down() { server.close() }

    private fun store() = AvatarModelStore(tmp.root, clock = { now })
    private val base get() = server.url("").toString().trimEnd('/')

    private fun manifest(file: String? = "jarvis/female/jarvis.glb", fingerprint: String = sha): MockResponse {
        val body = JSONObject().put("manifest", JSONObject().put("model", JSONObject().put("file", file ?: "")))
        if (file != null) body.put("model", JSONObject().put("file", file).put("sha256", fingerprint).put("size", model.size))
        else body.put("model", JSONObject.NULL).put("reason", "aucun modele actif")
        return MockResponse.Builder().code(200).body(body.toString()).build()
    }

    private fun bytes(data: ByteArray = model, header: String? = sha) =
        MockResponse.Builder().code(200).body(Buffer().write(data))
            .apply { if (header != null) addHeader("X-Model-Sha256", header) }.build()

    @Test fun downloadsVerifiesAndKeeps() {
        server.enqueue(manifest()); server.enqueue(bytes())
        val s = store()
        val first = s.sync(base, "b")
        assertTrue(first is AvatarModelStore.Outcome.Ready)
        first as AvatarModelStore.Outcome.Ready
        assertTrue(first.downloaded)
        assertEquals(sha, first.sha256)
        assertTrue(first.modelFile.readBytes().contentEquals(model))
        assertEquals("Bearer b", server.takeRequest().headers["Authorization"])

        // Same fingerprint on the server: nothing is downloaded again.
        server.enqueue(manifest())
        val again = s.sync(base, "b") as AvatarModelStore.Outcome.Ready
        assertFalse(again.downloaded)
        assertEquals(3, server.requestCount)          // manifest, model, manifest
    }

    @Test fun wrongFingerprintIsRefusedAndNothingIsInstalled() {
        server.enqueue(manifest()); server.enqueue(bytes(model.copyOf().also { it[0] = 99 }, header = null))
        val s = store()
        val out = s.sync(base, "b")
        assertTrue(out is AvatarModelStore.Outcome.Failed)
        assertTrue((out as AvatarModelStore.Outcome.Failed).reason.contains("SHA-256"))
        assertNull(s.installed())
        assertFalse(tmp.root.resolve("tmp/model.part").exists())
    }

    @Test fun headerThatDisagreesWithTheManifestIsRefused() {
        server.enqueue(manifest()); server.enqueue(bytes(header = "0".repeat(64)))
        assertTrue(store().sync(base, "b") is AvatarModelStore.Outcome.Failed)
    }

    @Test fun backsOffThenResumes() {
        server.enqueue(manifest()); server.enqueue(bytes(model.copyOf().also { it[1] = 7 }))
        val s = store()
        val failed = s.sync(base, "b") as AvatarModelStore.Outcome.Failed
        assertEquals(now + AvatarModelStore.BACKOFF_MS[0], failed.retryAtMillis)
        val before = server.requestCount
        assertTrue(s.sync(base, "b") is AvatarModelStore.Outcome.Failed)
        assertEquals("no request during the backoff", before, server.requestCount)

        now += AvatarModelStore.BACKOFF_MS[0] + 1
        server.enqueue(manifest()); server.enqueue(bytes())
        assertTrue(s.sync(base, "b") is AvatarModelStore.Outcome.Ready)
    }

    @Test fun noModelOnTheServerIsUnavailableNotAnError() {
        server.enqueue(manifest(file = null))
        val out = store().sync(base, "b")
        assertTrue(out is AvatarModelStore.Outcome.Unavailable)
    }

    @Test fun offlineWithAVerifiedCopyStillShowsTheFace() {
        server.enqueue(manifest()); server.enqueue(bytes())
        val s = store()
        assertTrue(s.sync(base, "b") is AvatarModelStore.Outcome.Ready)
        server.close()
        val out = s.sync(base, "b")
        assertTrue("got $out", out is AvatarModelStore.Outcome.Ready)
    }

    @Test fun offlineWithoutACopyFails() {
        server.close()
        assertTrue(store().sync(base, "b") is AvatarModelStore.Outcome.Failed)
    }

    @Test fun unauthorisedIsRejectedWithoutBackoff() {
        // A restarted server forgot the session: the caller logs in again and
        // the very next sync must go through, not wait 30 s.
        val s = store()
        server.enqueue(MockResponse.Builder().code(401).body("{}").build())
        assertTrue(s.sync(base, "old") is AvatarModelStore.Outcome.Rejected)
        server.enqueue(manifest()); server.enqueue(bytes())
        assertTrue(s.sync(base, "new") is AvatarModelStore.Outcome.Ready)
    }

    @Test fun unauthorisedOnTheModelIsRejectedToo() {
        server.enqueue(manifest()); server.enqueue(MockResponse.Builder().code(401).body("{}").build())
        assertTrue(store().sync(base, "b") is AvatarModelStore.Outcome.Rejected)
    }

    @Test fun aPathThatEscapesIsRefused() {
        server.enqueue(manifest(file = "../../evil.glb"))
        assertTrue(store().sync(base, "b") is AvatarModelStore.Outcome.Failed)
        val s = store()
        assertNull(s.modelFileFor("../x.glb"))
        assertNull(s.modelFileFor("/etc/passwd"))
        assertNull(s.modelFileFor("a//b.glb"))
        assertNotNull(s.modelFileFor("jarvis/female/jarvis.glb"))
    }

    @Test fun aLocalCopyThatChangedIsNeverShownAndIsFetchedAgain() {
        server.enqueue(manifest()); server.enqueue(bytes())
        val s = store()
        val ready = s.sync(base, "b") as AvatarModelStore.Outcome.Ready
        ready.modelFile.writeBytes(model.copyOf().also { it[2] = 1 })
        ready.modelFile.setLastModified(ready.modelFile.lastModified() + 5000)
        assertNull("a modified file must not verify", s.installed())
        server.enqueue(manifest()); server.enqueue(bytes())
        val again = s.sync(base, "b") as AvatarModelStore.Outcome.Ready
        assertTrue(again.downloaded)
    }
}
