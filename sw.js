// Service worker for the installable website (registered by index.html's "Installable website" block).
// Keeps a copy of the page so the installed app opens with no signal. Prices aren't kept here: the
// page always downloads them fresh.
//   - Opening the page (any address, sticker links too): the network first, the saved page offline
//   - The logos and icons: the saved copy, else the network
//   - Everything else (prices, pictures, other sites): left alone
// Change VERSION to make phones drop the old copy.
var VERSION = 'kms-site-1';
var FILES = ['./', 'logo.jpg', 'gg-logo.png', 'icon-192.png', 'icon-512.png', 'icon-180.png', 'manifest.webmanifest'];

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(VERSION).then(function (cache) { return cache.addAll(FILES); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) { return n.indexOf('kms-site-') === 0 && n !== VERSION; }).map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(function (response) {
      if (response.ok && url.pathname === new URL('./', self.location).pathname) {
        var copy = response.clone();
        caches.open(VERSION).then(function (cache) { cache.put('./', copy); });
      }
      return response;
    }).catch(function () {
      return caches.match('./');
    }));
    return;
  }
  var file = url.pathname.split('/').pop();
  if (FILES.indexOf(file) === -1) return;
  event.respondWith(caches.match(request, { ignoreSearch: true }).then(function (saved) { return saved || fetch(request); }));
});
