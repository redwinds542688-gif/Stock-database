package tw.mysh.monitor

import android.content.Context
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

/** 訊號通知的聲音 + 震動:App 自己放,走「鬧鐘」音軌,不靠通知頻道設定
 *  (OPPO / 小米等會把不明來源 App 的通知聲音、震動關掉;鬧鐘音軌在靜音、勿擾下也會響) */
object Alert {
    private val h = Handler(Looper.getMainLooper())
    private var rt: Ringtone? = null
    private val attrs = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()

    /** 2026-11-05 14:16 使用者「通知的音效有辦法統一嗎」→「第一個 只做一般通知 一聲 叮」「輸錢平倉 一次 低 高」→「改」:
     *  不再用各手機系統預設的通知 / 鬧鐘聲,改播 App 內建的 res/raw/mysh_notify(叮)、mysh_alarm(低→高);每支手機一樣。讀不到才退回系統預設 */
    private fun own(ctx: Context, alarm: Boolean, win: Boolean = false): Uri? = try {
        Uri.parse("android.resource://" + ctx.packageName + "/" + (if (alarm) R.raw.mysh_alarm else if (win) R.raw.mysh_win else R.raw.mysh_notify))
    } catch (e: Exception) { null }   // 2026-11-05 14:25 使用者「贏錢W3」:賺錢慶祝用雙金幣(mysh_win)

    fun buzz(ctx: Context, danger: Boolean) {
        h.post { vibrate(ctx, danger); sound(ctx, danger) }
    }

    /** 2026-10-31 虧損分級 1~5:一級比一級強。
     *  1 = 一般提示音 3 秒 + 震 2 下;2 = 鬧鐘聲 6 秒 + 長震 3 下;3 = 鬧鐘聲 12 秒 + 長震 5 下;4 = 鬧鐘聲 20 秒 + 長震 8 下;5 = 鬧鐘聲 30 秒 + 長震 12 下 */
    fun buzzLevel(ctx: Context, lv: Int) {
        val n = when (lv) { 1 -> 2; 2 -> 3; 3 -> 5; 4 -> 8; else -> 12 }
        val on = if (lv <= 1) 400L else 900L
        val pattern = LongArray(n * 2 + 1).also { p -> p[0] = 0L; for (i in 0 until n) { p[1 + i * 2] = on; p[2 + i * 2] = 250L } }
        val ms = when (lv) { 1 -> 3000L; 2 -> 6000L; 3 -> 12000L; 4 -> 20000L; else -> 30000L }
        // 2026-11-05 14:27 使用者「當沖 輸錢 N5 1到5萬對應一次到五次」:浮輸分級的聲音改「水滴」(mysh_loss),1 萬響 1 次 … 5 萬響 5 次
        h.post { vibratePattern(ctx, pattern); soundTimes(ctx, R.raw.mysh_loss, lv.coerceIn(1, 5)) }
    }
    /** 2026-10-31 賺錢慶祝 1~5:輕快的短震(像拍手)+ 一般提示音,級數越高拍越多下、響越久;不用鬧鐘聲 */
    fun cheer(ctx: Context, lv: Int) {
        val n = 2 + lv * 2
        val pattern = LongArray(n * 2 + 3).also { p ->
            p[0] = 0L
            for (i in 0 until n) { p[1 + i * 2] = 70L; p[2 + i * 2] = 90L }
            p[n * 2 + 1] = 120L; p[n * 2 + 2] = 350L
        }
        h.post { vibratePattern(ctx, pattern); soundFor(ctx, false, 1500L + lv * 700L, win = true) }
    }
    /** 同一個內建聲音連響 n 次(每次間隔 0.7 秒) */
    private fun soundTimes(ctx: Context, res: Int, n: Int) {
        try { rt?.stop() } catch (e: Exception) {}
        val uri = try { Uri.parse("android.resource://" + ctx.packageName + "/" + res) } catch (e: Exception) { null } ?: return
        for (i in 0 until n) h.postDelayed({
            try { val r = RingtoneManager.getRingtone(ctx, uri) ?: return@postDelayed; r.audioAttributes = attrs
                  if (Build.VERSION.SDK_INT >= 28) r.isLooping = false; r.play(); rt = r } catch (e: Exception) { Log.e("mysh", "sound failed", e) }
        }, i * 700L)
    }
    private fun vibratePattern(ctx: Context, pattern: LongArray) {
        try {
            val vib: Vibrator = if (Build.VERSION.SDK_INT >= 31) (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
                                else @Suppress("DEPRECATION") ctx.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            if (Build.VERSION.SDK_INT >= 26) @Suppress("DEPRECATION") vib.vibrate(VibrationEffect.createWaveform(pattern, -1), attrs)
            else @Suppress("DEPRECATION") vib.vibrate(pattern, -1)
        } catch (e: Exception) { Log.e("mysh", "vibrate failed", e) }
    }
    private fun soundFor(ctx: Context, alarm: Boolean, ms: Long, win: Boolean = false) {
        try {
            try { rt?.stop() } catch (e: Exception) {}
            val uri = own(ctx, alarm, win) ?: RingtoneManager.getDefaultUri(if (alarm) RingtoneManager.TYPE_ALARM else RingtoneManager.TYPE_NOTIFICATION)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE) ?: return
            val r = RingtoneManager.getRingtone(ctx, uri) ?: return
            r.audioAttributes = attrs
            if (Build.VERSION.SDK_INT >= 28) r.isLooping = alarm   // 分級 2~5:低→高重複到時間到(越嚴重越久);一般叮一聲
            r.play(); rt = r
            h.postDelayed({ try { if (rt === r) { r.stop(); rt = null } } catch (e: Exception) {} }, ms)
        } catch (e: Exception) { Log.e("mysh", "sound failed", e) }
    }
    private fun vibrate(ctx: Context, danger: Boolean) {
        try {
            val pattern = if (danger) longArrayOf(0, 800, 300, 800, 300, 800, 300, 1500) else longArrayOf(0, 400, 200, 400, 200, 600)
            val vib: Vibrator = if (Build.VERSION.SDK_INT >= 31) (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
                                else @Suppress("DEPRECATION") ctx.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            if (Build.VERSION.SDK_INT >= 26) @Suppress("DEPRECATION") vib.vibrate(VibrationEffect.createWaveform(pattern, -1), attrs)
            else @Suppress("DEPRECATION") vib.vibrate(pattern, -1)
        } catch (e: Exception) { Log.e("mysh", "vibrate failed", e) }
    }
    private fun sound(ctx: Context, danger: Boolean) {
        try {
            try { rt?.stop() } catch (e: Exception) {}
            val uri = own(ctx, danger) ?: RingtoneManager.getDefaultUri(if (danger) RingtoneManager.TYPE_ALARM else RingtoneManager.TYPE_NOTIFICATION)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE) ?: return
            val r = RingtoneManager.getRingtone(ctx, uri) ?: return
            r.audioAttributes = attrs
            if (Build.VERSION.SDK_INT >= 28) r.isLooping = false   // 輸錢平倉:低→高一次;一般:叮一次
            r.play(); rt = r
            h.postDelayed({ try { if (rt === r) { r.stop(); rt = null } } catch (e: Exception) {} }, if (danger) 8000L else 3000L)
        } catch (e: Exception) { Log.e("mysh", "sound failed", e) }
    }
}
