package tw.mysh.monitor

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager

/** 螢幕右上角的小綠點(閃爍)= 背景監控執行中;疊在所有 App 上面,連元大 App 裡也看得到 */
class OverlayDot(private val ctx: Context) {
    private var view: View? = null
    private var anim: ObjectAnimator? = null
    fun allowed(): Boolean = Settings.canDrawOverlays(ctx)
    fun enabled(): Boolean = ctx.getSharedPreferences("mysh", Context.MODE_PRIVATE).getBoolean("overlayDot", true)
    fun show() {
        if (view != null || !allowed() || !enabled()) return
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val d = ctx.resources.displayMetrics.density
        val v = View(ctx)
        v.background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(0xFF1DD75B.toInt()); setStroke((1 * d).toInt(), 0x80000000.toInt()) }
        val size = (12 * d).toInt()
        val type = if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        val lp = WindowManager.LayoutParams(size, size, type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT)
        lp.gravity = Gravity.TOP or Gravity.END
        lp.x = (6 * d).toInt(); lp.y = (30 * d).toInt()   // 狀態列下方,不蓋到電量
        try { wm.addView(v, lp); view = v } catch (e: Exception) { return }
        anim = ObjectAnimator.ofFloat(v, "alpha", 1f, 0.15f).apply { duration = 500; repeatMode = ValueAnimator.REVERSE; repeatCount = ValueAnimator.INFINITE; start() }
    }
    fun hide() {
        anim?.cancel(); anim = null
        view?.let { try { (ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(it) } catch (e: Exception) {} }
        view = null
    }
}
