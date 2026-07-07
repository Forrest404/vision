// FaceVision service worker: cache the app shell so the installed app opens
// instantly; everything live (API, media, streams) always goes to the network.
'use strict';

const CACHE = 'facevision-v1';
const SHELL = [
  '/',
  '/static/style.css?v=2',
  '/static/app.js?v=2',
  '/static/pages/live.js',
  '/static/pages/enroll.js',
  '/static/pages/identify.js',
  '/static/pages/search.js',
  '/static/pages/people.js',
  '/static/pages/objects.js',
  '/static/pages/settings.js',
  '/static/pages/phone.js',
  '/static/icons/icon-192.png',
  '/manifest.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  const isShell = e.request.method === 'GET' &&
    (url.pathname === '/' || url.pathname.startsWith('/static') || url.pathname === '/manifest.json');
  if (!isShell) return; // API/media/streams: straight to the network

  // network-first with cache fallback: fresh when the Mac is reachable,
  // instant shell when it briefly isn't
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return resp;
      })
      .catch(() => caches.match(e.request)));
});
