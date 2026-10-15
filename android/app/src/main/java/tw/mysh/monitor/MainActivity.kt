package tw.mysh.monitor

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.webkit.WebView
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        JsBridge.ensureChannels(this)
        web = findViewById(R.id.web)
        Page.setup(web, JsBridge(this, "activity"))
        web.loadUrl(Page.url(this))
        findViewById<TextView>(R.id.menuBtn).setOnClickListener { showMenu(it as TextView) }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        // 第一次打開:自動啟動背景監控
        if (!MonitorService.running) MonitorService.start(this)
    }

    private fun showMenu(anchor: TextView) {
        val m = PopupMenu(this, anchor)
        val running = MonitorService.running
        m.menu.add(0, 1, 0, if (running) "停止背景監控" else "啟動背景監控")
        m.menu.add(0, 2, 1, "監控狀態")
        m.menu.add(0, 3, 2, "關閉電池最佳化(必做)")
        m.menu.add(0, 4, 3, "更新程式(從 GitHub)")
        m.menu.add(0, 5, 4, "測試通知(進場)")
        m.menu.add(0, 7, 5, "測試通知(賠錢平倉)")
        m.menu.add(0, 8, 5, if (Settings.canDrawOverlays(this)) "螢幕角落監控點:已允許" else "允許螢幕角落監控點(需權限)")
        m.menu.add(0, 6, 6, "重新載入畫面")
        m.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                1 -> { if (MonitorService.running) MonitorService.stop(this) else MonitorService.start(this); toast(if (MonitorService.running) "停止中" else "啟動中") }
                2 -> AlertDialog.Builder(this).setTitle("監控狀態")
                        .setMessage("服務:${if (MonitorService.running) "執行中" else "未執行"}\n${MonitorService.status}\n最後一次:${MonitorService.lastTick}\n\n全自動:每個交易日 08:50 自動啟動、每 15 分鐘看門狗、開機自啟;只有按「停止背景監控」才會停。\n程式來源:${Page.url(this)}")
                        .setPositiveButton("好", null).show()
                3 -> batteryWhitelist()
                4 -> updatePage()
                5 -> JsBridge(this, "test").notify("測試 群創 做空進場 49.40", "10:24 空✓ · 停利 47.42 停損 50.14", "short")
                6 -> web.loadUrl(Page.url(this))
                8 -> if (!Settings.canDrawOverlays(this)) startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))) else toast("已允許,監控中會在右上角閃綠點")
                7 -> JsBridge(this, "test").notify("⚠⚠ 測試 群創 3481 停損 -1.6% 立即回補", "11:08 10:25 進 49.40 → 現價 50.20", "loss")
            }
            true
        }
        m.show()
    }

    private fun batteryWhitelist() {
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) { toast("已經關閉電池最佳化"); return }
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    private fun updatePage() {
        toast("下載中…")
        thread {
            val (ok, msg) = Page.update(this)
            runOnUiThread {
                AlertDialog.Builder(this).setTitle(if (ok) "更新完成" else "更新失敗").setMessage(msg).setPositiveButton("好") { _, _ ->
                    if (ok) { web.loadUrl(Page.url(this)); if (MonitorService.running) { MonitorService.stop(this); web.postDelayed({ MonitorService.start(this) }, 1500) } }
                }.show()
            }
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    override fun onBackPressed() { if (web.canGoBack()) web.goBack() else super.onBackPressed() }
}
