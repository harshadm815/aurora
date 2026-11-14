plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.aurora.companion"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.aurora.companion"
        // 26: Android 8, the first with self-managed calls (ConnectionService) for the Calling Agent.
        minSdk = 26
        targetSdk = 34
        versionCode = 4
        versionName = "0.4.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // No libraries yet: only Android itself and the Kotlin standard library.
    testImplementation("junit:junit:4.13.2")
}
