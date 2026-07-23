/**
 * EcoWise 宿舍助理 - Service Worker
 * ================================
 * 支持浏览器推送通知 (Web Push) 和 PWA 离线缓存
 */

const CACHE_NAME = 'ecowise-v4';
const CACHE_URLS = [
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-96.png'
];

// 安装时缓存核心静态资源（不缓存首页！）
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

// fetch事件：对HTML页面使用网络优先策略
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    // 对HTML页面使用网络优先（确保总是获取最新版本）
    if (event.request.mode === 'navigate' || 
        event.request.headers.get('accept')?.includes('text/html')) {
        event.respondWith(
            fetch(event.request)
                .catch(() => caches.match(event.request))
        );
    }
    // 对静态资源使用缓存优先
    else if (event.request.destination !== '') {
        event.respondWith(
            caches.match(event.request)
                .then(response => response || fetch(event.request))
                .catch(() => new Response('', { status: 408 }))
        );
    }
    // 其他请求（如 manifest、font 等）直接走网络
    else {
        // 不拦截，让浏览器正常处理
        return;
    }
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