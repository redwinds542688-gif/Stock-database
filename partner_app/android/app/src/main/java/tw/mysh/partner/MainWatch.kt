package tw.mysh.partner

import android.content.ComponentName
import android.content.Context
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * 2026-11-15 使用者「主程式跟看盤版 同時開啟 看盤才不通知 而不是手機裡有二個app 還未開啟 看盤就不通知」:
 * 名揚四海開著時(在監控)手機上一定掛著它的常駐通知(MonitorService 的前景服務通知)。
 * 這裡只看「名揚四海的常駐通知在不在」→ 在 = 名揚四海開著,由它響,夥伴看盤不響不震;不在 = 夥伴看盤照常響。
 * 不讀、不存任何通知內容。要使用者在手機設定允許一次「通知存取權」;沒允許 → 一律當作名揚四海沒開(夥伴看盤照常響)。
 */
class MainWatch : NotificationListenerService() {
    companion object {
        @Volatile var mainRunning = false
        private val PKGS = setOf("tw.mysh.monitor", "tw.mysh.monitor.viewer")
        fun granted(ctx: Context): Boolean {
            val s = Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners") ?: return false
            return s.contains(ComponentName(ctx, MainWatch::class.java).flattenToString())
        }
    }
    private fun scan() { mainRunning = try { activeNotifications?.any { it.packageName in PKGS && it.isOngoing } == true } catch (e: Exception) { false } }
    override fun onListenerConnected() { scan() }
    override fun onListenerDisconnected() { mainRunning = false }
    override fun onNotificationPosted(sbn: StatusBarNotification?) { if (sbn?.packageName in PKGS) scan() }
    override fun onNotificationRemoved(sbn: StatusBarNotification?) { if (sbn?.packageName in PKGS) scan() }
}
