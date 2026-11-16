import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Where llama.cpp's source is (not kept in this repository): llama.dir in local.properties.
val localProperties = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
val llamaDir: String? = localProperties.getProperty("llama.dir")

android {
    namespace = "app.aurora.companion"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.aurora.companion"
        // 26: Android 8, the first with self-managed calls (ConnectionService) for the Calling Agent.
        minSdk = 26
        targetSdk = 34
        versionCode = 11
        versionName = "0.10.0"
        ndk { abiFilters += "arm64-v8a" }                  // the phone's processor; keeps the app small
        if (llamaDir != null) {
            externalNativeBuild {
                cmake {
                    // Always optimised: an unoptimised model runner is several times slower, even in test builds.
                    arguments += listOf("-DLLAMA_DIR=$llamaDir", "-DANDROID_STL=c++_shared", "-DCMAKE_BUILD_TYPE=Release")
                    cppFlags += "-O3"
                }
            }
        }
    }

    // AURORA's mind on the phone (llama.cpp, offline plan C), built only when llama.dir is set.
    ndkVersion = "27.3.13750724"
    if (llamaDir != null) {
        externalNativeBuild {
            cmake {
                path = file("src/main/cpp/CMakeLists.txt")
                version = "3.22.1"
            }
        }
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
    // Offline calls (plan C): Vosk speech recognition on the phone (Apache 2.0) and JNA, which it uses.
    implementation("com.alphacephei:vosk-android:0.3.75@aar")
    implementation("net.java.dev.jna:jna:5.18.1@aar")
    testImplementation("junit:junit:4.13.2")
}
