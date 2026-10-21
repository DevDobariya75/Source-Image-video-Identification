pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "ChainStamp"

// Pure-Kotlin chain library (no Android code): JPEG / MP4 reading and writing.
include(":core")

// The Android app needs the Android SDK. Build only the library with:
//   gradlew -PcoreOnly :core:test
if (!providers.gradleProperty("coreOnly").isPresent) {
    include(":app")
}
