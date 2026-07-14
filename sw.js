// JHL Holdings — Service Worker
// Strategy: Cache-first for app shell, Network-first for /api/signals
// On network fail: serve last cached signal data with staleness banner

const CACHE_NAME = 'jhl-v2';
const SHELL_URLS = ['/', '/index.html', '/index-2.html', '/manifest.webmanifest'];
const API_URL = '/api/signals';
const API_CACHE = 'jhl-signals-v2';

// Install — cache the app shell immediately
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_URLS)).catch(() => {})
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME && k !== API_CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — routing logic
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API signals — network first, fall back to cache
  if (url.pathname === '/api/signals' || url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request.clone())
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(API_CACHE).then(cache => {
              cache.put(event.request, clone);
            });
          }
          return response;
        })
        .catch(() =>
          caches.match(event.request, { cacheName: API_CACHE }).then(cached => {
            if (cached) return cached;
            // Return empty signal state so feed doesn't crash
            return new Response(
              JSON.stringify({
                offline: true,
                last_scan: null,
                signals: [],
                fg: { score: null, label: 'Offline' },
                regime_map: {}
              }),
              { headers: { 'Content-Type': 'application/json' } }
            );
          })
        )
    );
    return;
  }

  // App shell — cache first, fall back to network
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
