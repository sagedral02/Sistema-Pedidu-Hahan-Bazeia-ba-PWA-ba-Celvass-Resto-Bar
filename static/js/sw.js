/* Celvass service worker.
 *
 * Caching rules
 *  - Static assets (/static/, /media/): stale-while-revalidate.
 *  - Customer pages (/, /t/...): network-first, cached copy used only when offline.
 *  - Staff portals, login/logout, API and WebSocket: never cached or intercepted.
 */
const VERSION = 'v6';
const STATIC_CACHE = `celvass-static-${VERSION}`;
const PAGE_CACHE = `celvass-pages-${VERSION}`;
const OFFLINE_URL = '/offline/';

const PRECACHE = [
  OFFLINE_URL,
  '/static/css/style.css',
  '/static/vendor/bootstrap/bootstrap.min.css',
  '/static/vendor/bootstrap/bootstrap.bundle.min.js',
  '/static/vendor/fontawesome/css/all.min.css',
  '/static/vendor/fontawesome/webfonts/fa-solid-900.woff2',
  '/static/vendor/fontawesome/webfonts/fa-regular-400.woff2',
  '/static/vendor/fonts/plus-jakarta-sans.css',
  '/static/vendor/fonts/plus-jakarta-sans-latin.woff2',
  '/static/js/i18n.js',
  '/static/js/api.js',
  '/static/js/app.js',
  '/static/js/sound.js',
  '/static/js/websocket.js',
  '/static/js/pwa.js',
  '/static/js/customer.js',
  '/static/icons/default-food.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

const NEVER_CACHE_PREFIXES = ['/api/', '/ws/', '/admin/', '/admin-portal/', '/cashier/', '/kitchen/', '/reports/', '/login/', '/logout/', '/demo-login/', '/sw.js'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('celvass-') && k !== STATIC_CACHE && k !== PAGE_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (event) => {
  if (event.data === 'clear-pages') {
    event.waitUntil(caches.delete(PAGE_CACHE));
  }
});

function isCustomerPage(url) {
  return url.pathname === '/' || url.pathname.startsWith('/t/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (NEVER_CACHE_PREFIXES.some((p) => url.pathname.startsWith(p))) return;

  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/')) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  if (request.mode === 'navigate' && (isCustomerPage(url) || url.pathname === OFFLINE_URL)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(PAGE_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(async () => (await caches.match(request)) || (await caches.match(OFFLINE_URL)))
    );
  }
});
