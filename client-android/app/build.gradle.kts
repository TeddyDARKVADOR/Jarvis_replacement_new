plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// avatar/ -> build/generated/avatarAssets/avatar. The page, its JavaScript and
// the vendored three.js only: never avatar/models (a downloaded .glb is not
// ours to redistribute; the phone fetches the user's own from the server),
// never the checks, the lab or the gesture clips.
abstract class SyncAvatarPage : DefaultTask() {
    /** The repository's avatar/ — read, never written. */
    @get:Internal abstract val source: DirectoryProperty

    /** What is actually copied: the up-to-date check looks only at these. */
    @get:InputFiles @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val page: ConfigurableFileCollection

    @get:OutputDirectory abstract val output: DirectoryProperty

    @get:Inject abstract val fs: FileSystemOperations

    @TaskAction
    fun run() {
        fs.sync {
            from(source) { include(*PAGE) }
            into(output.dir("avatar"))
        }
    }

    companion object {
        val PAGE = arrayOf("index.html", "js/**", "vendor/**")
    }
}

val avatarDir = rootProject.layout.projectDirectory.dir("../avatar")
val syncAvatarPage = tasks.register<SyncAvatarPage>("syncAvatarPage") {
    source.set(avatarDir)
    page.from(fileTree(avatarDir) { include(*SyncAvatarPage.PAGE) })
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(syncAvatarPage, SyncAvatarPage::output)
    }
}

android {
    namespace = "com.jarvis"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.jarvis"
        // 26 is the floor for the notification channels the foreground service
        // needs. Everything else here (AudioRecord, AudioTrack, OkHttp) is far
        // older; the microphone foreground-service *type* is API 34 and is
        // applied conditionally at runtime, see JarvisForegroundService.
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "0.1-prototype"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }

    androidResources {
        // TFLite models are memory-mapped straight out of the APK; a compressed
        // asset cannot be mapped and the interpreter fails to load it. The
        // self-test PCM is stored raw for the same reason — it is read as bytes.
        noCompress += listOf("tflite", "pcm")
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("androidx.activity:activity-compose:1.13.0")

    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")

    // OkHttp carries the three WebSockets. Chosen over a hand-rolled client for
    // one reason above all: pingInterval. A phone that loses Wi-Fi does not get
    // a TCP reset — the socket simply stops delivering, and without an
    // application-level ping the client sits there believing it is connected.
    implementation("com.squareup.okhttp3:okhttp:5.5.0")

    // Runs openWakeWord's three models on the phone. The same .tflite files
    // core/wake_word.py downloads on the desktop, so desktop and phone hear the
    // same word with the same weights.
    implementation("org.tensorflow:tensorflow-lite:2.17.0")

    // Serves the avatar page from the APK over https://appassets… : ES modules
    // do not load from file://, and this keeps the page's origin fixed.
    implementation("androidx.webkit:webkit:1.14.0")

    // AvatarModelStore is plain JVM (java.io, OkHttp, org.json) so its
    // download / SHA-256 / backoff rules are tested without a phone.
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver3:5.5.0")
    testImplementation("org.json:json:20240303")
}
