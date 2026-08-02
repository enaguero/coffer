/* Coffer service worker: makes the app installable and receives files from
 * the OS share sheet. Shared statements are parked in the Cache API; the
 * Import page (which holds the authenticated session) picks them up and
 * pushes them to the inbox API. No fetch interception beyond /share-target —
 * the app itself stays fully network-served. */

const SHARE_CACHE = "coffer-shared-files";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "POST" && url.pathname === "/share-target") {
    event.respondWith(handleShare(event.request));
  }
});

async function handleShare(request) {
  const form = await request.formData();
  const files = form.getAll("statements").filter((f) => typeof f === "object");
  const cache = await caches.open(SHARE_CACHE);
  let i = 0;
  for (const file of files) {
    i += 1;
    await cache.put(
      // Index guards same-millisecond, same-name collisions within one share.
      new Request(`/shared/${Date.now()}-${i}-${encodeURIComponent(file.name)}`),
      // Header values are Latin-1 only — encode so "John’s statement.pdf" and
      // non-Latin scripts survive instead of throwing and killing the share.
      new Response(file, { headers: { "X-Filename": encodeURIComponent(file.name) } }),
    );
  }
  // Land on the Import page, which uploads the parked files to the inbox.
  return Response.redirect("/import", 303);
}
