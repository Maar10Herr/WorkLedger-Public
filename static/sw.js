const CACHE = "workledger-static-v1";
const STATIC_ASSETS = [
  "/static/css/workledger.css",
  "/static/js/workledger-ui.js",
  "/static/js/drafts.js",
  "/static/js/pwa.js",
  "/static/manifest.webmanifest",
  "/static/icons/workledger.svg",
  "/static/offline.html"
];
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
  )));
  self.clients.claim();
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
