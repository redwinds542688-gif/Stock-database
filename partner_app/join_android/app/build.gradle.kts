// 自動當沖 · 夥伴加入 App:只是殼,畫面每次打開都讀雲端最新的 partner_app/join/index.html(改畫面不用改版);殼本身改了才升版本
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "tw.mysh.join"
    compileSdk = 34
    defaultConfig {
        applicationId = "tw.mysh.join"   // 和名揚四海、名揚四海看盤、夥伴看盤都不同 → 可以同時裝
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
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
    implementation("androidx.core:core-ktx:1.13.1")
}
