# JARVIS Android client

A thin client for MARK LIII. The phone is a microphone, a speaker and a screen;
the assistant — Gemini, memory, actions, plugins — stays on the server.

It contains no Gemini key, no tool logic and no memory, and it never will: the
whole point of this split is that changing phone loses nothing.

```
AudioRecord 16 kHz ──► WakeWordDetector ──► /ws/phone-audio ──► MARK LIII ──► Gemini
AudioTrack  24 kHz ◄────────────────────── /ws/phone-out   ◄──────────────────────┘
```

## Build

Needs a **JDK** 17 or newer — a JRE is not enough, and a machine with only
`jre1.8` installed has neither — plus one SDK platform. Everything else is
fetched by the wrapper.

```bash
cd client-android
ANDROID_HOME=$HOME/Android/Sdk ./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

**The platform package is `platforms;android-37.0`, not `platforms;android-37`.**
Asking for the latter fails with `Failed to find package`, which reads like the
platform does not exist yet. It does; the naming gained a minor component (as
`android-36.1` did), and `compileSdk = 37` resolves to `android-37.0`.

```bash
sdkmanager "platforms;android-37.0"
```

If `sdkmanager` warns that it *"only understands SDK XML versions up to 3"*,
its package list is incomplete — install a newer `cmdline-tools` before
concluding that something is missing from the repository.

Nothing has to be installed system-wide. A portable JDK unzipped anywhere works,
pointed at for the build alone:

```bash
JAVA_HOME=/path/to/jdk-21 ANDROID_HOME=$HOME/Android/Sdk ./gradlew :app:assembleDebug
```

Measured on a cold cache (Windows 11, JDK 21.0.12 LTS, 17 September 2026):

| | |
|---|---|
| `assembleDebug` | 2 min 7 s, 36 tasks — `app-debug.apk`, 31.5 MB |
| `assembleRelease` | 38 s — `app-release-unsigned.apk`, 28.0 MB |

The release output is **unsigned**: there is no `signingConfig` in
`app/build.gradle.kts`, so it cannot be installed as it stands. Use the debug
APK for bring-up.

`stripDebugDebugSymbols` reports that it cannot strip `libtensorflowlite_jni.so`
and `libandroidx.graphics.path.so`. That is expected and not an error — they are
packaged as they are, and they are most of the 31.5 MB.

Toolchain, and why each version is what it is:

| | | |
|---|---|---|
| Gradle | 9.6.0 | AGP 9.4 refuses anything older |
| AGP | 9.4.0 | current androidx and OkHttp refuse anything older |
| compileSdk / targetSdk | 37 | required by `okhttp-android` 5.5 |
| minSdk | 26 | notification channels; nothing here needs more |

Use the **debug** build for the first bring-up: it allows cleartext to any host,
so the app can talk to a laptop on the LAN before Tailscale exists. The release
build restricts cleartext to `*.ts.net` and loopback — see `PROTOCOL.md` §6.

## First run

1. On the server: `python -m server.run_headless --pairing`, copy the device
   token.
2. In the app: fill in **Host** (Tailscale name or IP), **Port** (8000) and the
   **Device token**, then SAVE.
3. GRANT the microphone and notification permissions.
4. CONNECT — the banner should reach `CONNECTED`.
5. START MIC — "Sent" climbs. Say something; "Received" climbs when JARVIS
   answers and you hear it.

The screen is intentionally plain. It exists to answer four questions while the
audio path is being brought up: am I connected, is the microphone on, are bytes
moving both ways, and what went wrong.

## Layout

```
com/jarvis/
├── MainActivity.kt          Compose host; requests permissions, starts the service
├── JarvisState.kt           process-wide observable state — only the service writes
├── net/
│   ├── Protocol.kt          the wire contract, read off the server
│   ├── JarvisClient.kt      three WebSockets, transport only, no policy
│   └── ReconnectManager.kt  when to try again — the only place that decides
├── audio/
│   ├── AudioRecorder.kt     AudioRecord 16 kHz VOICE_RECOGNITION
│   └── AudioPlayer.kt       AudioTrack at the rate the server announces
├── auth/AuthManager.kt      device token → bearer
├── wakeword/                one interface, so the engine can be swapped
├── service/                 the foreground service that survives screen-off
└── ui/HomeScreen.kt         the screen
```

## Wake word

`WakeWordDetector` is an interface with two implementations:

| Engine | When |
|---|---|
| `AlwaysOpen` | default. Not a wake word — the microphone streams for as long as it is on. Correct push-to-talk behaviour, and the only sane setting while testing the audio path. |
| `OpenWakeWordDetector` | openWakeWord `hey_jarvis`, three TFLite models running on the phone. Audio stays local until the word is heard. |

Switch engines from the settings toggle (the service must be stopped to save).

**The openWakeWord port is verified numerically, on the device.** The pipeline
was first re-implemented in Python against the same `.tflite` files and diffed
against openWakeWord's own `Model.predict` on identical audio — exact agreement
(0.00000000) once warmed up. Those reference scores ship in
`assets/wakeword/selftest_scores.txt`, and `WakeWordSelfTest` replays them
through the real Kotlin code path at every service start:

```
adb logcat -s JARVIS
[MIC] wake word self-test PASS — compared 20 frames, worst deviation 0,000000007
```

A FAIL means the port no longer matches the reference and detection cannot be
trusted — which is the failure that otherwise disguises itself as "it detects
badly". What the self-test does *not* tell you is how well the model hears
**you**; that is the threshold, and it needs a voice.

The models come from the same openWakeWord release `core/wake_word.py` downloads
on the desktop, so phone and desktop listen with identical weights.

## Production

Debug and release differ in exactly one thing that matters, and it is not
cosmetic:

| | Cleartext HTTP allowed to |
|---|---|
| `assembleDebug` | **any host** — for bringing it up against a laptop on the LAN |
| `assembleRelease` | `*.ts.net` and loopback only |

The audio sockets carry unencrypted PCM, so the tunnel is what protects them.
Point the release build at a Tailscale MagicDNS name, never a bare IP.

* Server setup: [`../deployment/ORACLE.md`](../deployment/ORACLE.md)
* Private network: [`../deployment/TAILSCALE.md`](../deployment/TAILSCALE.md)
* Known gaps and bugs: [`../server/README.md`](../server/README.md)

## What is deliberately not here

No boot receiver, no battery-optimisation exemption in the manifest, no
background service start. The user switches JARVIS on from a visible screen,
and the ongoing notification says so for as long as it is listening.

`../server/` holds the other half. `PROTOCOL.md` is the contract between them.
