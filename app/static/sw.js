// 왜올랐지 서비스 워커: 정적 파일은 캐시, 페이지는 네트워크 우선 (오프라인 시 안내 페이지)
const CACHE = 'waeollatji-v1';
const ASSETS = ['/offline', '/static/app.css', '/static/vendor/htmx.min.js', '/static/icons/icon-192.png', '/manifest.webmanifest'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res;
    })));
    return;
  }
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/offline')));
  }
});
// 푸시 알림 (Phase 2): 서버에서 보낸 payload를 표시
self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : { title: '왜올랐지', body: '새 알림' };
  e.waitUntil(self.registration.showNotification(data.title, { body: data.body, icon: '/static/icons/icon-192.png', data: data.url }));
});
self.addEventListener('notificationclick', e => { e.notification.close(); if (e.notification.data) e.waitUntil(clients.openWindow(e.notification.data)); });
