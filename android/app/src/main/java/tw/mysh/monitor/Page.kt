package tw.mysh.monitor

import android.annotation.SuppressLint
import android.content.Context
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** 網頁來源:優先用「更新程式」下載到 files/app.html 的版本,否則用 APK 內建的 assets/app.html */
object Page {
    const val UPDATE_URL = "https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/app/app.html"
    fun localFile(ctx: Context): File = File(ctx.filesDir, "app.html")
    /** 網頁裡的版本戳 <meta name="app-build" content="YYYYMMDDHHMM">;沒有就當 0(很舊) */
    fun buildOf(text: String): Long = Regex("name=\"app-build\" content=\"(\\d+)\"").find(text)?.groupValues?.get(1)?.toLongOrNull() ?: 0L
    private var assetBuildCache = -1L
    fun assetBuild(ctx: Context): Long {
        if (assetBuildCache < 0) assetBuildCache = try { ctx.assets.open("app.html").bufferedReader(Charsets.UTF_8).use { buildOf(it.readText()) } } catch (e: Exception) { 0L }
        return assetBuildCache
    }
    fun url(ctx: Context): String {
        val f = localFile(ctx)
        // 「更新程式」下載的版本若比這個 APK 內建的舊(例如 GitHub 還沒合併),就丟掉,用 APK 內建的
        if (f.exists()) {
            val fb = try { buildOf(f.readText(Charsets.UTF_8)) } catch (e: Exception) { 0L }
            if (fb < assetBuild(ctx)) f.delete()
        }
        return if (f.exists() && f.length() > 10000) "file://" + f.absolutePath else "file:///android_asset/app.html"
    }
    /** 目前實際在用的網頁版本戳(下載版或內建版,取較新的) */
    private var cbKey = ""; private var cbVal = 0L
    fun currentBuild(ctx: Context): Long {
        val f = localFile(ctx)
        val key = if (f.exists()) "${f.lastModified()}/${f.length()}" else "-"
        if (key != cbKey) {   // 2026-10-18 背景服務每 5 秒會問一次,檔案沒變就不用重讀整個檔
            cbVal = if (f.exists()) try { buildOf(f.readText(Charsets.UTF_8)) } catch (e: Exception) { 0L } else 0L
            cbKey = key
        }
        return maxOf(cbVal, assetBuild(ctx))
    }
    /** 自動更新(2026-10-18 使用者:「有辦法不用按就自動更新嗎」):靜靜去 GitHub 比對版本戳,
     *  比目前用的新才下載換掉;回傳 true = 有換新版(呼叫端要重新載入網頁)。失敗一律當沒有新版。 */
    fun autoUpdate(ctx: Context): Boolean {
        return try {
            val conn = URL(UPDATE_URL + "?_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
            conn.connectTimeout = 15000; conn.readTimeout = 30000
            if (conn.responseCode != 200) return false
            val txt = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
            if (txt.length < 10000 || !txt.contains("replaySignals")) return false
            val nb = buildOf(txt)
            if (nb <= currentBuild(ctx)) return false
            val tmp = File(ctx.filesDir, "app.html.tmp"); tmp.writeText(txt, Charsets.UTF_8)
            tmp.renameTo(localFile(ctx))
        } catch (e: Exception) { false }
    }
    /** 從 GitHub 下載最新 HTML;回傳 (成功, 訊息) */
    fun update(ctx: Context): Pair<Boolean, String> {
        return try {
            val conn = URL(UPDATE_URL + "?_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
            conn.connectTimeout = 15000; conn.readTimeout = 30000
            if (conn.responseCode != 200) return Pair(false, "HTTP " + conn.responseCode)
            val txt = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
            if (txt.length < 10000 || !txt.contains("replaySignals")) return Pair(false, "下載的內容不像程式檔(${txt.length} 字)")
            val nb = buildOf(txt); val ab = assetBuild(ctx)
            if (nb < ab) { localFile(ctx).delete(); return Pair(false, "GitHub 上的網頁(版本 $nb)比 App 內建的(版本 $ab)舊,不更新;等 GitHub 合併後再按") }
            val tmp = File(ctx.filesDir, "app.html.tmp"); tmp.writeText(txt, Charsets.UTF_8)
            tmp.renameTo(localFile(ctx))
            Pair(true, "已更新(${txt.length / 1024} KB)")
        } catch (e: Exception) { Pair(false, e.message ?: e.toString()) }
    }
    @SuppressLint("SetJavaScriptEnabled")
    fun setup(web: WebView, bridge: JsBridge) {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            allowFileAccess = true
            @Suppress("DEPRECATION") allowFileAccessFromFileURLs = true
            @Suppress("DEPRECATION") allowUniversalAccessFromFileURLs = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            cacheMode = WebSettings.LOAD_DEFAULT
            useWideViewPort = true
            loadWithOverviewMode = true
            setSupportZoom(false)
            textZoom = 100   // 2026-10-17 網頁字不跟手機「字型大小」設定走,版面由網頁自己依螢幕寬度算,不會破圖
            mediaPlaybackRequiresUserGesture = false
        }
        web.addJavascriptInterface(bridge, "Android")
        web.webViewClient = WebViewClient()
    }
}
