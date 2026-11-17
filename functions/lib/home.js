// Serves the shop's website pages from home.html (owner's request, Nov 2026): the homepage at kantomisplay.cc/
// (functions/index.js) and its own pages (functions/contact.js, schedule.js, condition-guide.js, how-we-price.js, updates.js,
// feature-request.js, beta.js, new-arrivals.js), which home.html draws from the address. Not a route itself (no onRequest).
import { secure, pagesDevRedirect } from './security.js';

export async function serveHome(context) {
  const { request, env } = context;
  const next = async () => secure(await context.next());
  const moved = pagesDevRedirect(request);
  if (moved) return moved;
  const method = request.method.toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') return next();
  const url = new URL(request.url);
  // home.html (Pages serves it at /home); if it's missing, whatever Pages would have served
  const home = await env.ASSETS.fetch(new URL('/home', url), { method });
  if (!home.ok) return next();
  const headers = new Headers(home.headers);
  headers.set('Cache-Control', 'public, max-age=300');
  return secure(new Response(method === 'HEAD' ? null : home.body, { status: 200, headers }));
}
