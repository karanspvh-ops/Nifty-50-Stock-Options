// POST /backend/register  (from the trading PC only)
// Header X-Register-Secret must match the REGISTER_SECRET set on this Pages
// project, so nobody else can point the team site at another server.
// Body: {"url": "https://<random>.trycloudflare.com", "online": true}
export async function onRequestPost({ request, env }) {
  if (!env.REGISTER_SECRET || request.headers.get('X-Register-Secret') !== env.REGISTER_SECRET) {
    return new Response('forbidden', { status: 403 });
  }
  const body = await request.json().catch(() => ({}));
  const url = typeof body.url === 'string' ? body.url : null;
  if (url && !/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(url)) {
    return new Response('bad url', { status: 400 });
  }
  await env.DESK_KV.put('backend', JSON.stringify({ url, online: body.online !== false, at: Date.now() }));
  return Response.json({ ok: true });
}
