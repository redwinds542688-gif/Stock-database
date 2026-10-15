package tw.mysh.monitor

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.util.Log
import android.webkit.JavascriptInterface
import java.util.concurrent.atomic.AtomicInteger

/** 網頁用 Android.notify(標題, 內容, 類別) / Android.log(文字) / Android.ready(檔數) 跟 App 溝通 */
open class JsBridge(private val ctx: Context, private val tag: String, private val onReady: ((Int) -> Unit)? = null) {
    companion object {
        const val CH_SIGNAL = "signals"
        const val CH_MONITOR = "monitor"
        private val seq = AtomicInteger(100)
        fun ensureChannels(ctx: Context) {
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CH_SIGNAL) == null) {
                val ch = NotificationChannel(CH_SIGNAL, "交易訊號", NotificationManager.IMPORTANCE_HIGH)
                ch.description = "進場、停利、停損、反向出場"
                ch.enableVibration(true); ch.vibrationPattern = longArrayOf(0, 400, 200, 400, 200, 600)
                ch.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                ch.lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                nm.createNotificationChannel(ch)
            }
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
            val color = when (kind) { "long" -> 0xFFFF3B3B.toInt(); "short" -> 0xFF1DD75B.toInt(); else -> 0xFFD8DEE9.toInt() }
            val n = Notification.Builder(ctx, CH_SIGNAL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(title ?: "訊號").setContentText(body ?: "")
                .setStyle(Notification.BigTextStyle().bigText(body ?: ""))
                .setColor(color).setColorized(false)
                .setCategory(Notification.CATEGORY_ALARM).setPriority(Notification.PRIORITY_HIGH)
                .setAutoCancel(true).setContentIntent(pi).setWhen(System.currentTimeMillis()).setShowWhen(true)
                .build()
            nm.notify(seq.incrementAndGet(), n)
            Log.i("mysh", "[$tag] notify $title | $body")
        } catch (e: Exception) { Log.e("mysh", "notify failed", e) }
    }

    @JavascriptInterface
    fun log(s: String?) { Log.d("mysh", "[$tag] $s") }

    @JavascriptInterface
    fun ready(n: Int) { Log.i("mysh", "[$tag] page ready, $n codes"); onReady?.invoke(n) }

    @JavascriptInterface
    fun isService(): Boolean = tag == "service"
}
