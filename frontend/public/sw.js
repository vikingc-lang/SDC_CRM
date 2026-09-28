/* Cirra service worker: installable app, offline reading, Web Push.
 *
 * - App shell and static assets are cached, so the app opens offline.
 * - Pages: network first; the last copy of each visited page is kept for offline use.
 * - API reads for the everyday screens (home, pipeline, accounts, contacts, deals, tasks, cases, leads, notifications) are
 *   network first with the last good answer kept on this device, so recently viewed records open offline.
 *   Writes are never cached here; the app queues them while offline (lib/offline.ts).
 * - Signing out sends {type: "clear"}, which deletes every cached API answer.
 * - Push: shows the notification and opens its link when tapped.
 */
const VERSION = "cirra-v1";
const SHELL = `${VERSION}-shell`;
const PAGES = `${VERSION}-pages`;
const API = `${VERSION}-api`;
const PRECACHE = ["/offline.html", "/icon.svg", "/icon-192.png", "/badge-96.png", "/manifest.webmanifest"];
const API_READS = /\/api\/v1\/(users\/me|ai\/briefing|alerts|dashboard|pipelines?|deals|accounts|contacts|tasks|activities|cases|leads|notifications|help)(\/|\?|$)/;
const API_NEVER = /\/api\/v1\/(auth|admin|export|public|t\/|calendar\/feed)/;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(request, cacheName, fallback) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const hit = await cache.match(request, { ignoreVary: true });
    if (hit) {
      const headers = new Headers(hit.headers);
      headers.set("X-Cirra-Offline", "1");
      return new Response(await hit.blob(), { status: hit.status, statusText: hit.statusText, headers });
    }
    if (fallback) return (await caches.match(fallback)) || Response.error();
    throw err;
  }
}

async function cacheFirst(request) {
  const hit = await caches.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) (await caches.open(SHELL)).put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith("/_next/static/") || PRECACHE.includes(url.pathname) || /\.(png|svg|woff2?)$/.test(url.pathname)) {
      event.respondWith(cacheFirst(request));
    } else if (request.mode === "navigate") {
      event.respondWith(networkFirst(request, PAGES, "/offline.html"));
    }
    return;
  }
  if (API_READS.test(url.pathname) && !API_NEVER.test(url.pathname)) {
    event.respondWith(networkFirst(request, API));
  }
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "clear") {
    event.waitUntil(Promise.all([caches.delete(API), caches.delete(PAGES)]));
  }
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: event.data ? event.data.text() : "Cirra" }; }
  event.waitUntil(self.registration.showNotification(data.title || "Cirra", {
    body: data.body || "",
    tag: data.tag,
    icon: "/icon-192.png",
    badge: "/badge-96.png",
    data: { url: data.url || "/notifications" },
    requireInteraction: data.kind === "sla",
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/notifications", self.location.origin).href;
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of all) {
      if (new URL(client.url).origin === self.location.origin && "focus" in client) {
        await client.focus();
        if ("navigate" in client) return client.navigate(target);
        return undefined;
      }
    }
    return self.clients.openWindow(target);
  })());
});
