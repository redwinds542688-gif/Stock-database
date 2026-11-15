package tw.mysh.partner

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * 夥伴看盤 App 自動更新(2026-11-15 使用者「看盤版 基本上不會改版 但也需要具備自動更新的能力」)
 * 照名揚四海 Updater.kt 的做法另寫一份(不共用):打開 App 時 + 每個平日 08:30(UpdateAlarm)看雲端的 partner_app/apk/version.json,
 * 比手上的新才跳「有新版」;按「下載安裝」→ 下載 APK、確認版本對 → 打開系統安裝畫面,再按一次「安裝」就好,金鑰和設定都保留。
 * 檔案經 Worker ?gh=raw 讀 GitHub(和名揚四海同一條);沒新版、讀不到都不打擾。
 */
object Updater {
    private const val BASE = "https://twse-proxy.redwinds542688.workers.dev/?gh=raw&path=partner_app/apk/"
    data class Info(val code: Int, val name: String)

    fun fetchInfo(): Info? = try {
        val c = URL(BASE + "version.json&_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
        c.connectTimeout = 15000; c.readTimeout = 20000; c.useCaches = false
        if (c.responseCode != 200) null else JSONObject(c.inputStream.bufferedReader(Charsets.UTF_8).readText()).let { Info(it.optInt("versionCode", 0), it.optString("versionName", "?")) }
    } catch (e: Exception) { null }

    fun check(act: Activity) {
        thread {
            val info = fetchInfo() ?: return@thread
            if (info.code <= BuildConfig.VERSION_CODE) return@thread
            act.runOnUiThread {
                if (act.isFinishing) return@runOnUiThread
                AlertDialog.Builder(act).setTitle("夥伴看盤有新版 ${info.name}")
                    .setMessage("目前 ${BuildConfig.VERSION_NAME} → ${info.name}\n\n按「下載安裝」會下載新版並打開安裝畫面,再按一次「安裝」就完成,金鑰和設定都會保留。")
                    .setPositiveButton("下載安裝") { _, _ -> download(act, info) }
                    .setNegativeButton("先不要", null).show()
            }
        }
    }

    private fun download(act: Activity, info: Info) {
        if (Build.VERSION.SDK_INT >= 26 && !act.packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(act).setTitle("先允許安裝")
                .setMessage("第一次要允許「夥伴看盤」安裝更新(只要設一次),按「去設定」打開後回來,下次打開 App 會再問一次。")
                .setPositiveButton("去設定") { _, _ -> act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${act.packageName}"))) }
                .setNegativeButton("取消", null).show()
            return
        }
        val dlg = AlertDialog.Builder(act).setTitle("下載中…").setMessage("約 1 MB,請稍候").setCancelable(false).show()
        thread {
            var err: String? = null
            val dir = File(act.cacheDir, "apk").apply { mkdirs() }
            dir.listFiles()?.forEach { try { it.delete() } catch (e: Exception) {} }
            val out = File(dir, "PartnerViewer-${info.code}.apk")
            try {
                // 2026-11-16 00:57 使用者截圖「下載失敗 雲端傳回的還是舊版」:?gh=raw 的 .apk 雲端暫存 30 分鐘(版本號只 1 分鐘)→ 改從夥伴專用的下載點(暫存 1 分鐘);不動名揚四海共用的 gh=raw
                val c = URL("https://twse-proxy.redwinds542688.workers.dev/partner/app.apk?v=${info.code}&_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
                c.connectTimeout = 20000; c.readTimeout = 120000; c.useCaches = false; c.instanceFollowRedirects = true
                if (c.responseCode != 200) throw Exception("HTTP ${c.responseCode}")
                c.inputStream.use { i -> out.outputStream().use { o -> i.copyTo(o) } }
                val pi = act.packageManager.getPackageArchiveInfo(out.absolutePath, 0)
                if (pi == null || pi.packageName != act.packageName) throw Exception("下載的檔案不對")
                @Suppress("DEPRECATION") if (pi.versionCode < info.code) throw Exception("雲端還沒換成新版,請過 3 分鐘再打開 App")
            } catch (e: Exception) { err = e.message ?: e.toString() }
            act.runOnUiThread {
                try { dlg.dismiss() } catch (e: Exception) {}
                if (err != null) { AlertDialog.Builder(act).setTitle("下載失敗").setMessage(err).setPositiveButton("好", null).show(); return@runOnUiThread }
                try {
                    val uri = FileProvider.getUriForFile(act, act.packageName + ".files", out)
                    act.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION))
                } catch (e: Exception) { AlertDialog.Builder(act).setTitle("無法打開安裝程式").setMessage(e.message ?: e.toString()).setPositiveButton("好", null).show() }
            }
        }
    }
}
