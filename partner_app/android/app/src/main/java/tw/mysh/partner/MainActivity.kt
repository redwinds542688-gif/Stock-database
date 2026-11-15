package tw.mysh.partner

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader

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
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web = WebView(this)
        web.setBackgroundColor(0xFF0B0E14.toInt())
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true                    // 金鑰、靜音設定存這裡
            mediaPlaybackRequiresUserGesture = false    // 通知聲音
            allowFileAccess = false
            allowContentAccess = true
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse? = loader.shouldInterceptRequest(req.url)
        }
        web.webChromeClient = object : WebChromeClient() {
            // 掃描金鑰 QR Code:網頁要相機 → 先問 Android 相機權限,同意了才給網頁
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    if (request.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                        else { pendingPerm = request; requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAM) }
                    } else request.deny()
                }
            }
            // 「從相簿選 QR Code 圖片」
            override fun onShowFileChooser(view: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null); fileCb = cb
                return try { startActivityForResult(Intent(Intent.ACTION_GET_CONTENT).setType("image/*").addCategory(Intent.CATEGORY_OPENABLE), REQ_FILE); true }
                catch (e: Exception) { fileCb = null; false }
            }
        }
        setContentView(web)
        if (savedInstanceState != null) web.restoreState(savedInstanceState)
        else web.loadUrl("https://appassets.androidplatform.net/assets/mine.html")
        Updater.check(this)   // 打開 App 時檢查一次版本,有新版才問
        UpdateAlarm.schedule(this)   // 每個平日 08:30(開盤前 30 分鐘)再檢查一次
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTI)   // 08:30 有新版時要能跳通知
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
    override fun onResume() { super.onResume(); web.onResume() }
    override fun onPause() { web.onPause(); super.onPause() }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() { if (web.canGoBack()) web.goBack() else super.onBackPressed() }

    companion object { const val REQ_CAM = 11; const val REQ_FILE = 12; const val REQ_NOTI = 13 }
}
