/* 2026-11-01 使用者「這程式蘋果手機可以使用嗎」→「先做A」:iPhone「加入主畫面」的網頁版 App(看盤版)用的 Service Worker。
   雲端(Worker)有新訊號時送一個「空的」推播(不帶內容,不用加密);這裡收到後問雲端 ?push=last 拿這支手機看得到的最新訊號,顯示成通知。
   iPhone 規定每一則推播都要顯示通知,所以問不到內容時也顯示一則「有新的交易訊號」。點通知 → 打開 App。 */
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => e.waitUntil((async () => {
  let items = [];
  try {
    const sub = await self.registration.pushManager.getSubscription();
    const r = await fetch('/?push=last', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({endpoint: sub ? sub.endpoint : ''})});
    const j = await r.json(); if (j && Array.isArray(j.items)) items = j.items;
  } catch (err) {}
  if (!items.length) items = [{title: '名揚四海。自動當沖', body: '有新的交易訊號,點開查看', key: 'generic'}];
  for (const it of items) await self.registration.showNotification(it.title, {body: it.body || '', tag: it.key || it.title, icon: '/icon-192.png', badge: '/icon-192.png', data: {url: '/app'}});
})()));
self.addEventListener('notificationclick', e => { e.notification.close(); e.waitUntil((async () => {
  const cs = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
  for (const c of cs){ if ('focus' in c) return c.focus(); }
  return self.clients.openWindow('/app');
})()); });
