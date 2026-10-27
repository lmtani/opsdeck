// Service worker for the installed app. It caches nothing: every view shows live data, so a
// stale copy would only mislead. It answers just one case, a page load while the backend is
// down, with a page that says so instead of the browser's error screen.

const OFFLINE_PAGE = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpsDeck · offline</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #070b14; color: #dbe4f5; font: 15px/1.5 system-ui, sans-serif; }
    main { text-align: center; padding: 24px; }
    h1 { color: #41d6e3; font-size: 20px; letter-spacing: 0.08em; }
    p { color: #7d8cab; }
    button { margin-top: 12px; padding: 8px 18px; border: 1px solid #2a3a5e; border-radius: 6px; background: #131b2e; color: #dbe4f5; font: inherit; cursor: pointer; }
  </style>
</head>
<body>
  <main>
    <h1>▣ OPSDECK</h1>
    <p>The OpsDeck server is not responding. Start it with <code>uv run main.py</code> and try again.</p>
    <button type="button" onclick="location.reload()">Retry</button>
  </main>
</body>
</html>`;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return; // API calls and assets go straight to the network
  event.respondWith(
    fetch(event.request).catch(
      () =>
        new Response(OFFLINE_PAGE, {
          status: 503,
          headers: { "Content-Type": "text/html; charset=utf-8" },
        })
    )
  );
});
