// Security headers for the pages functions/ serves (Cloudflare Pages doesn't add _headers to a function's
// response): the same as the /* block in _headers, keep the two the same. Also sends Cloudflare's own address for
// the project (card-api.pages.dev, never public) and www.kantomisplay.cc on to kantomisplay.cc.
export const SECURITY_HEADERS = {
  'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' https: data: blob:; media-src 'self' blob:; connect-src 'self' https://raw.githubusercontent.com https://cdn.jsdelivr.net https://kms-staff.pages.dev https://challenges.cloudflare.com http://127.0.0.1:8788; frame-src https://challenges.cloudflare.com; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(self), microphone=(), geolocation=(), payment=(), usb=()',
};

// A redirect for Cloudflare's own address or www, else null
export function pagesDevRedirect(request) {
  const url = new URL(request.url);
  if (!/((^|\.)card-api\.pages\.dev|^www\.kantomisplay\.cc)$/i.test(url.hostname)) return null;
  return Response.redirect('https://kantomisplay.cc' + url.pathname + url.search, 301);
}

// The response with the security headers added
export function secure(response) {
  const out = new Response(response.body, response);
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) out.headers.set(name, value);
  return out;
}
