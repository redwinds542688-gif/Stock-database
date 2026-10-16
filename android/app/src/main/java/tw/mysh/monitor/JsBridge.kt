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
    fun notify(title: String?, body: String?, kind: String?) {
        try {
            ensureChannels(ctx)
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val open = Intent(ctx, MainActivity::class.java).apply { flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP }
            val pi = PendingIntent.getActivity(ctx, 1, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val color = when (kind) { "long", "loss" -> 0xFFFF3B3B.toInt(); "short", "profit" -> 0xFF1DD75B.toInt(); else -> 0xFFD8DEE9.toInt() }
            val danger = kind == "loss"
            val n = Notification.Builder(ctx, if (danger) CH_DANGER else CH_SIGNAL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(title ?: "訊號").setContentText(body ?: "")
                .setStyle(Notification.BigTextStyle().bigText(body ?: ""))
                .setColor(color).setColorized(danger)
                .setCategory(Notification.CATEGORY_ALARM).setPriority(Notification.PRIORITY_HIGH)
                .setGroup(if (danger) "danger" else "signals")
                .setAutoCancel(true).setContentIntent(pi).setWhen(System.currentTimeMillis()).setShowWhen(true)
                .build()
            nm.notify(seq.incrementAndGet(), n)
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
}
