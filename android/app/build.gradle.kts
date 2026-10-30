// note: 免費 7 天改用 Android ID 認手機(重裝 App 不會重新計算)
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
        versionCode = 55
        versionName = "1.5.49"
    }
    // 2026-10-29 看盤版(使用者:「如果 程式給別人用 但我要控制判123在手上」→「先做1.2.3」第 1 項):同一份程式兩個口味,
    //   owner = 自己用的(app.html 含判一二三)、viewer = 給別人的看盤版(內建 / 更新都抓 viewer.html,沒有判一二三;套件名多 .viewer,兩個可以同時裝)
    flavorDimensions += "edition"
    productFlavors {
        create("owner") {   // 口味不能叫 main(和 main 原始碼目錄撞名,gradle 報 Multiple entries with same key: main)
            dimension = "edition"
            resValue("string", "app_name", "名揚四海自動當沖")
            buildConfigField("String", "UPDATE_URL", "\"https://twse-proxy.redwinds542688.workers.dev/?gh=raw&path=app/app.html\"")
            buildConfigField("String", "APK_DIR", "\"app/apk/\"")
            buildConfigField("String", "APK_NAME", "\"MyshMonitor.apk\"")
        }
        create("viewer") {
            dimension = "edition"
            applicationIdSuffix = ".viewer"
            versionNameSuffix = "-看盤"
            resValue("string", "app_name", "名揚四海 看盤")
            buildConfigField("String", "UPDATE_URL", "\"https://twse-proxy.redwinds542688.workers.dev/?gh=raw&path=app/viewer.html\"")
            buildConfigField("String", "APK_DIR", "\"app/apk/viewer/\"")
            buildConfigField("String", "APK_NAME", "\"MyshViewer.apk\"")
        }
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
