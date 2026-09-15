// Two plugins, and the reason for each is a build failure this project actually
// hit:
//
//   • `org.jetbrains.kotlin.android` is NOT here. AGP 9 applies the Kotlin
//     Gradle plugin itself and refuses the standalone one outright
//     (https://kotl.in/gradle/agp-built-in-kotlin).
//   • `org.jetbrains.kotlin.plugin.compose` IS here. Built-in Kotlin does not
//     imply the Compose compiler: with `buildFeatures.compose = true` and no
//     plugin, configuration fails with "the Compose Compiler Gradle plugin is
//     required".
//
// AGP 9.4.0 rather than the 9.0.1 the Flutter project next door uses: the
// current androidx and OkHttp releases refuse to build below it and want
// compileSdk 37. AGP 9.4 in turn requires Gradle 9.6 — see
// gradle/wrapper/gradle-wrapper.properties.
plugins {
    id("com.android.application") version "9.4.0" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20" apply false
}
