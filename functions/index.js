// The bare address (owner's request, Nov 2026): kantomisplay.cc/ shows the shop's homepage (home.html), while
// pricing.kantomisplay.cc/ is the price checker's scanner. Only "/" comes here (Cloudflare Pages runs this file for
// that one path), so sticker links (/K/…, /G/…), vendor pages (/<name>) and every other file are served as before.
// Anything with a query string (?id=… card links, ?shop=gg, ?browse=1, ?scan) is the price page on either address.
import { serveHome } from './lib/home.js';
import { secure, pagesDevRedirect } from './lib/security.js';

export async function onRequest(context) {
  const url = new URL(context.request.url);
  const moved = pagesDevRedirect(context.request);
  if (moved) return moved;
  if (/^pricing\./i.test(url.hostname) || url.search) return secure(await context.next());
  return serveHome(context);
}
