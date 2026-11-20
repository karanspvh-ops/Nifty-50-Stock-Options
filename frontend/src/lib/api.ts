/**
 * Where the backend is.
 *
 *  - On the trading PC (localhost) it is http://localhost:8000.
 *  - On the hosted team site the backend is reached through the Cloudflare
 *    relay on the same origin: /api/* and the live WebSockets are forwarded to
 *    the trading PC over the one connection its backend keeps open to
 *    Cloudflare (frontend/worker/index.js). The relay's status endpoint says
 *    whether that connection is up; it is re-checked every 10 s so the offline
 *    banner clears by itself when the backend starts.
 *
 * API and WS_BASE are live ES-module bindings: every importer sees the
 * current value at call time.
 */

const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

export let API = isLocal ? 'http://localhost:8000' : '';
export let WS_BASE = isLocal
  ? 'ws://localhost:8000'
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;
export let backendState: 'local' | 'relay' | 'offline' | 'checking' = isLocal ? 'local' : 'checking';

const listeners = new Set<() => void>();

export function onBackendChange(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export const wsUrl = (path: string) => `${WS_BASE}${path}`;

async function check() {
  let next: typeof backendState = 'offline';
  try {
    const r = await fetch('/api/_relay/status', { cache: 'no-store' });
    if (r.ok && (await r.json()).online) next = 'relay';
  } catch { /* relay unreachable: offline */ }
  if (next !== backendState) {
    backendState = next;
    listeners.forEach(fn => fn());
  }
}

export async function initApi() {
  if (isLocal) return;
  await check();
  setInterval(check, 10_000);
}
