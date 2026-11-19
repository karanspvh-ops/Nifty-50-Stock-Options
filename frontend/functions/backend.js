// GET /backend -> where the trading PC's backend is right now.
// The backend registers its Cloudflare tunnel address (which changes on every
// start) and heartbeats every 2 minutes; older than 5 minutes = offline.
const STALE_MS = 5 * 60 * 1000;

export async function onRequestGet({ env }) {
  const rec = await env.DESK_KV.get('backend', 'json');
  const online = !!(rec && rec.url && rec.online !== false && Date.now() - rec.at < STALE_MS);
  return Response.json(
    { url: rec?.url || null, online, at: rec?.at || null },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
