/* 2026-11-15 夥伴看盤網址版(iPhone / iPad / 電腦「加入主畫面」/ 安裝)用的 Service Worker:只為了能裝成 App,不快取、不攔請求(資料一律即時讀雲端)。
   和名揚四海的 /sw.js 分開(範圍只有 /partner/)。 */
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
/* 2026-11-21 使用者「蘋果如果可以推播也需要」→「改」:雲端送來「空的」推播 → 問 ?ppush=last 拿這位夥伴自己的最新通知(進場 / 平倉 / 浮贏浮輸)。
   iPhone 規定每則推播都要顯示通知,問不到內容也顯示一則。 */
self.addEventListener('push', e => e.waitUntil((async () => {
  let items = [];
  try {
    const sub = await self.registration.pushManager.getSubscription();
    const r = await fetch('/?ppush=last', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({endpoint: sub ? sub.endpoint : ''}), cache: 'no-store'});
    const j = await r.json(); if (j && Array.isArray(j.items)) items = j.items;
  } catch (err) {}
  if (!items.length) items = [{title: '自動當沖 · 夥伴看盤', body: '有新的交易,點開查看', key: 'generic'}];
  for (const it of items) await self.registration.showNotification(it.title, {body: it.body || '', tag: it.key || it.title, icon: '/partner/icon-192.png', badge: '/partner/icon-192.png'});
})()));
self.addEventListener('notificationclick', e => { e.notification.close(); e.waitUntil((async () => {
  const cs = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
  for (const c of cs){ if ('focus' in c) return c.focus(); }
  return self.clients.openWindow('/partner/');
})()); });
