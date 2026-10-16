package tw.mysh.monitor

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.util.Log
import java.time.DayOfWeek
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

/** 全自動:
 *  1. 看門狗:每 15 分鐘「精確鬧鐘」叫一次,服務不在就重啟(精確鬧鐘才有權限從背景啟動前景服務)。
 *  2. 每個交易日 08:50 另外一個精確鬧鐘,確保開盤前一定在跑(就算昨天被系統殺掉、使用者沒開 App)。
 *  3. 開機 / App 更新後自動啟動。
 *  只有使用者自己按「停止背景監控」才會停,停了鬧鐘也取消,直到再按啟動。 */
object Watchdog {
    private const val REQ_WD = 77
    private const val REQ_DAILY = 78
    private val TZ: ZoneId = ZoneId.of("Asia/Taipei")
    private fun pi(ctx: Context, req: Int, what: String): PendingIntent =
        PendingIntent.getBroadcast(ctx, req, Intent(ctx, WatchdogReceiver::class.java).putExtra("what", what),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    private fun exact(am: AlarmManager, type: Int, at: Long, pi: PendingIntent) {
        try {
            if (Build.VERSION.SDK_INT >= 23) am.setExactAndAllowWhileIdle(type, at, pi) else am.setExact(type, at, pi)
        } catch (e: SecurityException) { am.setAndAllowWhileIdle(type, at, pi) }   // 沒有精確鬧鐘權限就退回一般鬧鐘
    }
    fun schedule(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        exact(am, AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + 15 * 60_000L, pi(ctx, REQ_WD, "watchdog"))
        scheduleDaily(ctx)
    }
    /** 下一個交易日(週一~五)08:50 */
    fun scheduleDaily(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        var t = ZonedDateTime.now(TZ).withHour(8).withMinute(50).withSecond(0).withNano(0)
        if (!t.isAfter(ZonedDateTime.now(TZ))) t = t.plusDays(1)
        while (t.dayOfWeek == DayOfWeek.SATURDAY || t.dayOfWeek == DayOfWeek.SUNDAY) t = t.plusDays(1)
        exact(am, AlarmManager.RTC_WAKEUP, t.toInstant().toEpochMilli(), pi(ctx, REQ_DAILY, "daily"))
        Log.i("mysh", "daily alarm at $t")
    }
    fun restartSoon(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        exact(am, AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + 3000L, pi(ctx, REQ_WD, "restart"))
    }
    fun cancel(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.cancel(pi(ctx, REQ_WD, "watchdog")); am.cancel(pi(ctx, REQ_DAILY, "daily"))
    }
    fun userStopped(ctx: Context) = ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).getBoolean("userStopped", false)
}

class WatchdogReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        val what = intent?.getStringExtra("what") ?: "?"
        val stopped = Watchdog.userStopped(ctx)
        Log.i("mysh", "alarm $what: running=${MonitorService.running} userStopped=$stopped")
        if (stopped) return
        if (!MonitorService.running && MonitorService.shouldRun()) MonitorService.start(ctx)   // 非交易時段不啟動,只把鬧鐘接下去
        // 鬧鐘是一次性的,每次響完再排下一次
        if (what == "daily") Watchdog.scheduleDaily(ctx) else Watchdog.schedule(ctx)
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        if (!Watchdog.userStopped(ctx)) { if (MonitorService.shouldRun()) MonitorService.start(ctx); Watchdog.schedule(ctx) }
    }
}
