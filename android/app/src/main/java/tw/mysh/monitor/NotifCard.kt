package tw.mysh.monitor

import android.app.Notification
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.util.Log
import android.widget.RemoteViews
import org.json.JSONObject

/** 2026-10-24 通知列新排版:兩列、三欄,「·」上下對齊,字放到最大,依種類畫底色(加碼黃網紋、做空藍斜網、做多紫方網、停利淡藍、停損淡紅)。
 *  做法:把整塊內容畫成一張圖,用自訂通知版面顯示(Android 12 起系統會留上方的 App 名稱 / 時間,這張圖放在下面)。 */
object NotifCard {
    fun apply(ctx: Context, nb: Notification.Builder, lay: String?) {
        if (lay.isNullOrBlank()) return
        try {
            val j = JSONObject(lay)
            val t = j.getJSONArray("t"); val b = j.getJSONArray("b")
            val top = List(3) { t.optString(it, "") }; val bot = List(3) { b.optString(it, "") }
            val c = try { Color.parseColor(j.optString("c", "#ffffff")) } catch (e: Exception) { Color.WHITE }
            val bg = j.optString("bg", "")
            val dm = ctx.resources.displayMetrics
            val w = (minOf(dm.widthPixels, dm.heightPixels) - 32 * dm.density).toInt().coerceAtLeast(300)
            val big = draw(top, bot, c, bg, w, dm.density)
            val rvBig = RemoteViews(ctx.packageName, R.layout.notif_card_big).apply { setImageViewBitmap(R.id.card, big) }
            val rvSmall = RemoteViews(ctx.packageName, R.layout.notif_card_small).apply { setImageViewBitmap(R.id.card, big) }
            nb.setStyle(Notification.DecoratedCustomViewStyle())
                .setCustomContentView(rvSmall).setCustomBigContentView(rvBig).setCustomHeadsUpContentView(rvBig)
        } catch (e: Exception) { Log.w("mysh", "NotifCard 畫圖失敗,用文字通知: ${e.message}") }
    }

    private fun draw(top: List<String>, bot: List<String>, color: Int, bg: String, w: Int, d: Float): Bitmap {
        val pad = 10 * d; val gap = 5 * d; val rowGap = 4 * d
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.DEFAULT_BOLD }
        // 字放到最大:先用 1px 量寬度,再等比放大到剛好塞滿寬度(最大 30dp)
        fun widthAt(size: Float): Float {
            p.textSize = size
            val cols = (0 until 3).map { maxOf(p.measureText(top[it]), p.measureText(bot[it])) }
            return cols.sum() + 2 * p.measureText("·") + 4 * gap
        }
        val avail = w - 2 * pad
        var size = 30 * d
        val w0 = widthAt(size); if (w0 > avail) size *= avail / w0
        size = size.coerceAtLeast(10 * d)
        p.textSize = size
        val fm = p.fontMetrics; val lineH = fm.descent - fm.ascent
        val h = (2 * pad + 2 * lineH + rowGap).toInt()
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val cv = Canvas(bmp)
        // 底色 + 網紋(圓角)
        val r = RectF(0f, 0f, w.toFloat(), h.toFloat()); val rad = 14 * d
        val clip = Path().apply { addRoundRect(r, rad, rad, Path.Direction.CW) }
        cv.save(); cv.clipPath(clip)
        val (fill, line, kind) = when (bg) {
            "add" -> Triple(0xFF3A3626.toInt(), 0x3DFFD746, 2)
            "short" -> Triple(0xFF25323F.toInt(), 0x2478BEFF, 2)
            "long" -> Triple(0xFF3B2F55.toInt(), 0x4DBE96FF, 1)
            "profit" -> Triple(0xFF21384A.toInt(), 0, 0)
            "loss" -> Triple(0xFF4A2626.toInt(), 0, 0)
            else -> Triple(0xFF2D2F36.toInt(), 0, 0)
        }
        cv.drawColor(fill)
        if (kind > 0) {
            val lp = Paint(Paint.ANTI_ALIAS_FLAG).apply { this.color = line; strokeWidth = d }
            val step = (if (kind == 2) 7 else 6) * d
            if (kind == 2) { var x = -h.toFloat(); while (x < w + h) { cv.drawLine(x, 0f, x + h, h.toFloat(), lp); cv.drawLine(x + h, 0f, x, h.toFloat(), lp); x += step } }
            else { var x = 0f; while (x < w) { cv.drawLine(x, 0f, x, h.toFloat(), lp); x += step }; var y = 0f; while (y < h) { cv.drawLine(0f, y, w.toFloat(), y, lp); y += step } }
        }
        cv.restore()
        // 三欄:每欄寬 = 上下兩列較寬者;欄與欄之間平均分配剩下的空間,讓「·」上下對齊、左右貼齊
        val cols = (0 until 3).map { maxOf(p.measureText(top[it]), p.measureText(bot[it])) }
        val dotW = p.measureText("·")
        val spare = ((avail - cols.sum() - 2 * dotW) / 4f).coerceAtLeast(0f)
        // 欄位置:第 1 欄 x0 · 點 x1 · 第 2 欄 x2 · 點 x3 · 第 3 欄 x4(貼右邊)
        val x0 = pad; val x1 = x0 + cols[0] + spare; val x2 = x1 + dotW + spare; val x3 = x2 + cols[1] + spare
        val x4 = w - pad - cols[2]
        fun row(cells: List<String>, y: Float, c: Int) {
            p.color = c; cv.drawText(cells[0], x0, y, p); cv.drawText(cells[1], x2, y, p); cv.drawText(cells[2], x4, y, p)
            p.color = 0xFFCCCCCC.toInt(); cv.drawText("·", x1, y, p); cv.drawText("·", x3, y, p)
        }
        row(top, pad - fm.ascent, color)
        row(bot, pad + lineH + rowGap - fm.ascent, Color.WHITE)
        return bmp
    }
}
