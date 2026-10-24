package tw.mysh.monitor

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import android.webkit.JavascriptInterface
import java.util.concurrent.atomic.AtomicInteger

/** 網頁用 Android.notify(標題, 內容, 類別) / Android.log(文字) / Android.ready(檔數) 跟 App 溝通 */
open class JsBridge(private val ctx: Context, private val tag: String, private val onReady: ((Int) -> Unit)? = null) {
    /** 2026-10-19 使用者:右下角「≡」取消,改成長按網頁的「設定」鍵開同一個選單;MainActivity 把開選單的動作掛在這裡 */
    var onMenu: (() -> Unit)? = null
    @JavascriptInterface
    fun openMenu() { onMenu?.invoke() }

    companion object {
        const val CH_SIGNAL = "signals2"
        const val CH_MONITOR = "monitor"
        const val CH_DANGER = "danger2"
        private val seq = AtomicInteger(100)
        fun ensureChannels(ctx: Context) {
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CH_SIGNAL) == null) {
                val ch = NotificationChannel(CH_SIGNAL, "交易訊號", NotificationManager.IMPORTANCE_HIGH)
                ch.description = "進場、停利、停損、反向出場"
                ch.setSound(null, null); ch.enableVibration(false)        // 聲音、震動由 Alert 自己放(走鬧鐘音軌),不靠頻道
                ch.lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                ch.setBypassDnd(true)
                nm.createNotificationChannel(ch)
            }
            if (nm.getNotificationChannel(CH_DANGER) == null) {
                val ch = NotificationChannel(CH_DANGER, "賠錢平倉(危險)", NotificationManager.IMPORTANCE_HIGH)
                ch.description = "停損、超6%、賠錢出場:鬧鐘聲 + 長震動"
                ch.setSound(null, null); ch.enableVibration(false)        // 同上,由 Alert 放鬧鐘聲 + 長震動
                ch.enableLights(true); ch.lightColor = 0xFFFF3B3B.toInt()
                ch.lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                ch.setBypassDnd(true)
                nm.createNotificationChannel(ch)
            }
            for (old in listOf("signals", "danger")) try { nm.deleteNotificationChannel(old) } catch (e: Exception) {}
            if (nm.getNotificationChannel(CH_MONITOR) == null) {
                val ch = NotificationChannel(CH_MONITOR, "背景監控", NotificationManager.IMPORTANCE_LOW)
                ch.description = "監控中的固定通知"; ch.setShowBadge(false)
                nm.createNotificationChannel(ch)
            }
        }
    }

    @JavascriptInterface
    fun notify(title: String?, body: String?, kind: String?) = notify2(title, body, kind, null)

    /** 2026-10-24 使用者:「照這樣的排版 改 判一二三 的訊息 跟 轉折第一則訊息」「安卓系統的通知 也一樣」
     *  lay = {t:[上列 3 欄], b:[下列 3 欄], c:上列顏色, bg:turn/add/alert/long/short/profit/loss}:畫成一張圖放進通知(兩列、三欄「·」上下對齊、字放到最大、底色網紋)。
     *  lay 是 null 或畫圖失敗就照舊的文字通知。點通知一樣開那檔的 K 圖。 */
    @JavascriptInterface
    fun notify2(title: String?, body: String?, kind: String?, lay: String?) {
        try {
            ensureChannels(ctx)
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            // 2026-10-19 使用者:「在這點選泰鼎,程式就直接跳到泰鼎 K 線圖畫面」→ 通知帶代號 / 名稱,點了 MainActivity 直接開那檔的圖;每則用不同 requestCode 才不會互相蓋掉
            val m = Regex("^(?:⚠⚠ |⚠ |✅ )?(\\S+) (\\d{4,6}[A-Z]*)").find(title ?: "")
            val id = seq.incrementAndGet()
            val open = Intent(ctx, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                if (m != null) { putExtra("open_code", m.groupValues[2]); putExtra("open_name", m.groupValues[1]) }
            }
            val pi = PendingIntent.getActivity(ctx, id, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val color = when (kind) { "long", "loss" -> 0xFFFF3B3B.toInt(); "short", "profit" -> 0xFF1DD75B.toInt(); else -> 0xFFD8DEE9.toInt() }
            val danger = kind == "loss"
            val n = Notification.Builder(ctx, if (danger) CH_DANGER else CH_SIGNAL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(title ?: "訊號").setContentText(body ?: "")
                .setStyle(Notification.BigTextStyle().bigText(body ?: ""))
                .also { nb -> NotifCard.apply(ctx, nb, lay) }
                .setColor(color).setColorized(danger)
                .setCategory(Notification.CATEGORY_ALARM).setPriority(Notification.PRIORITY_HIGH)
                .setGroup(if (danger) "danger" else "signals")
                .setAutoCancel(true).setContentIntent(pi).setWhen(System.currentTimeMillis()).setShowWhen(true)
                .build()
            nm.notify(id, n)
            Alert.buzz(ctx, danger)
            NotifLog.add(ctx, title ?: "", body ?: "", kind ?: "")
            Log.i("mysh", "[$tag] notify $title | $body")
        } catch (e: Exception) { Log.e("mysh", "notify failed", e) }
    }

    @JavascriptInterface
    fun log(s: String?) { Log.d("mysh", "[$tag] $s") }

    @JavascriptInterface
    fun ready(n: Int) { Log.i("mysh", "[$tag] page ready, $n codes"); onReady?.invoke(n) }

    @JavascriptInterface
    fun isService(): Boolean = tag == "service"

    @JavascriptInterface
    fun monitorRunning(): Boolean = MonitorService.running

    /** 今日通知紀錄(JSON 陣列,新的在前),給網頁顯示區用 */
    @JavascriptInterface
    fun todayLog(): String = NotifLog.today(ctx)

    @JavascriptInterface
    fun logDays(): String = NotifLog.days(ctx)

    @JavascriptInterface
    fun logFor(d: String?): String = NotifLog.forDate(ctx, d ?: "")

    /** 正在用的判別式(一 / 二 / 三),存 SharedPreferences,離開 App 再回來還是同一份(2026-10-19 使用者:「判別式需要紀錄,離開程式再回來要就是離開時的判別式」)。
     *  原本只存網頁的 localStorage,內建網頁(android_asset)和下載更新後的網頁(files/)是不同來源,換頁後會讀不到,所以改由 App 存。 */
    @JavascriptInterface
    fun getRuleset(): String = ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).getString("ruleset", "") ?: ""

    /** 強制重新啟動背景監控(2026-10-19 使用者:「判一亮燈,再次長按判一便可以強制監控重新啟動」):先停再起;非交易時段只排鬧鐘,回傳 off */
    @JavascriptInterface
    fun restartMonitor(): String {
        if (!MonitorService.shouldRun()) { Watchdog.schedule(ctx); ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).edit().putBoolean("userStopped", false).apply(); return "off" }
        val h = android.os.Handler(android.os.Looper.getMainLooper())
        if (MonitorService.running) { MonitorService.stop(ctx); h.postDelayed({ MonitorService.start(ctx) }, 1500) } else MonitorService.start(ctx)
        return "restart"
    }

    /** 監控設定(滾輪的 8 個值,JSON 字串)也由 App 保存,原因同上(2026-10-19 使用者:「設定那邊有紀錄存檔嗎」) */
    @JavascriptInterface
    fun getPref(key: String?): String = ctx.getSharedPreferences("mysh.kv", Context.MODE_PRIVATE).getString(key ?: "", "") ?: ""

    @JavascriptInterface
    fun setPref(key: String?, v: String?) { ctx.getSharedPreferences("mysh.kv", Context.MODE_PRIVATE).edit().putString(key ?: "", v ?: "").apply() }

    @JavascriptInterface
    fun setRuleset(k: String?) { ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).edit().putString("ruleset", k ?: "").apply(); Log.i("mysh", "[$tag] ruleset -> $k") }
}
