# 名揚四海 Android 背景監控 App

- `app/src/main/assets/app.html`:內建的網頁程式(和 `../app/app.html` 同一份)。
- 「更新程式」功能會從 `https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/app/app.html` 下載最新版到手機,
  所以改 HTML 之後只要更新 `app/app.html`(repo 根目錄的 `app/`),不用重新安裝 APK。
- 簽章金鑰 `keystore/mysh.jks`(密碼在 `app/build.gradle.kts`),之後重新編譯要用同一把才能覆蓋安裝。
- 編譯:`ANDROID_HOME=<sdk> gradle assembleRelease` → `app/build/outputs/apk/release/app-release.apk`。
