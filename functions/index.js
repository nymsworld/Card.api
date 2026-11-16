// The bare address (owner's request, Nov 2026): kantomisplay.cc/ shows the shop's homepage (home.html), while
// pricing.kantomisplay.cc/ is the price checker's scanner. Only "/" comes here (Cloudflare Pages runs this file for
// that one path), so sticker links (/K/…, /G/…), vendor pages (/<name>) and every other file are served as before.
// Anything with a query string (?id=… card links, ?shop=gg, ?browse=1, ?scan) is the price page on either address.
export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  if (/^pricing\./i.test(url.hostname) || url.search || (method !== 'GET' && method !== 'HEAD')) return next();
  // home.html (Pages serves it at /home); a homepage that's missing falls back to the price page
  const home = await env.ASSETS.fetch(new URL('/home', url), { method });
  if (!home.ok) return next();
  const headers = new Headers(home.headers);
  headers.set('Cache-Control', 'public, max-age=300');
  return new Response(method === 'HEAD' ? null : home.body, { status: 200, headers });
}
