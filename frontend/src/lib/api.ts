/**
 * Where the backend is, and how the hosted team site gets its data.
 *
 *  - On the trading PC (localhost): http://localhost:8000, unchanged.
 *  - On the hosted team site (Cloudflare): one WebSocket, /api/desk/ws,
 *    receives every dashboard dataset the backend pushes (and the saved copy
 *    when the backend is off). The panels keep their normal polling code, but
 *    a GET for a pushed path is answered from that local copy, so a page
 *    that is left open makes no further network requests. Anything not
 *    pushed (a specific report, a date) goes to Cloudflare, which forwards it
 *    to the trading PC or answers from its saved copy.
 *
 * API and WS_BASE are live ES-module bindings.
 */

export const isHosted = !['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

export let API = isHosted ? '' : 'http://localhost:8000';
export let WS_BASE = isHosted
  ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  : 'ws://localhost:8000';

export const wsUrl = (path: string) => `${WS_BASE}${path}`;

// ── Desk state (hosted only) ───────────────────────────────────────────────
export interface DeskState { online: boolean; since: number | null; savedAt: number | null; connected: boolean }
export let desk: DeskState = { online: !isHosted, since: null, savedAt: null, connected: !isHosted };

const listeners = new Set<() => void>();
export function onDeskChange(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
function setDesk(patch: Partial<DeskState>) {
  desk = { ...desk, ...patch };
  listeners.forEach(fn => fn());
}

const cache = new Map<string, { body: string; at: number }>();
const parts = new Map<string, string[]>();

function connectDesk() {
  const ws = new WebSocket(wsUrl('/api/desk/ws'));
  ws.onopen = () => setDesk({ connected: true });
  ws.onmessage = ev => {
    let msg: any;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === 'desk') {
      setDesk({ online: !!msg.online, since: msg.since ?? null, savedAt: msg.saved_at ?? desk.savedAt });
    } else if (msg.type === 'data') {
      let body: string = msg.body;
      if (msg.parts > 1) {
        const acc = parts.get(msg.path) || new Array(msg.parts);
        acc[msg.part] = msg.body;
        parts.set(msg.path, acc);
        if (acc.filter(x => x != null).length < msg.parts) return;
        parts.delete(msg.path);
        body = acc.join('');
      }
      cache.set(msg.path, { body, at: msg.at });
      if (desk.online) setDesk({ savedAt: msg.at });
    }
  };
  ws.onclose = () => { setDesk({ connected: false }); setTimeout(connectDesk, 3000); };
}

function installFetchCache() {
  const realFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method || 'GET').toUpperCase();
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname + input.search : '';
    if (method === 'GET' && url.startsWith('/api/')) {
      const hit = cache.get(url);
      if (hit) {
        return Promise.resolve(new Response(hit.body, {
          status: 200, headers: { 'Content-Type': 'application/json', 'X-Desk-Saved-At': String(hit.at) },
        }));
      }
    }
    return realFetch(input as RequestInfo, init);
  };
}

export function initApi() {
  if (!isHosted) return;
  installFetchCache();
  connectDesk();
}
