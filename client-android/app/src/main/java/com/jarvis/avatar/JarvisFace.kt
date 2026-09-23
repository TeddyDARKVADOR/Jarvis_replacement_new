package com.jarvis.avatar

import android.annotation.SuppressLint
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.webkit.WebViewAssetLoader
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.io.File

/**
 * The same `avatar/` page the desktop hosts, in a WebView, driven by facts.
 *
 * WHAT THIS FILE IS, AND IS NOT
 *   Hosting only. The page, the engine, `bridge.js` and the Director are the
 *   desktop's, copied into the APK at build time from `avatar/` (never kept
 *   as a second copy in the repository). Every rule about what the face does
 *   lives in `avatar/js/host.js`, tested under Node; this file forwards facts
 *   and never decides anything.
 *
 * THE 2D CORE IS THE DEFAULT, THE FACE IS AN UPGRADE
 *   The core is drawn until the page says `onReady` — model loaded, verified,
 *   not procedural. `onFailed`, a render process that dies, or no verified
 *   model: the core stays, and the rest of the app does not notice.
 *
 * RENDERING ONLY WHEN SEEN
 *   ON_PAUSE (background, screen off) stops the page's animation loop
 *   (`window.JARVIS.setAnimated(false)`) and pauses the WebView; ON_RESUME
 *   starts both again. The voice service never touches any of this.
 */
const val PAGE_URL = "https://appassets.androidplatform.net/assets/avatar/index.html?host=phone"

/** Serves the page from the APK, and the manifest + model from local storage,
 *  at the very paths `main.js` already asks for. */
class AvatarPathHandler(context: Context, private val root: File, private val store: AvatarModelStore) :
    WebViewAssetLoader.PathHandler {
    private val assets = WebViewAssetLoader.AssetsPathHandler(context)

    override fun handle(path: String): WebResourceResponse? {
        if (path == "avatar/manifest.json") return file(File(root, "manifest.json"), "application/json")
        if (path.startsWith("avatar/models/")) {
            val target = store.modelFileFor(path.removePrefix("avatar/models/"))
            return target?.let { file(it, "model/gltf-binary") }
        }
        return assets.handle(path)
    }

    private fun file(f: File, mime: String): WebResourceResponse? =
        if (f.isFile) WebResourceResponse(mime, null, f.inputStream()) else null
}

/** What the page tells the app. Called on a WebView thread; hops to main. */
class PageReports(private val onReady: () -> Unit, private val onFailed: (String) -> Unit) {
    private val main = Handler(Looper.getMainLooper())

    @JavascriptInterface
    fun onReady(json: String) {
        Log.i("JarvisFace", "page prete : $json")
        main.post(onReady)
    }

    @JavascriptInterface
    fun onFailed(json: String) {
        Log.w("JarvisFace", "page en echec : $json")
        main.post { onFailed(json) }
    }

    /** Every `avatar` event and what host.js did with it (played, stale, older…). */
    @JavascriptInterface
    fun onIntent(json: String) {
        Log.i("JarvisFace", "directive : $json")
    }

    /** host.js's counters, every 5 s while the face is seen. */
    @JavascriptInterface
    fun onStats(json: String) {
        Log.i("JarvisFace", "stats : $json")
    }
}

fun WebView.post(message: JSONObject) {
    evaluateJavascript("window.postMessage(${message}, '*')", null)
}

/**
 * The home screen's centrepiece: the 3D face when a verified model loaded,
 * [core] otherwise. [facts] are the four the state word needs; [avatarEvent]
 * is the last raw `avatar` event off /ws (host.js judges its freshness).
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun JarvisFace(
    facts: JSONObject,
    wokeAt: Long,
    speakerLevel: Float,
    micLevel: Float,
    avatarEvent: String?,
    avatarSeq: Long,
    onTap: (() -> Unit)?,
    core: @Composable () -> Unit,
) {
    val model by AvatarModels.state.collectAsState()
    var ready by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf<String?>(null) }
    var view by remember { mutableStateOf<WebView?>(null) }
    val usable = model is AvatarModelStore.Outcome.Ready && failed == null

    // One slot, two layers: the face is laid over the core and only covers it
    // once the page said it is ready.
    Box(contentAlignment = Alignment.Center) {
        if (!ready || !usable) core()
        if (usable) FaceLayer(
            facts, wokeAt, speakerLevel, micLevel, avatarEvent, avatarSeq, onTap,
            ready = ready,
            view = view,
            onView = { view = it },
            onReady = { ready = true },
            onFailed = { failed = it; ready = false },
        )
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun FaceLayer(
    facts: JSONObject,
    wokeAt: Long,
    speakerLevel: Float,
    micLevel: Float,
    avatarEvent: String?,
    avatarSeq: Long,
    onTap: (() -> Unit)?,
    ready: Boolean,
    view: WebView?,
    onView: (WebView?) -> Unit,
    onReady: () -> Unit,
    onFailed: (String) -> Unit,
) {

    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle, view) {
        val web = view
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> {
                    web?.onResume()
                    web?.post(JSONObject().put("type", "host-visible").put("visible", true))
                }
                Lifecycle.Event.ON_PAUSE -> {
                    web?.post(JSONObject().put("type", "host-visible").put("visible", false))
                    web?.onPause()
                }
                else -> Unit
            }
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }

    LaunchedEffect(ready, facts.toString(), wokeAt) {
        if (!ready) return@LaunchedEffect
        fun send() {
            val ago = if (wokeAt > 0) (SystemClock.elapsedRealtime() - wokeAt) / 1000.0 else -1.0
            view?.post(JSONObject().put("type", "host-state")
                .put("facts", JSONObject(facts.toString()).put("wokeAgoS", if (ago >= 0) ago else null)))
        }
        send()
        // WAKING lasts 1.5 s (host.js WAKE_MAX_AGE_S): say it when it ends.
        if (wokeAt > 0) { delay(1_600); send() }
    }
    LaunchedEffect(ready, avatarSeq) {
        if (ready && avatarEvent != null) {
            view?.post(JSONObject().put("type", "host-intent").put("event", JSONObject(avatarEvent)))
        }
    }
    LaunchedEffect(ready, speakerLevel, micLevel) {
        if (ready) view?.post(JSONObject().put("type", "host-level")
            .put("speaker", speakerLevel.toDouble()).put("mic", micLevel.toDouble()))
    }

    Box(
        Modifier
            // The core's own 240 dp slot, a little larger: a face needs its
            // shoulders' worth of margin to not look cropped.
            .size(280.dp)
            .alpha(if (ready) 1f else 0f)
            .let { if (onTap != null) it.clickable { onTap() } else it },
    ) {
        AndroidView(
            modifier = Modifier.size(280.dp),
            factory = { ctx ->
                val root = AvatarModels.root(ctx)
                val loader = WebViewAssetLoader.Builder()
                    .addPathHandler("/assets/", AvatarPathHandler(ctx, root, AvatarModels.store(ctx)))
                    .build()
                WebView(ctx).apply {
                    setBackgroundColor(0)                        // the page is transparent
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = false
                    settings.allowFileAccess = false
                    settings.allowContentAccess = false
                    settings.mediaPlaybackRequiresUserGesture = true
                    overScrollMode = View.OVER_SCROLL_NEVER
                    addJavascriptInterface(PageReports(onReady = onReady, onFailed = onFailed), "JarvisAndroid")
                    webViewClient = object : WebViewClient() {
                        override fun shouldInterceptRequest(v: WebView, request: WebResourceRequest) =
                            loader.shouldInterceptRequest(request.url)

                        override fun onRenderProcessGone(v: WebView, detail: RenderProcessGoneDetail): Boolean {
                            // The GPU process died: never take the app with it.
                            Log.w("JarvisFace", "processus de rendu perdu (crash=${detail.didCrash()})")
                            onFailed("render process gone")
                            onView(null)
                            v.destroy()
                            return true
                        }
                    }
                    loadUrl(PAGE_URL)
                    onView(this)
                }
            },
            onRelease = { it.destroy(); onView(null) },
        )
    }
}
