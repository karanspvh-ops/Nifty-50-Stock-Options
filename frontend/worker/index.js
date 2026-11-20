// SPVH Options Desk -- team site (Cloudflare Worker + Durable Object).
//
// Always on. The trading PC's backend keeps ONE outgoing WebSocket to /relay
// (secret-checked). Over it the backend:
//   - pushes dashboard data whenever it changes ({t:'data', path, body})
//   - pushes the live market / trades streams ({t:'stream', ch, data})
//   - answers HTTP requests forwarded from team browsers ({t:'req'} -> {t:'res'})
//
// The Durable Object keeps the latest copy of everything (memory, plus SQLite
// storage flushed at most once a minute per key), so the site works at any
// hour: with the backend off, browsers get the saved copy and the header
// shows the feed as disconnected; only actions that need the trading PC
// (exits, go live, settings) answer "unavailable".
//
// Cost: browsers make ONE request per page load (the /api/desk/ws socket);
// dashboard data reaches them as pushes over it (outbound messages aren't
// billed) and their polling is answered from a local cache, not the network.

import { DurableObject } from 'cloudflare:workers';

// Bump to start a fresh instance (a new deploy doesn't restart one held open by live sockets)
const DESK_INSTANCE = 'desk-v2';
const REQUEST_TIMEOUT_MS = 25_000;
const GET_CACHE_MS = 1_000;
const CHUNK = 400_000;                 // characters per stored row / per pushed message part
const FLUSH_MS = 60_000;               // persist changed keys at most once a minute
const STREAMS = ['market', 'trades'];
const REPLAY_TYPES = { market: ['market_update'], trades: ['snapshot', 'account', 'alerts'] };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/relay' || url.pathname.startsWith('/api/')) {
      return env.RELAY.get(env.RELAY.idFromName(DESK_INSTANCE)).fetch(request);
    }
    return env.ASSETS.fetch(request);
  },
};

export class Relay extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec('CREATE TABLE IF NOT EXISTS kv (k TEXT NOT NULL, part INTEGER NOT NULL, v TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (k, part))');
    this.backend = null;               // non-hibernating socket: keeps this object awake while the PC is connected
    this.backendSince = null;
    this.pending = new Map();          // request id -> {resolve, timer}
    this.parts = new Map();            // chunk reassembly: key -> {parts: [], n}
    this.mem = null;                   // key -> {v, at}; loaded lazily from storage
    this.dirty = new Set();
    this.getCache = new Map();
  }

  // ── Storage ──────────────────────────────────────────────────────────────
  load() {
    if (this.mem) return;
    this.mem = new Map();
    const rows = this.sql.exec('SELECT k, part, v, at FROM kv ORDER BY k, part').toArray();
    for (const r of rows) {
      const cur = this.mem.get(r.k);
      if (cur && r.part > 0) cur.v += r.v; else this.mem.set(r.k, { v: r.v, at: r.at });
    }
  }

  set(k, v) {
    this.load();
    this.mem.set(k, { v, at: Date.now() });
    this.dirty.add(k);
    this.ctx.storage.getAlarm().then(a => { if (a == null) this.ctx.storage.setAlarm(Date.now() + FLUSH_MS); });
  }

  get(k) { this.load(); return this.mem.get(k) || null; }

  flush() {
    for (const k of this.dirty) {
      const e = this.mem.get(k);
      if (!e) continue;
      this.sql.exec('DELETE FROM kv WHERE k = ?', k);
      const n = Math.max(1, Math.ceil(e.v.length / CHUNK));
      for (let p = 0; p < n; p++) {
        this.sql.exec('INSERT INTO kv (k, part, v, at) VALUES (?, ?, ?, ?)', k, p, e.v.slice(p * CHUNK, (p + 1) * CHUNK), e.at);
      }
    }
    this.dirty.clear();
  }

  async alarm() { this.flush(); }

  // ── Routing ──────────────────────────────────────────────────────────────
  async fetch(request) {
    const url = new URL(request.url);
    const upgrade = request.headers.get('Upgrade') === 'websocket';
    if (url.pathname === '/relay') return this.connectBackend(request);
    if (url.pathname === '/api/_relay/status') return json(this.deskState());
    if (upgrade && url.pathname === '/api/desk/ws') return this.connectViewer('data');
    const m = url.pathname.match(/^\/api\/market\/ws\/(market|trades)$/);
    if (upgrade && m) return this.connectViewer(m[1]);
    return this.forward(request, url);
  }

  deskState() {
    this.load();
    const last = this.get('meta:last_data_at');
    return { type: 'desk', online: !!this.backend, since: this.backendSince, saved_at: last ? Number(last.v) : null };
  }

  // ── The trading PC ───────────────────────────────────────────────────────
  connectBackend(request) {
    if (request.headers.get('Upgrade') !== 'websocket') return new Response('expected websocket', { status: 426 });
    if (!this.env.RELAY_SECRET || request.headers.get('X-Relay-Secret') !== this.env.RELAY_SECRET) {
      return new Response('forbidden', { status: 403 });
    }
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    if (this.backend) { try { this.backend.close(4000, 'replaced by a newer backend connection'); } catch {} }
    this.backend = server;
    this.backendSince = Date.now();
    this.load();
    this.broadcastData(JSON.stringify(this.deskState()));

    server.addEventListener('message', ev => this.onBackendMessage(ev.data));
    const gone = () => {
      if (this.backend !== server) return;
      this.backend = null;
      this.backendSince = null;
      for (const [id, p] of this.pending) {
        clearTimeout(p.timer);
        p.resolve(offlineRes());
        this.pending.delete(id);
      }
      this.flush();
      this.broadcastData(JSON.stringify(this.deskState()));
    };
    server.addEventListener('close', gone);
    server.addEventListener('error', gone);
    return new Response(null, { status: 101, webSocket: client });
  }

  onBackendMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    if (msg.parts > 1) {                       // reassemble chunked messages
      const key = `${msg.t}:${msg.id || msg.path}`;
      const acc = this.parts.get(key) || { parts: [], got: 0 };
      if (acc.parts[msg.part] == null) { acc.parts[msg.part] = msg.body; acc.got++; }
      this.parts.set(key, acc);
      if (acc.got < msg.parts) return;
      this.parts.delete(key);
      msg = { ...msg, body: acc.parts.join(''), parts: 1, part: 0 };
    }
    if (msg.t === 'res') {
      const p = this.pending.get(msg.id);
      if (p) { clearTimeout(p.timer); this.pending.delete(msg.id); p.resolve(msg); }
    } else if (msg.t === 'data') {
      this.set('data:' + msg.path, msg.body);
      this.set('meta:last_data_at', String(Date.now()));
      this.sendData(msg.path, msg.body);
    } else if (msg.t === 'stream' && STREAMS.includes(msg.ch)) {
      try {
        const type = JSON.parse(msg.data).type;
        if (REPLAY_TYPES[msg.ch].includes(type)) this.set(`stream:${msg.ch}:${type}`, msg.data);
      } catch {}
      for (const ws of this.ctx.getWebSockets(msg.ch)) { try { ws.send(msg.data); } catch {} }
    }
  }

  // ── Team browsers (hibernatable sockets: an idle site costs nothing) ──────
  connectViewer(channel) {
    const [client, server] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(server, [channel]);
    this.load();
    if (channel === 'data') {
      server.send(JSON.stringify(this.deskState()));
      for (const [k, e] of this.mem) {
        if (k.startsWith('data:')) this.sendTo(server, k.slice(5), e.v, e.at);
      }
    } else {
      for (const type of REPLAY_TYPES[channel]) {
        const e = this.get(`stream:${channel}:${type}`);
        if (e) { try { server.send(e.v); } catch {} }
      }
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  webSocketMessage() {}                         // browsers only listen
  webSocketClose(ws) { try { ws.close(); } catch {} }

  sendTo(ws, path, body, at) {
    const n = Math.max(1, Math.ceil(body.length / CHUNK));
    for (let p = 0; p < n; p++) {
      try { ws.send(JSON.stringify({ type: 'data', path, at, part: p, parts: n, body: body.slice(p * CHUNK, (p + 1) * CHUNK) })); } catch {}
    }
  }

  sendData(path, body) {
    const at = Date.now();
    for (const ws of this.ctx.getWebSockets('data')) this.sendTo(ws, path, body, at);
  }

  broadcastData(text) {
    for (const ws of this.ctx.getWebSockets('data')) { try { ws.send(text); } catch {} }
  }

  // ── HTTP from browsers: forwarded when online, saved copy when not ───────
  async forward(request, url) {
    const key = url.pathname + url.search;
    const isGet = request.method === 'GET';
    if (!this.backend) {
      if (isGet) {
        const e = this.get('data:' + key) || this.get('get:' + key);
        if (e) return new Response(e.v, { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Desk-Saved-At': String(e.at) } });
        return json({ detail: 'Not saved yet. Available once the trading system is back online.' }, 503);
      }
      return json({ detail: 'Unavailable right now: the trading system on the office PC is offline.' }, 503);
    }
    if (isGet) {
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
      return toResponse(offlineRes());
    }
    if (isGet) {
      this.getCache.set(key, { at: Date.now(), promise });
      if (this.getCache.size > 300) this.getCache.clear();
    }
    const res = await promise;
    if (isGet && res.status === 200 && !res.b64 && (res.type || '').includes('json')) this.set('get:' + key, res.body);
    return toResponse(res);
  }
}

function offlineRes() {
  return { status: 503, type: 'application/json', body: JSON.stringify({ detail: 'Unavailable right now: the trading system on the office PC is offline.' }) };
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
