package tw.mysh.partner

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import java.util.Calendar
import java.util.TimeZone
import kotlin.concurrent.thread

/**
 * 2026-11-15 使用者「每次開盤前30分鐘 跟開啟程式時 檢查一次版本 就好」:
 *   ① 打開 App 時檢查一次(MainActivity → Updater.check)
 *   ② 每個平日台北時間 08:30(開盤前 30 分鐘)檢查一次:App 沒開也會醒來看;有新版就跳一則通知,點了打開 App → 跳「下載安裝」
 * ※ 例外:專案規則「App 不寫看時間決定行為的邏輯」,這一項是使用者明確指定的時間,只用在檢查版本,不影響看盤資料。
 */
object UpdateAlarm {
    private const val CH = "update"
    fun schedule(ctx: Context) {
        val tz = TimeZone.getTimeZone("Asia/Taipei")
        val c = Calendar.getInstance(tz).apply { set(Calendar.HOUR_OF_DAY, 8); set(Calendar.MINUTE, 30); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
        if (c.timeInMillis <= System.currentTimeMillis()) c.add(Calendar.DAY_OF_MONTH, 1)
        while (c.get(Calendar.DAY_OF_WEEK) == Calendar.SATURDAY || c.get(Calendar.DAY_OF_WEEK) == Calendar.SUNDAY) c.add(Calendar.DAY_OF_MONTH, 1)
        val pi = PendingIntent.getBroadcast(ctx, 830, Intent(ctx, UpdateReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, c.timeInMillis, pi)   // 不需要精準到秒,手機省電時可能晚幾分鐘
    }
    fun notifyNew(ctx: Context, name: String) {
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(NotificationChannel(CH, "夥伴看盤更新", NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(ctx, 1, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        @Suppress("DEPRECATION")
        val b = if (Build.VERSION.SDK_INT >= 26) android.app.Notification.Builder(ctx, CH) else android.app.Notification.Builder(ctx)
        nm.notify(830, b.setSmallIcon(R.drawable.ic_stat).setContentTitle("夥伴看盤有新版 $name").setContentText("點這裡打開 App 更新").setContentIntent(open).setAutoCancel(true).build())
    }
}

class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val app = ctx.applicationContext
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) { UpdateAlarm.schedule(app); return }
        val done = goAsync()
        thread {
            try { val info = Updater.fetchInfo(); if (info != null && info.code > BuildConfig.VERSION_CODE) UpdateAlarm.notifyNew(app, info.name) }
            catch (e: Exception) {} finally { UpdateAlarm.schedule(app); done.finish() }   // 排下一個平日 08:30
        }
    }
}
