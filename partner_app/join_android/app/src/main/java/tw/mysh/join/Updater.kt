package tw.mysh.join

import android.app.Activity
import android.app.AlertDialog
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * 夥伴加入 App 自動更新。
 * 2026-11-15 使用者「看盤版 基本上不會改版 但也需要具備自動更新的能力」「每次開盤前30分鐘 跟開啟程式時 檢查一次版本」
 * 2026-11-16 21:45 使用者「而且要自己自動更新」:有新版就自己下載、自己裝好,不用按「下載安裝」「安裝」
 *   - Android 12 以上:系統允許 App 更新自己不用再問(PackageInstaller + USER_ACTION_NOT_REQUIRED),金鑰和設定都保留;
 *     裝的時候 App 會關掉一下,裝好跳通知「已更新」,點一下打開(Android 不准 App 自己在背景跳回來)
 *   - Android 11 以下,或系統還是要問:會跳系統的「安裝」畫面,按一次就好
 *   - 第一次要允許「安裝不明來源的應用程式」(只要設一次),沒允許時會帶你去設定
 * 2026-11-16 22:20 使用者「夥伴加入 也是app 自動更新 不用重新下載」:照夥伴看盤同一套,打開 App 時檢查(夥伴加入不常開,不排 08:30)。畫面本來就每次讀雲端最新的;
 * 只有手機功能改了才有新版 App。版本號讀 partner_app/join_apk/version.json;安裝檔從 /partner/join/app.apk(雲端暫存 60 秒)。
 */
object Updater {
    private const val VER = "https://twse-proxy.redwinds542688.workers.dev/?gh=raw&path=partner_app/join_apk/version.json"
    private const val APK = "https://twse-proxy.redwinds542688.workers.dev/partner/join/app.apk"
    const val ACTION_DONE = "tw.mysh.join.INSTALL_DONE"
    data class Info(val code: Int, val name: String)
    @Volatile private var busy = false

    fun fetchInfo(): Info? = try {
        val c = URL("$VER&_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
        c.connectTimeout = 15000; c.readTimeout = 20000; c.useCaches = false
        if (c.responseCode != 200) null else JSONObject(c.inputStream.bufferedReader(Charsets.UTF_8).readText()).let { Info(it.optInt("versionCode", 0), it.optString("versionName", "?")) }
    } catch (e: Exception) { null }

    fun canInstall(ctx: Context): Boolean = Build.VERSION.SDK_INT < 26 || ctx.packageManager.canRequestPackageInstalls()

    /** 打開 App 時:有新版 → 自己下載、自己裝(沒允許安裝時才問一次要不要去設定) */
    fun check(act: Activity) {
        thread {
            val info = fetchInfo() ?: return@thread
            if (info.code <= BuildConfig.VERSION_CODE) return@thread
            if (!canInstall(act)) { act.runOnUiThread { askPermission(act, info) }; return@thread }
            val err = update(act.applicationContext, info)
            if (err != null) act.runOnUiThread {
                if (!act.isFinishing) AlertDialog.Builder(act).setTitle("自動更新沒成功").setMessage("$err\n\n下次打開 App 會再自動試一次。").setPositiveButton("好", null).show()
            }
        }
    }

    private fun askPermission(act: Activity, info: Info) {
        if (act.isFinishing) return
        AlertDialog.Builder(act).setTitle("夥伴加入有新版 ${info.name}")
            .setMessage("要讓 App 以後自己自動更新,第一次要允許「夥伴加入」安裝應用程式(只要設一次)。\n\n按「去設定」打開開關,回來後 App 會自己更新。")
            .setPositiveButton("去設定") { _, _ -> act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${act.packageName}"))) }
            .setNegativeButton("先不要", null).show()
    }

    /** 下載 → 確認是新版 → 交給系統安裝(不用按)。回傳錯誤訊息;成功(已交給系統)回 null */
    fun update(ctx: Context, info: Info): String? {
        if (busy) return null
        busy = true
        try {
            val dir = File(ctx.cacheDir, "apk").apply { mkdirs() }
            dir.listFiles()?.forEach { try { it.delete() } catch (e: Exception) {} }
            val out = File(dir, "PartnerJoin-${info.code}.apk")
            val c = URL("$APK?v=${info.code}&_=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
            c.connectTimeout = 20000; c.readTimeout = 120000; c.useCaches = false; c.instanceFollowRedirects = true
            if (c.responseCode != 200) return "下載失敗(HTTP ${c.responseCode})"
            c.inputStream.use { i -> out.outputStream().use { o -> i.copyTo(o) } }
            val pi = ctx.packageManager.getPackageArchiveInfo(out.absolutePath, 0) ?: return "下載的檔案不對"
            if (pi.packageName != ctx.packageName) return "下載的檔案不對"
            @Suppress("DEPRECATION") if (pi.versionCode < info.code) return "雲端還沒換成新版,過幾分鐘會再試"
            val inst = ctx.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            val id = inst.createSession(params)
            inst.openSession(id).use { s ->
                s.openWrite("app.apk", 0, out.length()).use { o -> out.inputStream().use { it.copyTo(o) }; s.fsync(o) }
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                val done = PendingIntent.getBroadcast(ctx, id, Intent(ctx, InstallReceiver::class.java).setAction(ACTION_DONE), flags)
                s.commit(done.intentSender)
            }
            return null
        } catch (e: Exception) {
            return e.message ?: e.toString()
        } finally { busy = false }
    }
}

/** 系統裝完(或要使用者按一下)時回報這裡:要按 → 打開系統的安裝畫面;失敗 → 不打擾,下次再試 */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE) != PackageInstaller.STATUS_PENDING_USER_ACTION) return
        @Suppress("DEPRECATION") val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
        try { ctx.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        catch (e: Exception) { try { DoneNote.newVersion(ctx) } catch (x: Exception) {} }   // 背景打不開安裝畫面(08:30 App 沒開)→ 改跳通知
    }
}

/** 裝好新版(MY_PACKAGE_REPLACED)→ 跳「已自動更新」;背景打不開系統安裝畫面 → 跳「有新版,點這裡打開」 */
object DoneNote {
    private const val CH = "update"
    private fun post(ctx: Context, title: String, text: String) {
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(android.app.NotificationChannel(CH, "夥伴加入更新", android.app.NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(ctx, 3, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        @Suppress("DEPRECATION")
        val b = if (Build.VERSION.SDK_INT >= 26) android.app.Notification.Builder(ctx, CH) else android.app.Notification.Builder(ctx)
        nm.notify(77, b.setSmallIcon(android.R.drawable.stat_sys_download_done).setContentTitle(title).setContentText(text).setContentIntent(open).setAutoCancel(true).build())
    }
    fun done(ctx: Context) = post(ctx, "夥伴加入已自動更新到 ${BuildConfig.VERSION_NAME}", "點這裡打開")
    fun newVersion(ctx: Context) = post(ctx, "夥伴加入有新版", "點這裡打開 App 更新")
}

class UpdateDoneReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) try { DoneNote.done(ctx.applicationContext) } catch (e: Exception) {}
    }
}
