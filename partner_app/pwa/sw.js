/* 2026-11-15 夥伴看盤網址版(iPhone / iPad / 電腦「加入主畫面」/ 安裝)用的 Service Worker:只為了能裝成 App,不快取、不攔請求(資料一律即時讀雲端)。
   和名揚四海的 /sw.js 分開(範圍只有 /partner/)。 */
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick', e => { e.notification.close(); e.waitUntil((async () => {
  const cs = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
  for (const c of cs){ if ('focus' in c) return c.focus(); }
  return self.clients.openWindow('/partner/');
})()); });
