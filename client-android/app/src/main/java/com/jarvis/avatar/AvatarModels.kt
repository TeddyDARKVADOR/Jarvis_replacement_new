package com.jarvis.avatar

import android.content.Context
import android.util.Log
import com.jarvis.auth.AuthManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The one place the UI asks "is there a verified face to show?".
 *
 * `refresh()` is called when the app comes to the foreground. It never blocks
 * the caller, never runs twice at once, and respects the store's own backoff,
 * so calling it on every resume costs nothing when nothing changed.
 */
object AvatarModels {
    private const val TAG = "AvatarModels"
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "avatar-models") }
    private val busy = AtomicBoolean(false)
    @Volatile private var store: AvatarModelStore? = null

    private val _state = MutableStateFlow<AvatarModelStore.Outcome?>(null)
    val state: StateFlow<AvatarModelStore.Outcome?> = _state

    fun root(context: Context): File = File(context.filesDir, "avatar")

    fun store(context: Context): AvatarModelStore =
        store ?: synchronized(this) {
            store ?: AvatarModelStore(root(context)).also { store = it }
        }

    fun refresh(context: Context, auth: AuthManager) {
        if (!busy.compareAndSet(false, true)) return
        val app = context.applicationContext
        worker.execute {
            try {
                val s = store(app)
                // A verified local copy is shown at once; the sync may improve it.
                s.installed()?.let { _state.value = it }
                if (!auth.isConfigured) return@execute
                val bearer = auth.session?.bearer ?: auth.login().bearer
                val outcome = s.sync(auth.endpoint.httpBase, bearer)
                Log.i(TAG, "sync : $outcome")
                _state.value = outcome
            } catch (e: Exception) {
                Log.w(TAG, "sync impossible : ${e.message}")
                if (_state.value !is AvatarModelStore.Outcome.Ready) {
                    _state.value = AvatarModelStore.Outcome.Failed(e.message ?: "erreur", 0L)
                }
            } finally {
                busy.set(false)
            }
        }
    }
}
