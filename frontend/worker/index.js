// SPVH Options Desk -- team site relay (Cloudflare Worker + Durable Object).
//
// The trading PC never accepts connections. Its backend opens ONE outgoing
// WebSocket to /relay (secret-checked) and keeps it open. Through it:
//   - live streams (market, trades) are pushed up once and fanned out here
//     to every team browser connected to /api/market/ws/<channel>
//   - every HTTP request a team browser makes to /api/* is forwarded down
//     to the backend, which answers it over the same socket
// Everything else is the built frontend (static assets).
//
// One Durable Object instance ("desk") holds the backend socket, the browser
// sockets and the requests waiting for answers.

const REQUEST_TIMEOUT_MS = 25_000;
const GET_CACHE_MS = 1_000;          // identical GETs from several viewers within 1 s share one backend call
const CHANNELS = ['market', 'trades'];
// Latest message per (channel, type) replayed to a browser the moment it connects
const REPLAY_TYPES = { market: ['market_update'], trades: ['snapshot', 'account', 'alerts'] };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/relay' || url.pathname.startsWith('/api/')) {
      const stub = env.RELAY.get(env.RELAY.idFromName('desk'));
      return stub.fetch(request);
    }
    return env.ASSETS.fetch(request);
  },
};

export class Relay {
  constructor(state, env) {
    this.env = env;
    this.backend = null;
    this.backendSince = null;
    this.pending = new Map();        // request id -> {resolve, timer}
    this.viewers = new Map(CHANNELS.map(c => [c, new Set()]));
    this.replay = new Map(CHANNELS.map(c => [c, new Map()]));
    this.getCache = new Map();       // path?query -> {at, promise}
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === '/relay') return this.connectBackend(request);
    if (url.pathname === '/api/_relay/status') {
      return json({ online: !!this.backend, since: this.backendSince, viewers: this.viewerCount() });
    }
    const m = url.pathname.match(/^\/api\/market\/ws\/(market|trades)$/);
    if (m && request.headers.get('Upgrade') === 'websocket') return this.connectViewer(m[1]);
    return this.forward(request, url);
  }

  viewerCount() {
    let n = 0;
    for (const s of this.viewers.values()) n += s.size;
    return n;
  }

  // ── The trading PC ────────────────────────────────────────────────────────
  connectBackend(request) {
    if (request.headers.get('Upgrade') !== 'websocket') return new Response('expected websocket', { status: 426 });
    if (!this.env.RELAY_SECRET || request.headers.get('X-Relay-Secret') !== this.env.RELAY_SECRET) {
      return new Response('forbidden', { status: 403 });
    }
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    if (this.backend) { try { this.backend.close(4000, 'replaced by a newer backend connection'); } catch {} }
    this.backend = server;
    this.backendSince = new Date().toISOString();

    server.addEventListener('message', ev => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.t === 'res') {
        const p = this.pending.get(msg.id);
        if (p) { clearTimeout(p.timer); this.pending.delete(msg.id); p.resolve(msg); }
      } else if (msg.t === 'stream' && this.viewers.has(msg.ch)) {
        this.broadcast(msg.ch, msg.data);
      } else if (msg.t === 'ping') {
        server.send(JSON.stringify({ t: 'pong' }));
      }
    });
    const gone = () => {
      if (this.backend !== server) return;
      this.backend = null;
      this.backendSince = null;
      for (const [id, p] of this.pending) {
        clearTimeout(p.timer);
        p.resolve({ status: 503, body: JSON.stringify({ detail: 'The trading system went offline' }), type: 'application/json' });
        this.pending.delete(id);
      }
      // Drop viewers so their pages show the system offline and reconnect later
      for (const set of this.viewers.values()) for (const ws of set) { try { ws.close(4001, 'backend offline'); } catch {} }
    };
    server.addEventListener('close', gone);
    server.addEventListener('error', gone);
    return new Response(null, { status: 101, webSocket: client });
  }

  // ── Team browsers: live streams ──────────────────────────────────────────
  connectViewer(channel) {
    if (!this.backend) return new Response('trading system offline', { status: 503 });
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    const set = this.viewers.get(channel);
    set.add(server);
    for (const data of this.replay.get(channel).values()) { try { server.send(data); } catch {} }
    const drop = () => set.delete(server);
    server.addEventListener('close', drop);
    server.addEventListener('error', drop);
    return new Response(null, { status: 101, webSocket: client });
  }

  broadcast(channel, data) {
    try {
      const type = JSON.parse(data).type;
      if (REPLAY_TYPES[channel].includes(type)) this.replay.get(channel).set(type, data);
    } catch {}
    for (const ws of this.viewers.get(channel)) {
      try { ws.send(data); } catch { this.viewers.get(channel).delete(ws); }
    }
  }

  // ── Team browsers: HTTP requests, answered by the backend ─────────────────
  async forward(request, url) {
    if (!this.backend) return json({ detail: 'The trading system is offline' }, 503);
    const key = url.pathname + url.search;
    if (request.method === 'GET') {
      const hit = this.getCache.get(key);
      if (hit && Date.now() - hit.at < GET_CACHE_MS) return toResponse(await hit.promise);
    }
    const body = ['GET', 'HEAD'].includes(request.method) ? null : await request.text();
    const id = crypto.randomUUID();
    const promise = new Promise(resolve => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({ status: 504, body: JSON.stringify({ detail: 'The trading system did not answer in time' }), type: 'application/json' });
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, timer });
    });
    try {
      this.backend.send(JSON.stringify({
        t: 'req', id, method: request.method, path: key, body,
        type: request.headers.get('Content-Type') || null,
        ip: request.headers.get('CF-Connecting-IP') || null,
        agent: (request.headers.get('User-Agent') || '').slice(0, 120),
      }));
    } catch {
      const p = this.pending.get(id);
      if (p) { clearTimeout(p.timer); this.pending.delete(id); }
      return json({ detail: 'The trading system is offline' }, 503);
    }
    if (request.method === 'GET') {
      this.getCache.set(key, { at: Date.now(), promise });
      if (this.getCache.size > 500) this.getCache.clear();
    }
    return toResponse(await promise);
  }
}

function toResponse(msg) {
  const headers = { 'Content-Type': msg.type || 'application/json', 'Cache-Control': 'no-store' };
  if (msg.disposition) headers['Content-Disposition'] = msg.disposition;
  if (msg.b64) {
    const bin = Uint8Array.from(atob(msg.body || ''), c => c.charCodeAt(0));
    return new Response(bin, { status: msg.status || 200, headers });
  }
  return new Response(msg.body ?? '', { status: msg.status || 200, headers });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
}
