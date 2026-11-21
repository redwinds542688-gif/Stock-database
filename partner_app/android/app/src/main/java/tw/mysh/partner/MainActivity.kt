package tw.mysh.partner

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.app.AlertDialog
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * 自動當沖 · 夥伴看盤 App(2026-11-15 使用者「看盤版 改成App 變成apk可以下載安裝 然後一進入就必須輸入金鑰 否則無法進入」)
 * 只是一個全螢幕的網頁殼:內建 assets/mine.html(= partner_app/mine.html),畫面、金鑰入口、讀資料、聲音震動都在網頁裡。
 * 網頁用 https://appassets.androidplatform.net/ 開(安全來源 → 相機掃碼可以用);金鑰存在 App 自己的網頁儲存區。
 * 只看不下單:這個 App 沒有任何下單、改設定的功能。
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private var pendingPerm: PermissionRequest? = null
    private var fileCb: ValueCallback<Array<Uri>>? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 2026-11-21 使用者「掃描 會員 崩潰」(夥伴看盤按掃描 App 直接關掉):記下當機原因,下次打開秀出來(截圖給 Claude 查)
        val crashF = java.io.File(filesDir, "crash.txt")
        val prev = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e -> try { crashF.writeText(android.util.Log.getStackTraceString(e).take(3000)) } catch (x: Exception) {}; prev?.uncaughtException(t, e) }
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web = WebView(this)
        web.setBackgroundColor(0xFF0B0E14.toInt())
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true                    // 金鑰、靜音設定存這裡
            mediaPlaybackRequiresUserGesture = false    // 通知聲音
            textZoom = 100                              // 2026-11-15 使用者截圖金額被擠到下一行:不跟手機系統字體大小放大(畫面的字本來就很大),長數字由網頁自己縮字
            allowFileAccess = false
            allowContentAccess = true
        }
        web.webViewClient = object : WebViewClient() {
            // 網頁元件(畫面)當掉:不讓整個 App 跟著關掉,記下原因、重開畫面
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                try { crashF.writeText("畫面元件當掉(didCrash=${detail.didCrash()})") } catch (x: Exception) {}; renderDead = true
                try { (view.parent as? android.view.ViewGroup)?.removeView(view); view.destroy() } catch (x: Exception) {}
                recreate(); return true
            }
            // 2026-11-16 21:35 使用者「每一次都要重新安裝嗎」→「改」:畫面改從雲端讀最新的(Worker /partner/ = main 的 partner_app/mine.html),改畫面不用重裝;
            //   網址照舊是 appassets(金鑰存的地方不變、相機照樣能用),只是內容換成雲端那份;雲端讀不到(沒網路、逾時)才用 App 裡內建的那份
            override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse? {
                if (req.isForMainFrame && req.url.path == "/assets/mine.html") cloudPage()?.let { return it }
                return loader.shouldInterceptRequest(req.url)
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            // 掃描金鑰 QR Code:網頁要相機 → 先問 Android 相機權限,同意了才給網頁
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    try {
                        if (request.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                            if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                            else { pendingPerm = request; requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAM) }
                        } else request.deny()
                    } catch (e: Exception) { try { crashF.writeText("相機權限:" + android.util.Log.getStackTraceString(e).take(2000)) } catch (x: Exception) {}; try { request.deny() } catch (x: Exception) {} }
                }
            }
            override fun onPermissionRequestCanceled(request: PermissionRequest) { if (pendingPerm === request) pendingPerm = null }
            // 「從相簿選 QR Code 圖片」
            override fun onShowFileChooser(view: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null); fileCb = cb
                return try { startActivityForResult(Intent(Intent.ACTION_GET_CONTENT).setType("image/*").addCategory(Intent.CATEGORY_OPENABLE), REQ_FILE); true }
                catch (e: Exception) { fileCb = null; false }
            }
        }
        web.addJavascriptInterface(Host(), "PartnerHost")   // 網頁問:這支手機有沒有裝名揚四海
        setContentView(web)
        // 2026-11-21 漏洞檢查 5:還原不了(例如畫面元件當掉後重開)就重新載入,不會空白
        if (savedInstanceState == null || renderDead || web.restoreState(savedInstanceState) == null) web.loadUrl("https://appassets.androidplatform.net/assets/mine.html")
        if (crashF.exists()) { val t = try { crashF.readText() } catch (e: Exception) { "" }; crashF.delete()
            if (t.isNotBlank()) { sendBug("crash", t); AlertDialog.Builder(this).setTitle("上次夥伴看盤意外關閉").setMessage("已自動回報給 Claude 查原因。").setPositiveButton("好", null).show() } }
        Updater.check(this)   // 打開 App 時檢查一次版本,有新版才問
        UpdateAlarm.schedule(this)   // 每個平日 08:30(開盤前 30 分鐘)再檢查一次
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTI)   // 08:30 有新版時要能跳通知
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        if (code == REQ_CAM) {
            val p = pendingPerm; pendingPerm = null
            if (p != null) try { if (res.isNotEmpty() && res[0] == PackageManager.PERMISSION_GRANTED) p.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) else p.deny() } catch (e: Exception) {}
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        if (code == REQ_FILE) { val u = data?.data; fileCb?.onReceiveValue(if (result == RESULT_OK && u != null) arrayOf(u) else null); fileCb = null }
        else super.onActivityResult(code, result, data)
    }


    /** 2026-11-21 使用者「有問題 你讓程式自己報告」:當機原因送雲端 ?pbug=put(Claude 讀 ?pbug=list) */
    private fun sendBug(kind: String, text: String) {
        kotlin.concurrent.thread {
            try {
                val body = org.json.JSONObject().put("app", APPNAME).put("ver", BuildConfig.VERSION_NAME).put("kind", kind).put("msg", text.take(500)).put("stack", text.take(2000))
                    .put("dev", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL} Android ${android.os.Build.VERSION.RELEASE} (SDK ${android.os.Build.VERSION.SDK_INT})").toString()
                val c = java.net.URL("https://twse-proxy.redwinds542688.workers.dev/?pbug=put").openConnection() as java.net.HttpURLConnection
                c.requestMethod = "POST"; c.doOutput = true; c.connectTimeout = 10000; c.readTimeout = 10000; c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }; c.responseCode
            } catch (e: Exception) {}
        }
    }

    private var cloudOk = false
    private var renderDead = false
    /** 讀雲端最新的畫面;內容不像夥伴看盤(例如雲端回錯誤頁)就不用 */
    private fun cloudPage(): WebResourceResponse? = try {
        val c = URL("https://twse-proxy.redwinds542688.workers.dev/partner/?app=1&_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
        c.connectTimeout = 4000; c.readTimeout = 6000; c.useCaches = false
        if (c.responseCode != 200) null else {
            val b = c.inputStream.use { it.readBytes() }
            val t = String(b, Charsets.UTF_8)
            if (t.contains("夥伴看盤") && t.contains("</html>")) { cloudOk = true; WebResourceResponse("text/html", "utf-8", ByteArrayInputStream(b)) } else null
        }
    } catch (e: Exception) { cloudOk = false; null }

    private var pausedAt = 0L
    override fun onSaveInstanceState(out: Bundle) { super.onSaveInstanceState(out); if (!renderDead) try { web.saveState(out) } catch (e: Exception) {} }
    override fun onResume() {
        super.onResume(); web.onResume()
        // 離開超過 10 分鐘再回來 → 重新載入(拿到雲端最新的畫面);上次是用內建那份(沒網路)回來也再試一次雲端
        if (pausedAt > 0 && (!cloudOk || SystemClock.elapsedRealtime() - pausedAt > 10 * 60_000L)) web.reload()
    }
    override fun onPause() { pausedAt = SystemClock.elapsedRealtime(); web.onPause(); super.onPause() }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() { if (web.canGoBack()) web.goBack() else super.onBackPressed() }

    /** 2026-11-15 使用者「主程式跟看盤版 同時開啟 看盤才不通知」:名揚四海「正在開著」(它的常駐通知在)→ 網頁那邊不響不震;只是有裝、沒開 → 照常響(MainWatch) */
    inner class Host {
        @JavascriptInterface fun mainRunning(): Boolean = MainWatch.granted(this@MainActivity) && MainWatch.mainRunning
        @JavascriptInterface fun mainInstalled(): Boolean = listOf("tw.mysh.monitor", "tw.mysh.monitor.viewer").any {
            try { packageManager.getPackageInfo(it, 0); true } catch (e: Exception) { false } }
        @JavascriptInterface fun watchGranted(): Boolean = MainWatch.granted(this@MainActivity)
        @JavascriptInterface fun openWatchSettings() { runOnUiThread { try { startActivity(Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) } catch (e: Exception) {} } }
        @JavascriptInterface fun version(): String = BuildConfig.VERSION_NAME
        @JavascriptInterface fun cloud(): Boolean = cloudOk   // 畫面是雲端最新的(true)還是 App 內建的(false,沒網路)
    }

    companion object { const val REQ_CAM = 11; const val REQ_FILE = 12; const val REQ_NOTI = 13; const val APPNAME = "夥伴看盤" }
}
