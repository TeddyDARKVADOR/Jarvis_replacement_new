package com.jarvis.avatar

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/**
 * The face's model on the phone: fetched once from the server, verified,
 * kept.
 *
 * WHERE IT COMES FROM
 *   `GET /api/avatar/manifest` (server/avatar_api.py) names the model the user
 *   installed on the server with `presence.install_model`, and gives the
 *   SHA-256 its profile recorded. `GET /api/avatar/model` gives the bytes.
 *   There is no second model mechanism: the fingerprint is the one
 *   `install_model` measured, and the manifest is the one the desktop reads.
 *
 * WHAT "VERIFIED" MEANS
 *   The bytes are hashed WHILE they arrive and compared with the manifest's
 *   SHA-256 (and the header's, which must agree). Only then are they moved in
 *   place, by rename. A local copy is re-hashed before use, once per process
 *   per (size, mtime): a file that changed on disk is never shown.
 *
 * WHAT HAPPENS WHEN IT CANNOT
 *   Every failure — network, 401, no model on the server, wrong fingerprint —
 *   is an [Outcome] and never an exception: the caller keeps the 2D core.
 *   A failed download backs off (30 s, 2 min, 10 min, 30 min) before trying
 *   again; a network failure with a verified local copy still shows the face.
 *
 * Pure JVM on purpose (java.io, OkHttp, org.json): testable without Android.
 */
class AvatarModelStore(
    private val root: File,
    private val http: OkHttpClient = OkHttpClient(),
    private val clock: () -> Long = System::currentTimeMillis,
) {
    sealed interface Outcome {
        /** A verified model and the manifest that names it. */
        data class Ready(val manifest: String, val modelFile: File, val sha256: String,
                         val downloaded: Boolean) : Outcome
        /** The server has no verified model to offer. Not an error. */
        data class Unavailable(val reason: String) : Outcome
        /** The bearer was refused: sessions live in the server's RAM, so a
         *  restart or a deploy expires them. Log in again and retry — no
         *  backoff, nothing is wrong with the model. */
        data class Rejected(val reason: String) : Outcome
        /** Something went wrong; do not try again before [retryAtMillis]. */
        data class Failed(val reason: String, val retryAtMillis: Long) : Outcome
    }

    private val manifestFile get() = File(root, "manifest.json")
    private val installedFile get() = File(root, "installed.json")
    private val tmpDir get() = File(root, "tmp")

    private var failures = 0
    private var retryAt = 0L
    private val verified = HashMap<String, Triple<Long, Long, String>>()   // path -> size, mtime, sha

    /** The locally installed model, re-verified. Null if none or if it changed. */
    @Synchronized
    fun installed(): Outcome.Ready? {
        val record = readJson(installedFile) ?: return null
        val rel = record.optString("file")
        val sha = record.optString("sha256")
        val file = modelFileFor(rel) ?: return null
        if (!file.isFile || sha.length != 64) return null
        if (hashOf(file) != sha) return null
        val manifest = manifestFile.takeIf { it.isFile }?.readText() ?: return null
        return Outcome.Ready(manifest, file, sha, downloaded = false)
    }

    /** Blocking. Call from a background thread. */
    @Synchronized
    fun sync(httpBase: String, bearer: String): Outcome {
        val now = clock()
        if (now < retryAt) {
            return installed() ?: Outcome.Failed("nouvel essai dans ${(retryAt - now) / 1000} s", retryAt)
        }
        val info = try {
            get("$httpBase/api/avatar/manifest", bearer).use { response ->
                if (response.code == 401) return installed() ?: Outcome.Rejected("jeton refuse (401)")
                if (!response.isSuccessful) return fail("manifeste : HTTP ${response.code}")
                JSONObject(response.body.string())
            }
        } catch (e: IOException) {
            // Offline is the normal case on a train: a verified copy is enough.
            return installed() ?: fail("reseau : ${e.message}")
        } catch (e: Exception) {
            return fail("manifeste illisible : ${e.message}")
        }

        val model = info.optJSONObject("model")
        val manifest = info.optJSONObject("manifest")
        if (model == null || manifest == null) {
            succeed()
            return Outcome.Unavailable(info.optString("reason", "aucun modele sur le serveur"))
        }
        val rel = model.optString("file")
        val sha = model.optString("sha256").lowercase()
        val target = modelFileFor(rel) ?: return fail("chemin de modele refuse : $rel")
        if (sha.length != 64) return fail("empreinte absente du manifeste")

        val local = installed()
        if (local != null && local.sha256 == sha && local.modelFile == target) {
            writeAtomically(manifestFile, manifest.toString())   // calibration may have moved
            succeed()
            return local.copy(manifest = manifest.toString())
        }

        return try {
            download("$httpBase/api/avatar/model", bearer, target, sha, model.optLong("size", -1))
            writeAtomically(manifestFile, manifest.toString())
            writeAtomically(installedFile, JSONObject().put("file", rel).put("sha256", sha).toString())
            succeed()
            Outcome.Ready(manifest.toString(), target, sha, downloaded = true)
        } catch (e: Unauthorized) {
            installed() ?: Outcome.Rejected("jeton refuse (401)")
        } catch (e: Exception) {
            installed() ?: fail(e.message ?: e.javaClass.simpleName)
        }
    }

    // ── download ─────────────────────────────────────────────────────────────

    private fun download(url: String, bearer: String, target: File, sha: String, size: Long) {
        tmpDir.mkdirs()
        val part = File(tmpDir, "model.part")
        part.delete()
        get(url, bearer).use { response ->
            if (response.code == 401) throw Unauthorized()
            if (!response.isSuccessful) throw IOException("modele : HTTP ${response.code}")
            val announced = response.header("X-Model-Sha256")?.lowercase()
            if (announced != null && announced != sha) {
                throw IOException("empreinte annoncee differente du manifeste")
            }
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            response.body.byteStream().use { input ->
                part.outputStream().use { output ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        digest.update(buf, 0, n)
                        output.write(buf, 0, n)
                        total += n
                    }
                }
            }
            val got = digest.digest().toHex()
            if (got != sha) {
                part.delete()
                throw IOException("empreinte SHA-256 incorrecte")
            }
            if (size >= 0 && total != size) {
                part.delete()
                throw IOException("taille incorrecte ($total au lieu de $size)")
            }
        }
        target.parentFile?.mkdirs()
        if (target.exists()) target.delete()
        if (!part.renameTo(target)) {
            part.delete()
            throw IOException("installation impossible")
        }
        verified.remove(target.path)
    }

    private class Unauthorized : IOException("401")

    private fun get(url: String, bearer: String) =
        http.newCall(Request.Builder().url(url).header("Authorization", "Bearer $bearer").build()).execute()

    // ── bookkeeping ──────────────────────────────────────────────────────────

    private fun fail(reason: String): Outcome.Failed {
        failures += 1
        val delay = BACKOFF_MS[(failures - 1).coerceAtMost(BACKOFF_MS.size - 1)]
        retryAt = clock() + delay
        return Outcome.Failed(reason, retryAt)
    }

    private fun succeed() {
        failures = 0
        retryAt = 0L
    }

    /** `models/<rel>` under [root], or null for anything that escapes it. */
    fun modelFileFor(rel: String): File? {
        if (rel.isBlank() || rel.startsWith("/") || rel.contains('\\') ||
            rel.split('/').any { it == ".." || it.isEmpty() }) return null
        val base = File(root, "models").canonicalFile
        val file = File(base, rel).canonicalFile
        return if (file.path.startsWith(base.path + File.separator)) file else null
    }

    private fun hashOf(file: File): String {
        val key = file.path
        val cached = verified[key]
        if (cached != null && cached.first == file.length() && cached.second == file.lastModified()) {
            return cached.third
        }
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(1 shl 16)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                digest.update(buf, 0, n)
            }
        }
        val sha = digest.digest().toHex()
        verified[key] = Triple(file.length(), file.lastModified(), sha)
        return sha
    }

    private fun readJson(file: File): JSONObject? =
        try { if (file.isFile) JSONObject(file.readText()) else null } catch (e: Exception) { null }

    private fun writeAtomically(file: File, text: String) {
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(text)
        if (file.exists()) file.delete()
        tmp.renameTo(file)
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    companion object {
        val BACKOFF_MS = longArrayOf(30_000, 120_000, 600_000, 1_800_000)
    }
}
