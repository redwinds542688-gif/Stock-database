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
            mediaPlaybackRequiresUserGesture = false
        }
        web.addJavascriptInterface(bridge, "Android")
        web.webViewClient = WebViewClient()
    }
}
