package tw.mysh.monitor

import android.content.Context
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
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
        h.post { vibratePattern(ctx, pattern); soundFor(ctx, lv >= 2, ms) }
    }
    /** 2026-10-31 賺錢慶祝 1~5:輕快的短震(像拍手)+ 一般提示音,級數越高拍越多下、響越久;不用鬧鐘聲 */
    fun cheer(ctx: Context, lv: Int) {
        val n = 2 + lv * 2
        val pattern = LongArray(n * 2 + 3).also { p ->
            p[0] = 0L
            for (i in 0 until n) { p[1 + i * 2] = 70L; p[2 + i * 2] = 90L }
            p[n * 2 + 1] = 120L; p[n * 2 + 2] = 350L
        }
        h.post { vibratePattern(ctx, pattern); soundFor(ctx, false, 1500L + lv * 700L) }
    }
    private fun vibratePattern(ctx: Context, pattern: LongArray) {
        try {
            val vib: Vibrator = if (Build.VERSION.SDK_INT >= 31) (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
                                else @Suppress("DEPRECATION") ctx.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            if (Build.VERSION.SDK_INT >= 26) @Suppress("DEPRECATION") vib.vibrate(VibrationEffect.createWaveform(pattern, -1), attrs)
            else @Suppress("DEPRECATION") vib.vibrate(pattern, -1)
        } catch (e: Exception) { Log.e("mysh", "vibrate failed", e) }
    }
    private fun soundFor(ctx: Context, alarm: Boolean, ms: Long) {
        try {
            try { rt?.stop() } catch (e: Exception) {}
            val uri = RingtoneManager.getDefaultUri(if (alarm) RingtoneManager.TYPE_ALARM else RingtoneManager.TYPE_NOTIFICATION)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE) ?: return
            val r = RingtoneManager.getRingtone(ctx, uri) ?: return
            r.audioAttributes = attrs
            if (Build.VERSION.SDK_INT >= 28) r.isLooping = true
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
            val uri = RingtoneManager.getDefaultUri(if (danger) RingtoneManager.TYPE_ALARM else RingtoneManager.TYPE_NOTIFICATION)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE) ?: return
            val r = RingtoneManager.getRingtone(ctx, uri) ?: return
            r.audioAttributes = attrs
            if (Build.VERSION.SDK_INT >= 28) r.isLooping = danger
            r.play(); rt = r
            h.postDelayed({ try { if (rt === r) { r.stop(); rt = null } } catch (e: Exception) {} }, if (danger) 8000L else 3000L)
        } catch (e: Exception) { Log.e("mysh", "sound failed", e) }
    }
}
