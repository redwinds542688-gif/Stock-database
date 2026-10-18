package tw.mysh.monitor

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import android.webkit.WebView
import java.time.DayOfWeek
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlin.concurrent.thread

/** 前景服務:自己開一個看不見的 WebView 載入同一份程式,每 5 秒叫 bgTick(),由網頁決定要不要通知 */
class MonitorService : Service() {
    companion object {
        const val ACTION_START = "tw.mysh.monitor.START"
        const val ACTION_STOP = "tw.mysh.monitor.STOP"
        const val NOTIF_ID = 1
        @Volatile var running = false
        @Volatile var status = "未啟動"
        @Volatile var lastTick = ""
        @Volatile var instance: MonitorService? = null
        /** 測試用:不管開不開盤都敲一次;replay = 今天所有點重新通知 */
        fun forceTick(replay: Boolean) { instance?.let { svc -> svc.handler.post { svc.web?.evaluateJavascript("(async()=>{ try{ return await bgTick(true, ${replay}); }catch(e){ return 'err '+e.message; } })().then(r=>Android.tickResult(String(r)))", null) } } }
        fun start(ctx: Context) {
            ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).edit().putBoolean("userStopped", false).apply()
            val i = Intent(ctx, MonitorService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }
        fun stop(ctx: Context) {
            ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).edit().putBoolean("userStopped", true).apply()
            Watchdog.cancel(ctx)
            ctx.startService(Intent(ctx, MonitorService::class.java).setAction(ACTION_STOP))
        }
        private val TZ: ZoneId = ZoneId.of("Asia/Taipei")
        /** 服務該不該活著:週一~五 08:45~13:35;其他時間服務自己停掉(通知列就不會掛著),下個交易日 08:50 鬧鐘再叫起來 */
        fun shouldRun(): Boolean {
            val now = ZonedDateTime.now(TZ)
            if (now.dayOfWeek == DayOfWeek.SATURDAY || now.dayOfWeek == DayOfWeek.SUNDAY) return false
            val t = now.toLocalTime()
            return !t.isBefore(LocalTime.of(8, 45)) && !t.isAfter(LocalTime.of(13, 35))
        }
        fun inSession(): Boolean {
            val now = ZonedDateTime.now(TZ)
            if (now.dayOfWeek == DayOfWeek.SATURDAY || now.dayOfWeek == DayOfWeek.SUNDAY) return false
            val t = now.toLocalTime()
            return !t.isBefore(LocalTime.of(8, 55)) && !t.isAfter(LocalTime.of(13, 32))
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var web: WebView? = null
    private var pageReady = false
    private var wake: PowerManager.WakeLock? = null
    private var ticks = 0
    private var pageLoadedAt = 0L
    private var loadedBuild = 0L       // 服務網頁目前載入的版本戳
    private var lastUpdateCheck = 0L   // 上次去 GitHub 比對版本的時間
    private var pendingReload = false  // 盤中下載了新版、但還有持倉,等沒持倉再重載
    private val dot by lazy { OverlayDot(this) }

    private val tick = object : Runnable {
        override fun run() {
            val w = web
            val session = inSession()
            if (!shouldRun()) {                      // 收盤了:自己停掉,通知列不再掛著;鬧鐘會在下個交易日 08:50 再啟動
                status = "收盤休眠(下個交易日 08:50 自動啟動)"
                Log.i("mysh", "out of hours, service sleeps")
                Watchdog.scheduleDaily(this@MonitorService)
                stopSelf(); return
            }
            if (w != null && !pageReady && pageLoadedAt > 0 && System.currentTimeMillis() - pageLoadedAt > 90_000L) { pageReady = true; Log.w("mysh", "page ready 逾時,直接開始敲") }   // 網頁沒回報 ready(例如清單讀取失敗)也照敲,bgTick 自己會回 nocodes
            if (w != null && pageReady && session) {
                w.evaluateJavascript("(async()=>{ try{ return await bgTick(); }catch(e){ return 'err '+e.message; } })().then(r=>Android.tickResult(String(r)))", null)
                ticks++
            }
            // 網頁隔天要重新載入(清單、歷史 K 換成今天的):每天 08:55 左右重讀一次
            val now = ZonedDateTime.now(TZ)
            if (w != null && pageLoadedAt > 0 && now.hour == 8 && now.minute >= 55 && System.currentTimeMillis() - pageLoadedAt > 3600_000L) reloadPage()
            if (session && !lastTick.startsWith("holiday")) dot.show() else dot.hide()   // 只在監控中才亮點
            status = if (!session) "非交易時段(週一~五 08:55~13:32 才監控)"
                     else if (lastTick.startsWith("holiday")) "今天休市(證交所交易日 ${lastTick.removePrefix("holiday ").trim()}),每 10 分鐘確認一次"
                     else "監控中 · ${now.toLocalTime().withNano(0)} · $lastTick"
            updateForeground()
            // 2026-10-18 1.5.27 背景也自動更新網頁(使用者:「不需要按更新程式就自動更新完畢」):每 15 分鐘比對 GitHub,有新版就換檔。
            // 重新載入的時機:非交易時段立刻;盤中要等網頁回報沒有持倉(BG.pos 空)才重載,免得持倉的進場價、警示狀態被洗掉。
            val nowMs = System.currentTimeMillis()
            if (w != null && nowMs - lastUpdateCheck > 15 * 60_000L) {
                lastUpdateCheck = nowMs
                thread { try { if (Page.autoUpdate(this@MonitorService)) { Log.i("mysh", "service auto update → " + Page.currentBuild(this@MonitorService)); handler.post { pendingReload = true } } } catch (e: Exception) {} }
            }
            if (w != null && (pendingReload || Page.currentBuild(this@MonitorService) > loadedBuild)) {
                pendingReload = true
                if (!session) { pendingReload = false; reloadPage() }
                else w.evaluateJavascript("(window.BG && BG.pos) ? BG.pos.size : 0") { r -> if ((r ?: "0").trim() == "0" && pendingReload) { pendingReload = false; reloadPage() } }
            }
            val holiday = lastTick.startsWith("holiday")
            handler.postDelayed(this, if (!session) 60000L else if (holiday) 600000L else 5000L)
        }
    }

    inner class ServiceBridge : JsBridge(this@MonitorService, "service", { pageReady = true }) {
        @android.webkit.JavascriptInterface
        fun tickResult(r: String) { lastTick = r; Log.d("mysh", "tick $r") }
    }

    override fun onCreate() {
        super.onCreate()
        JsBridge.ensureChannels(this)
        if (Build.VERSION.SDK_INT >= 34) startForeground(NOTIF_ID, buildNotification("啟動中…"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIF_ID, buildNotification("啟動中…"), 0)
        else startForeground(NOTIF_ID, buildNotification("啟動中…"))
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mysh:monitor").apply { acquire() }
        createWeb()
        dot.show()
        handler.post(tick)
        running = true; instance = this
        Watchdog.schedule(this)
        NotifLog.add(this, "背景監控啟動", "", "sys")
    }

    /** 使用者從「最近使用」把 App 滑掉 → 服務會跟著被停,這裡立刻排程重啟 */
    override fun onTaskRemoved(rootIntent: Intent?) {
        Watchdog.restartSoon(this)
        super.onTaskRemoved(rootIntent)
    }

    private fun createWeb() {
        try {
            val w = WebView(this)
            Page.setup(w, ServiceBridge())
            w.loadUrl(Page.url(this))
            web = w; pageReady = false; pageLoadedAt = System.currentTimeMillis(); loadedBuild = Page.currentBuild(this)
        } catch (e: Exception) { Log.e("mysh", "webview create failed", e); status = "WebView 建立失敗:" + e.message }
    }

    fun reloadPage() {
        pageReady = false
        web?.loadUrl(Page.url(this)); pageLoadedAt = System.currentTimeMillis(); loadedBuild = Page.currentBuild(this)
    }

    /** Android 一個 App 只會有一個 Service 實例:重複 start 只會再叫一次 onStartCommand,不會再建一個;
        這裡再保險一次:已經在跑(web != null)就什麼都不做,不會重建 WebView、不會多一組計時器 */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopSelf(); return START_NOT_STICKY }
        if (web != null) Log.d("mysh", "service already running, ignore duplicate start") else { createWeb(); handler.removeCallbacks(tick); handler.post(tick) }
        return START_STICKY
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 3, Intent(this, MonitorService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val active = text.startsWith("監控中")                       // 狀態列:監控中 ● 實心、其他 ○ 空心(系統會把小圖示變白色,顏色只在下拉通知列看得到)
        return Notification.Builder(this, JsBridge.CH_MONITOR)
            .setSmallIcon(if (active) R.drawable.ic_stat_on else R.drawable.ic_stat_off)
            .setColor(if (active) 0xFF1DD75B.toInt() else 0xFF8A94A6.toInt())
            .setContentTitle(if (active) "● 名揚四海 監控中" else "○ 名揚四海 背景監控").setContentText(text)
            .setOngoing(true).setOnlyAlertOnce(true).setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "停止監控", stop).build())
            .build()
    }

    private var lastShown = ""
    private fun updateForeground() {
        if (status == lastShown) return
        lastShown = status
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        nm.notify(NOTIF_ID, buildNotification(status))
    }

    override fun onDestroy() {
        running = false; status = "已停止"; instance = null
        NotifLog.add(this, "背景監控停止", "", "sys")
        handler.removeCallbacks(tick)
        dot.hide()
        try { web?.destroy() } catch (e: Exception) {}
        web = null
        try { wake?.release() } catch (e: Exception) {}
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
