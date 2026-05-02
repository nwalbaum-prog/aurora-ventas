// Service Worker — Aurora Bakers
// Solo cachea assets estáticos; nunca cachea páginas autenticadas

const CACHE = 'aurora-v1';
const STATIC = [
  '/static/css/style.css',
  '/static/img/logo.png',
  '/static/img/icons/icon-192.png',
  '/static/img/icons/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Solo cachea assets estáticos (CSS, imágenes, fuentes CDN)
  const isStatic = url.pathname.startsWith('/static/') ||
                   url.hostname.includes('jsdelivr.net') ||
                   url.hostname.includes('cdn.');
  if (!isStatic) return; // páginas y APIs siempre van a la red
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
