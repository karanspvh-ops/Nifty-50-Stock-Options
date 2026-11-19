/**
 * Where the backend is.
 *
 *  - On the trading PC (localhost) it is http://localhost:8000.
 *  - On the hosted team site (Cloudflare Pages) the backend is reached through
 *    the Cloudflare tunnel the trading PC opens when it starts. The tunnel's
 *    address changes on every backend start, so the site asks its own
 *    /backend endpoint (a Pages Function the backend registers with) and
 *    re-checks it every 20 s, so a backend restart is picked up without a
 *    page reload.
 *
 * API and WS_BASE are live ES-module bindings: every importer sees the
 * current value at call time.
 */

export let API = 'http://localhost:8000';
export let WS_BASE = 'ws://localhost:8000';
export let backendState: 'local' | 'tunnel' | 'offline' | 'checking' = 'local';

const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
const listeners = new Set<() => void>();

export function onBackendChange(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export const wsUrl = (path: string) => `${WS_BASE}${path}`;

async function discover() {
  let next: typeof backendState = 'offline';
  let url = '';
  try {
    const r = await fetch('/backend', { cache: 'no-store' });
    if (r.ok) {
      const d = await r.json();
      if (d.url && d.online) { url = String(d.url).replace(/\/$/, ''); next = 'tunnel'; }
    }
  } catch { /* site reachable but backend registry isn't: treat as offline */ }
  const changed = next !== backendState || (url && url !== API);
  if (url) { API = url; WS_BASE = url.replace(/^http/, 'ws'); }
  backendState = next;
  if (changed) listeners.forEach(fn => fn());
}

export async function initApi() {
  if (isLocal) return;
  backendState = 'checking';
  await discover();
  setInterval(discover, 20_000);
}
