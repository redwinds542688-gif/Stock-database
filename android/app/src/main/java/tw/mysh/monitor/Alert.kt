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
