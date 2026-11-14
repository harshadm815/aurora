// Keeps the app's own files so it opens quickly and can be installed. The API is never cached.
const CACHE = "aurora-app-1";
const FILES = ["/", "/app.js", "/app.css", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;   // always from the PC
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
