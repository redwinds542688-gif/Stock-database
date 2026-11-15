// note: 1.0.9 畫面顯示版本號;字型載入後再縮一次字
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "tw.mysh.partner"
    compileSdk = 34
    defaultConfig {
        applicationId = "tw.mysh.partner"   // 和名揚四海(tw.mysh.monitor)、名揚四海看盤(tw.mysh.monitor.viewer)不同 → 三個可以同時裝
        minSdk = 26
        targetSdk = 34
        versionCode = 10
        versionName = "1.0.9"
    }
    signingConfigs {
        create("release") {
            storeFile = file("../../../android/keystore/mysh.jks")
            storePassword = "mysh2026"
            keyAlias = "mysh"
            keyPassword = "mysh2026"
        }
    }
    buildTypes {
        release { isMinifyEnabled = false; signingConfig = signingConfigs.getByName("release") }
        debug { signingConfig = signingConfigs.getByName("release") }
    }
    buildFeatures { buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("androidx.core:core-ktx:1.13.1")   // FileProvider(更新時把下載的 APK 交給系統安裝程式)
}
