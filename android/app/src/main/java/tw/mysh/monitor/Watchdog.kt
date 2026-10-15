package tw.mysh.monitor

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.util.Log

/** 看門狗:每 15 分鐘系統鬧鐘叫一次,服務不在就重啟;開機也自動啟動 */
object Watchdog {
    private const val REQ = 77
    private fun pi(ctx: Context): PendingIntent =
        PendingIntent.getBroadcast(ctx, REQ, Intent(ctx, WatchdogReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    fun schedule(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.setInexactRepeating(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + 15 * 60_000L, 15 * 60_000L, pi(ctx))
    }
    fun restartSoon(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + 3000L, pi(ctx))
    }
    fun cancel(ctx: Context) { (ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager).cancel(pi(ctx)) }
}

class WatchdogReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        val stopped = ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).getBoolean("userStopped", false)
        Log.i("mysh", "watchdog: running=${MonitorService.running} userStopped=$stopped")
        if (!MonitorService.running && !stopped) MonitorService.start(ctx)
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        val stopped = ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).getBoolean("userStopped", false)
        if (!stopped) { MonitorService.start(ctx); Watchdog.schedule(ctx) }
    }
}
