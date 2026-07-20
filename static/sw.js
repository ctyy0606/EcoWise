/**
 * EcoWise 宿舍助理 - Service Worker
 * ================================
 * 支持浏览器推送通知 (Web Push) 和 PWA 离线缓存
 */

const CACHE_NAME = 'ecowise-v1';
const CACHE_URLS = [
    '/',
    '/static/manifest.json'
];

// 安装时缓存核心资源
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(CACHE_URLS))
            .then(() => self.skipWaiting())
    );
});

// 激活时清理旧缓存
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            );
        }).then(() => self.clients.claim())
    );
});

// 接收推送消息并显示通知
self.addEventListener('push', event => {
    if (!event.data) return;

    let payload;
    try {
        payload = event.data.json();
    } catch (e) {
        payload = { title: 'EcoWise 提醒', body: event.data.text() };
    }

    const title = payload.title || 'EcoWise 宿舍助理';
    const options = {
        body: payload.body || '您有一条新消息',
        icon: payload.icon || '/static/icon-192.png',
        badge: payload.badge || '/static/icon-96.png',
        tag: payload.tag || 'ecowise-default',
        requireInteraction: payload.requireInteraction || false,
        data: payload.data || {},
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// 点击通知时打开网页
self.addEventListener('notificationclick', event => {
    event.notification.close();

    const urlToOpen = event.notification.data?.url || '/';

    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(clientList => {
                // 如果已有窗口打开，聚焦它
                for (const client of clientList) {
                    if (client.url === urlToOpen && 'focus' in client) {
                        return client.focus();
                    }
                }
                // 否则打开新窗口
                if (self.clients.openWindow) {
                    return self.clients.openWindow(urlToOpen);
                }
            })
    );
});
