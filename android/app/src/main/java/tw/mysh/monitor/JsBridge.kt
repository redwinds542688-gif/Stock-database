package tw.mysh.monitor

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.util.Log
import android.webkit.JavascriptInterface
import java.util.concurrent.atomic.AtomicInteger

/** 網頁用 Android.notify(標題, 內容, 類別) / Android.log(文字) / Android.ready(檔數) 跟 App 溝通 */
open class JsBridge(private val ctx: Context, private val tag: String, private val onReady: ((Int) -> Unit)? = null) {
    /** 2026-10-19 使用者:右下角「≡」取消,改成長按網頁的「設定」鍵開同一個選單;MainActivity 把開選單的動作掛在這裡 */
    var onMenu: (() -> Unit)? = null
    @JavascriptInterface
    fun openMenu() { onMenu?.invoke() }
    /** 2026-11-02 使用者「從螢幕最左邊或最右邊往內滑就等於按一次返回 這個不需要」:網頁告訴 App 通知欄在哪(CSS px),App 把那段左右邊緣從系統返回手勢排除 */
    var onGesture: ((Int, Int) -> Unit)? = null
    @JavascriptInterface
    fun gestureZone(top: Int, h: Int) { onGesture?.invoke(top, h) }
    /** 2026-10-30 使用者「Android ID … 這個可以」:系統給這個 App 的識別碼(解除安裝重裝不變,恢復原廠才換),免費 7 天用;不需要權限 */
    @JavascriptInterface
    fun androidId(): String = try { Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID) ?: "" } catch (e: Exception) { "" }

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

    /** 2026-10-31 這版 App 會的功能(網頁用來判斷):lv = 虧損分級通知、win = 賺錢慶祝通知 */
    @JavascriptInterface
    fun caps(): String = "lv,win"

    /** 2026-10-24 使用者:「照這樣的排版 改 判一二三 的訊息 跟 轉折第一則訊息」「安卓系統的通知 也一樣」
     *  lay = {t:[上列 3 欄], b:[下列 3 欄], c:上列顏色, bg:turn/add/alert/long/short/profit/loss}:畫成一張圖放進通知(兩列、三欄「·」上下對齊、字放到最大、底色網紋)。
     *  lay 是 null 或畫圖失敗就照舊的文字通知。點通知一樣開那檔的 K 圖。 */
    @JavascriptInterface
    fun notify2(title: String?, body: String?, kind: String?, lay: String?) {
        try {
            ensureChannels(ctx)
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            // 2026-10-19 使用者:「在這點選泰鼎,程式就直接跳到泰鼎 K 線圖畫面」→ 通知帶代號 / 名稱,點了 MainActivity 直接開那檔的圖;每則用不同 requestCode 才不會互相蓋掉
            val m = Regex("^(?:[^\\p{L}\\p{N}\\s]+\\s*)*(\\S+) (\\d{4,6}[A-Z]*)").find(title ?: "")   // 2026-11-09 使用者「改」:標題前面不管是什麼符號(📌 ↩ ⚠⚠ ✅ …)都跳過,抓得到名稱 + 代號
            val id = seq.incrementAndGet()
            val open = Intent(ctx, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                if (m != null) { putExtra("open_code", m.groupValues[2]); putExtra("open_name", m.groupValues[1]) }
            }
            val pi = PendingIntent.getActivity(ctx, id, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            // 2026-10-31 使用者「浮輸1,2,3,4 訊息通知 要有危機感 一則要比一則嚴重」:電腦推的虧損分級 lv1~lv5,顏色 黃 → 橘 → 紅 → 深紅,聲音 / 震動一級比一級長
            val lv = Regex("^lv(\\d)$").find(kind ?: "")?.groupValues?.get(1)?.toIntOrNull() ?: 0
            // 2026-10-31 使用者「要有慶祝的通知」:賺錢分級 win1~win5,綠 → 青 → 金
            val win = Regex("^win(\\d)$").find(kind ?: "")?.groupValues?.get(1)?.toIntOrNull() ?: 0
            val color = when {
                win == 1 -> 0xFF66BB6A.toInt()
                win == 2 -> 0xFF00C853.toInt()
                win == 3 -> 0xFF00BFA5.toInt()
                win == 4 -> 0xFFFFD54F.toInt()
                win >= 5 -> 0xFFFFC107.toInt()
                lv == 1 -> 0xFFFFC107.toInt()
                lv == 2 -> 0xFFFF8C00.toInt()
                lv == 3 -> 0xFFFF3B3B.toInt()
                lv == 4 -> 0xFFD50000.toInt()
                lv >= 5 -> 0xFF8B0000.toInt()
                kind == "long" || kind == "loss" -> 0xFFFF3B3B.toInt()
                kind == "short" || kind == "profit" -> 0xFF1DD75B.toInt()
                else -> 0xFFD8DEE9.toInt()
            }
            val danger = kind == "loss" || lv >= 2
            val n = Notification.Builder(ctx, if (danger) CH_DANGER else CH_SIGNAL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(title ?: "訊號").setContentText(body ?: "")
                .setStyle(Notification.BigTextStyle().bigText(body ?: ""))
                .also { nb -> NotifCard.apply(ctx, nb, lay) }
                .setColor(color).setColorized(danger || lv >= 1 || win >= 1)
                .setCategory(Notification.CATEGORY_ALARM).setPriority(Notification.PRIORITY_HIGH)
                .setGroup(if (danger) "danger" else "signals")
                .setAutoCancel(true).setContentIntent(pi).setWhen(System.currentTimeMillis()).setShowWhen(true)
                .build()
            nm.notify(id, n)
            // 2026-11-05 14:30 使用者「2改N8」:停利 / 賺錢平倉用收銀叮(mysh_profit)
            // 2026-11-05 14:37 使用者「A 照你說的做」:收盤出場 / 強平(kind exit,標題沒有 ✅ ⚠)照標題的 ±% 選聲音:賺 → 收銀叮、賠 → 下滑音、平盤 → 低音叮
            val xp = if (kind == "exit") Regex("([+\\-−])\\s*(\\d+(?:\\.\\d+)?)%").find(title ?: "")?.let { mm -> val v = mm.groupValues[2].toDoubleOrNull() ?: 0.0; if (v == 0.0) 0 else if (mm.groupValues[1] == "+") 1 else -1 } ?: 0 else 0
            val profit = kind == "profit" || (kind == "exit" && ((title ?: "").startsWith("✅") || ((title ?: "").let { !it.startsWith("⚠") } && xp > 0)))
            val exitLoss = kind == "exit" && ((title ?: "").startsWith("⚠") || xp < 0)
            if (lv > 0) Alert.buzzLevel(ctx, lv) else if (win > 0) Alert.cheer(ctx, win) else if (profit) Alert.buzzProfit(ctx) else if (exitLoss) Alert.buzz(ctx, true) else if (kind == "pc") Alert.buzzPc(ctx) else if ((kind == "long" || kind == "short") && Regex("·\\s*超額\\s*·").containsMatchIn(body ?: "")) Alert.buzzOver(ctx) else Alert.buzz(ctx, danger)   // 2026-11-05 14:34 使用者「6改A3」:電腦提醒用三連嗶
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
