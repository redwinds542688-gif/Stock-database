package tw.mysh.join

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader

/**
 * 自動當沖 · 夥伴加入 App(2026-11-15 使用者「做成一個app 我在這修改 雲端改版 手機自動改版 這樣比較方便」)
 * 只是一個全螢幕的網頁殼:每次打開都讀雲端最新的畫面(Worker /partner/join/ = GitHub main 的 partner_app/join/index.html),
 * 所以改畫面只要推上去,手機下次打開(或離開 10 分鐘再回來)就是新版,不用重裝。
 * 沒網路時改開 App 裡內建的那份(編 APK 當時的畫面)。殼本身改了才會跳「有新版」(Updater)。
 * 這個 App 不下單;夥伴的元大帳密由網頁在手機上先加密才送出(接真資料後)。
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private var pendingPerm: PermissionRequest? = null
    private var fileCb: ValueCallback<Array<Uri>>? = null
    private var pausedAt = 0L
    private var offline = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web = WebView(this)
        web.setBackgroundColor(0xFF0B0E14.toInt())
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            textZoom = 100                       // 不跟手機系統字體放大(會撐破版面);畫面自己照螢幕寬度算字
            cacheMode = WebSettings.LOAD_DEFAULT // 雲端回 no-cache → 每次都問有沒有新版
            allowFileAccess = false
            allowContentAccess = true
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse? = loader.shouldInterceptRequest(req.url)
            override fun onReceivedError(view: WebView, req: WebResourceRequest, err: WebResourceError) {
                if (req.isForMainFrame && !offline) { offline = true; view.loadUrl(LOCAL) }   // 沒網路:開內建那份
            }
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.host == HOST || u.host == "appassets.androidplatform.net") return false
                return try { startActivity(Intent(Intent.ACTION_VIEW, u)); true } catch (e: Exception) { true }   // 其他網址(例如下載夥伴看盤)交給瀏覽器
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    if (request.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                        else { pendingPerm = request; requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAM) }
                    } else request.deny()
                }
            }
            // 選檔案:憑證檔(.pfx)、QR Code 圖片
            override fun onShowFileChooser(view: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null); fileCb = cb
                val type = params.acceptTypes?.firstOrNull { it.isNotBlank() && !it.startsWith(".") } ?: "*/*"
                return try { startActivityForResult(Intent(Intent.ACTION_GET_CONTENT).setType(type).addCategory(Intent.CATEGORY_OPENABLE), REQ_FILE); true }
                catch (e: Exception) { fileCb = null; false }
            }
        }
        web.addJavascriptInterface(Host(), "JoinHost")
        setContentView(web)
        if (savedInstanceState != null) web.restoreState(savedInstanceState) else web.loadUrl(REMOTE)
        Updater.check(this)   // 打開 App 時檢查殼有沒有新版
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        if (code == REQ_CAM) {
            val p = pendingPerm; pendingPerm = null
            if (p != null) { if (res.isNotEmpty() && res[0] == PackageManager.PERMISSION_GRANTED) p.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) else p.deny() }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        if (code == REQ_FILE) { val u = data?.data; fileCb?.onReceiveValue(if (result == RESULT_OK && u != null) arrayOf(u) else null); fileCb = null }
        else super.onActivityResult(code, result, data)
    }

    override fun onSaveInstanceState(out: Bundle) { super.onSaveInstanceState(out); web.saveState(out) }
    override fun onPause() { pausedAt = SystemClock.elapsedRealtime(); web.onPause(); super.onPause() }
    override fun onResume() {
        super.onResume(); web.onResume()
        // 離開超過 10 分鐘再回來 → 重新讀雲端(拿到最新畫面);沒網路時開的是內建那份,回來也再試一次雲端
        if (pausedAt > 0 && (offline || SystemClock.elapsedRealtime() - pausedAt > 10 * 60_000L)) { offline = false; web.loadUrl(REMOTE) }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() { if (web.canGoBack()) web.goBack() else super.onBackPressed() }

    inner class Host {
        @JavascriptInterface fun version(): String = BuildConfig.VERSION_NAME
        @JavascriptInterface fun offline(): Boolean = offline
    }

    companion object {
        const val HOST = "twse-proxy.redwinds542688.workers.dev"
        const val REMOTE = "https://$HOST/partner/join/"
        const val LOCAL = "https://appassets.androidplatform.net/assets/join.html"
        const val REQ_CAM = 11; const val REQ_FILE = 12
    }
}
