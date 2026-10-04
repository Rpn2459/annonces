// Service worker : réseau d'abord (toujours la dernière version en ligne), cache en secours hors connexion.
const CACHE = 'gh-annonces-v4';
const CORE = ['./', './index.html', './generateur-les-vendus-guy-hoquet.html', './generateur-annonce-guy-hoquet.html',
  './manifest.webmanifest', './logo-guy-hoquet.png', './icon-192.png', './icon-512.png', './avatar-emi.jpg'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== CACHE).map(n => caches.delete(n)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && (e.request.url.startsWith(self.location.origin) || e.request.url.includes('fonts.g'))) {
      const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy));
    }
    return r;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
