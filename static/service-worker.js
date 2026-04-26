self.addEventListener('install', (e) => {
  console.log('[Service Worker] Install');
});

self.addEventListener('fetch', (e) => {
  // This allows the app to fetch resources from your live Flask URL
  e.respondWith(fetch(e.request));
});