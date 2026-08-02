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
  for (const file of files) {
    await cache.put(
      new Request(`/shared/${Date.now()}-${encodeURIComponent(file.name)}`),
      new Response(file, { headers: { "X-Filename": file.name } }),
    );
  }
  // Land on the Import page, which uploads the parked files to the inbox.
  return Response.redirect("/import?shared=1", 303);
}
