plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "tw.mysh.monitor"
    compileSdk = 34
    defaultConfig {
        applicationId = "tw.mysh.monitor"
        minSdk = 26
        targetSdk = 34
        versionCode = 17
        versionName = "1.5.11"
    }
    signingConfigs {
        create("release") {
            storeFile = file("../keystore/mysh.jks")
            storePassword = "mysh2026"
            keyAlias = "mysh"
            keyPassword = "mysh2026"
        }
    }
    buildTypes {
        release { isMinifyEnabled = false; signingConfig = signingConfigs.getByName("release") }
        debug { signingConfig = signingConfigs.getByName("release") }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
}
