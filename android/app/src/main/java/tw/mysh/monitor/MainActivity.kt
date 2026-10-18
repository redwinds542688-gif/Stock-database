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
        web.loadUrl(Page.url(this)); loadedBuild = Page.currentBuild(this)
        findViewById<TextView>(R.id.menuBtn).setOnClickListener { showMenu(it as TextView) }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        // 第一次打開:自動啟動背景監控
        if (MonitorService.shouldRun()) { if (!MonitorService.running) MonitorService.start(this) }
        else { Watchdog.schedule(this); getSharedPreferences("mysh", MODE_PRIVATE).edit().putBoolean("userStopped", false).apply() }   // 非交易時段:只排鬧鐘,服務不啟動
        handleTest(intent)
        web.postDelayed({ Updater.check(this, false) }, 4000)     // 打開 App 時靜靜檢查有沒有新版(6 小時一次)
    }

    private var lastAutoPage = 0L
    private var loadedBuild = 0L   // 這個畫面目前載入的網頁版本戳
    override fun onResume() {
        super.onResume()
        // 網頁自動更新(2026-10-18;同日 1.5.27 使用者:「不需要按更新程式就自動更新完畢」):每次回到前景都比對 GitHub(原本 10 分鐘一次,
        // 現在只擋 15 秒內重複);背景服務也每 15 分鐘自己查(MonitorService),服務換好檔案後這裡回前景看到檔案比載入的新就重新載入
        val now = System.currentTimeMillis()
        if (now - lastAutoPage < 15_000L) return
        lastAutoPage = now
        thread {
            val changed = Page.autoUpdate(this)
            runOnUiThread {
                val cur = Page.currentBuild(this)
                if (changed || cur > loadedBuild) {
                    if (changed) toast("網頁已自動更新到新版 $cur")
                    loadedBuild = cur; web.loadUrl(Page.url(this))
                    if (changed && MonitorService.running) { MonitorService.stop(this); web.postDelayed({ MonitorService.start(this) }, 1500) }   // 服務自己換的就不用重啟,它已經重載過了
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent?) { super.onNewIntent(intent); handleTest(intent) }

    /** 測試鉤子(adb):am start -n tw.mysh.monitor/.MainActivity --es test notify|tick|replay */
    private fun handleTest(intent: Intent?) {
        when (intent?.getStringExtra("test")) {
            "notify" -> { JsBridge(this, "test").notify("測試 群創 3481 做空進場 49.40", "10:24 空✓ · 停利 47.42 停損 50.14", "short")
                          JsBridge(this, "test").notify("⚠⚠ 測試 麗正 2302 停損 -1.5% 立即賣出", "09:49 09:37 進 46.00 → 現價 45.35", "loss") }
            "tick" -> web.postDelayed({ MonitorService.forceTick(false) }, 20000)
            "replay" -> web.postDelayed({ MonitorService.forceTick(true) }, 20000)
        }
    }

    private fun showMenu(anchor: TextView) {
        val m = PopupMenu(this, anchor)
        // 2026-10-19 使用者:「啟動 / 停止背景監控」拿掉。監控全自動;要強制重啟就長按亮著的判鍵(Android.restartMonitor);要停就返回鍵 → 結束程式
        m.menu.add(0, 2, 1, "監控狀態")
        m.menu.add(0, 3, 2, "關閉電池最佳化(必做)")
        m.menu.add(0, 4, 3, "更新程式(從 GitHub)")
        m.menu.add(0, 9, 3, "更新 App(從 GitHub)")
        m.menu.add(0, 5, 4, "測試通知(進場)")
        m.menu.add(0, 7, 5, "測試通知(賠錢平倉)")
        m.menu.add(0, 8, 5, if (Settings.canDrawOverlays(this)) "螢幕角落監控點:已允許" else "允許螢幕角落監控點(需權限)")
        m.menu.add(0, 6, 6, "重新載入畫面")
        m.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                2 -> AlertDialog.Builder(this).setTitle("監控狀態")
                        .setMessage("服務:${if (MonitorService.running) "執行中" else if (MonitorService.shouldRun()) "未執行" else "收盤休眠(週一~五 08:45 起床,13:35 休眠)"}\n${MonitorService.status}\n最後一次:${MonitorService.lastTick}\n\n全自動:每個交易日 08:50 自動啟動、每 15 分鐘看門狗、開機自啟;只有按「停止背景監控」才會停。\n程式來源:${Page.url(this)}")
                        .setPositiveButton("好", null).show()
                3 -> batteryWhitelist()
                4 -> updatePage()
                9 -> Updater.check(this, true)
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

    /** 返回鍵(2026-10-19 使用者:「虛擬按鍵 返回 出現離開程式 確認 結束程式」):圖表開著就只關圖表;否則跳「離開程式?」
     *  確認 = 回桌面,背景監控照跑、通知照發;結束程式 = 停止背景監控(看門狗也不再自動啟動)並關閉;取消 = 留在程式 */
    override fun onBackPressed() {
        web.evaluateJavascript("(function(){ try{ var s = document.getElementById('sheet'); if (s && s.classList.contains('open')){ closeChart(); return 'closed'; } }catch(e){} return ''; })()") { r ->
            if (r != null && r.contains("closed")) return@evaluateJavascript
            AlertDialog.Builder(this).setTitle("離開程式?")
                .setMessage("確認:回到桌面,背景監控繼續跑、通知照發。\n結束程式:停止背景監控並關閉程式。")
                .setPositiveButton("確認") { _, _ -> moveTaskToBack(true) }
                .setNegativeButton("結束程式") { _, _ ->
                    if (MonitorService.running) MonitorService.stop(this)
                    getSharedPreferences("mysh", MODE_PRIVATE).edit().putBoolean("userStopped", true).apply()
                    web.postDelayed({ finishAndRemoveTask() }, 300)
                }
                .setNeutralButton("取消", null).show()
        }
    }
}
