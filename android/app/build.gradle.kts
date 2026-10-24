// note: 通知列新排版(兩列三欄對齊、大字、底色網紋);點通知開那檔 K 圖
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
        versionCode = 52
        versionName = "1.5.46"
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
    buildFeatures { buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
}
