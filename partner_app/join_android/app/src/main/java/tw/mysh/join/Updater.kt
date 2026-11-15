package tw.mysh.join

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
 * 夥伴加入 App 殼的自動更新(畫面本身每次打開都讀雲端最新的,不用更新;只有殼改了才會跳「有新版」)
 * 打開 App 時看雲端 partner_app/join_apk/version.json,比手上的新才問。
 * 比手上的新才跳「有新版」;按「下載安裝」→ 下載 APK、確認版本對 → 打開系統安裝畫面,再按一次「安裝」就好,資料都保留。
 * 檔案經 Worker ?gh=raw 讀 GitHub(和名揚四海同一條);沒新版、讀不到都不打擾。
 */
object Updater {
    private const val BASE = "https://twse-proxy.redwinds542688.workers.dev/?gh=raw&path=partner_app/join_apk/"
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
                AlertDialog.Builder(act).setTitle("夥伴加入有新版 ${info.name}")
                    .setMessage("目前 ${BuildConfig.VERSION_NAME} → ${info.name}\n\n按「下載安裝」會下載新版並打開安裝畫面,再按一次「安裝」就完成,資料都會保留。")
                    .setPositiveButton("下載安裝") { _, _ -> download(act, info) }
                    .setNegativeButton("先不要", null).show()
            }
        }
    }

    private fun download(act: Activity, info: Info) {
        if (Build.VERSION.SDK_INT >= 26 && !act.packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(act).setTitle("先允許安裝")
                .setMessage("第一次要允許「夥伴加入」安裝更新(只要設一次),按「去設定」打開後回來,下次打開 App 會再問一次。")
                .setPositiveButton("去設定") { _, _ -> act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${act.packageName}"))) }
                .setNegativeButton("取消", null).show()
            return
        }
        val dlg = AlertDialog.Builder(act).setTitle("下載中…").setMessage("約 1 MB,請稍候").setCancelable(false).show()
        thread {
            var err: String? = null
            val dir = File(act.cacheDir, "apk").apply { mkdirs() }
            dir.listFiles()?.forEach { try { it.delete() } catch (e: Exception) {} }
            val out = File(dir, "PartnerJoin-${info.code}.apk")
            try {
                val c = URL(BASE + "PartnerJoin.apk&v=${info.code}&_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
                c.connectTimeout = 20000; c.readTimeout = 120000; c.useCaches = false; c.instanceFollowRedirects = true
                if (c.responseCode != 200) throw Exception("HTTP ${c.responseCode}")
                c.inputStream.use { i -> out.outputStream().use { o -> i.copyTo(o) } }
                val pi = act.packageManager.getPackageArchiveInfo(out.absolutePath, 0)
                if (pi == null || pi.packageName != act.packageName) throw Exception("下載的檔案不對")
                @Suppress("DEPRECATION") if (pi.versionCode < info.code) throw Exception("雲端傳回的還是舊版,請過 5 分鐘再打開 App")
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
