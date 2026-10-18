package tw.mysh.monitor

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.appcompat.app.AlertDialog
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/** App 本體(APK)更新:看 GitHub 的 app/apk/version.json,比內建版本新就下載 APK 叫系統安裝程式(使用者按一下「安裝」即可,不用自己去抓檔案) */
object Updater {
    private const val RAW = "https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/app/apk/"
    data class Info(val code: Int, val name: String, val note: String)

    fun fetchInfo(): Info? = try {
        val c = URL(RAW + "version.json?_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
        c.connectTimeout = 15000; c.readTimeout = 20000
        if (c.responseCode != 200) null else {
            val j = JSONObject(c.inputStream.bufferedReader(Charsets.UTF_8).readText())
            Info(j.optInt("versionCode", 0), j.optString("versionName", "?"), j.optString("note", ""))
        }
    } catch (e: Exception) { Log.w("mysh", "version.json 讀取失敗: ${e.message}"); null }

    /** manual=false:App 打開時靜靜檢查,6 小時最多一次,有新版才問 */
    fun check(act: Activity, manual: Boolean) {
        val sp = act.getSharedPreferences("mysh", Context.MODE_PRIVATE)
        if (!manual && System.currentTimeMillis() - sp.getLong("lastUpdChk", 0) < 6 * 3600_000L) return
        thread {
            val info = fetchInfo()
            sp.edit().putLong("lastUpdChk", System.currentTimeMillis()).apply()
            act.runOnUiThread {
                if (act.isFinishing) return@runOnUiThread
                if (info == null) { if (manual) msg(act, "檢查失敗", "連不上 GitHub,稍後再試"); return@runOnUiThread }
                if (info.code <= BuildConfig.VERSION_CODE) { if (manual) msg(act, "已是最新版", "目前 ${BuildConfig.VERSION_NAME},GitHub 上是 ${info.name}"); return@runOnUiThread }
                AlertDialog.Builder(act).setTitle("有新版 App ${info.name}")
                    .setMessage("目前 ${BuildConfig.VERSION_NAME} → ${info.name}\n${info.note}\n\n按「下載安裝」會抓 APK 並打開安裝畫面,再按一次「安裝」就完成,設定都會保留。")
                    .setPositiveButton("下載安裝") { _, _ -> download(act, info) }
                    .setNegativeButton("先不要", null).show()
            }
        }
    }

    private fun download(act: Activity, info: Info) {
        if (Build.VERSION.SDK_INT >= 26 && !act.packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(act).setTitle("先允許安裝")
                .setMessage("第一次要允許「名揚四海自動交易」安裝 App(只要設一次),按「去設定」打開後回來再按一次更新。")
                .setPositiveButton("去設定") { _, _ -> act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${act.packageName}"))) }
                .setNegativeButton("取消", null).show()
            return
        }
        val dlg = AlertDialog.Builder(act).setTitle("下載中…").setMessage("約 3 MB,請稍候").setCancelable(false).show()
        thread {
            var err: String? = null
            val dir = File(act.cacheDir, "apk").apply { mkdirs() }
            dir.listFiles()?.forEach { try { it.delete() } catch (e: Exception) {} }          // 2026-10-19:先清掉以前下載的舊 APK,安裝程式才不會拿到舊檔(使用者裝到 1.5.27)
            val out = File(dir, "MyshMonitor-${info.code}.apk")                               // 檔名帶版本號,每一版都是新檔
            val urls = listOf(RAW + "MyshMonitor.apk?v=${info.code}&_=" + System.currentTimeMillis(),
                              "https://github.com/redwinds542688-gif/Stock-database/raw/main/app/apk/MyshMonitor.apk?v=${info.code}&_=" + System.currentTimeMillis())
            var got = 0
            for (u in urls) {
                try {
                    val c = URL(u).openConnection() as HttpURLConnection
                    c.connectTimeout = 20000; c.readTimeout = 120000; c.useCaches = false; c.instanceFollowRedirects = true
                    c.setRequestProperty("Cache-Control", "no-cache"); c.setRequestProperty("Pragma", "no-cache")
                    if (c.responseCode != 200) throw Exception("HTTP ${c.responseCode}")
                    c.inputStream.use { i -> out.outputStream().use { o -> i.copyTo(o) } }
                    if (out.length() < 500_000) throw Exception("檔案不完整(${out.length()} bytes)")
                    val pi = act.packageManager.getPackageArchiveInfo(out.absolutePath, 0)
                    got = pi?.versionCode ?: 0
                    val nm = pi?.versionName ?: "?"
                    Log.i("mysh", "下載到 APK 版本 $nm ($got),預期 ${info.code},來源 $u")
                    if (got >= info.code) { err = null; break }                                  // 下載到的版本要對,不對就換下一個網址再抓一次
                    err = "GitHub 傳回的還是舊版 $nm,快取還沒更新,請過 5 分鐘再試"
                } catch (e: Exception) { err = e.message ?: e.toString() }
            }
            act.runOnUiThread {
                try { dlg.dismiss() } catch (e: Exception) {}
                if (err != null) { msg(act, "下載失敗", err!!); return@runOnUiThread }
                try {
                    val uri = FileProvider.getUriForFile(act, act.packageName + ".files", out)
                    val i = Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    act.startActivity(i)
                } catch (e: Exception) { msg(act, "無法打開安裝程式", e.message ?: e.toString()) }
            }
        }
    }
    private fun msg(act: Activity, t: String, m: String) { try { AlertDialog.Builder(act).setTitle(t).setMessage(m).setPositiveButton("好", null).show() } catch (e: Exception) {} }
}
