package tw.mysh.monitor

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId

/** 今日通知紀錄:存在 SharedPreferences(依日期一組,最多 300 則),網頁顯示區用 Android.todayLog() 讀 */
object NotifLog {
    private val TZ: ZoneId = ZoneId.of("Asia/Taipei")
    private const val PREF = "notiflog"
    private fun today() = LocalDate.now(TZ).toString()
    @Synchronized
    fun add(ctx: Context, title: String, body: String, kind: String) {
        val sp = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
        val key = today()
        val arr = try { JSONArray(sp.getString(key, "[]")) } catch (e: Exception) { JSONArray() }
        val t = LocalTime.now(TZ).withNano(0).toString().substring(0, 5)
        val item = JSONObject().put("t", t).put("title", title).put("body", body).put("kind", kind)
        val out = JSONArray().put(item)                       // 新的在最前面
        for (i in 0 until minOf(arr.length(), 299)) out.put(arr.get(i))
        // 只留最近 10 天
        val keep = LocalDate.now(TZ).minusDays(10).toString()
        val ed = sp.edit(); for (k in sp.all.keys) if (k < keep) ed.remove(k)
        ed.putString(key, out.toString()).apply()
    }
    fun today(ctx: Context): String = forDate(ctx, today())
    fun forDate(ctx: Context, d: String): String = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString(d, "[]") ?: "[]"
    /** 有紀錄的日期(JSON 陣列,舊到新) */
    fun days(ctx: Context): String { val a = JSONArray(); for (k in ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).all.keys.sorted()) a.put(k); return a.toString() }
}
