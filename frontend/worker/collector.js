// Cloud NIFTY option collector + sync endpoints (D1 binding: DB).
//
// Every minute 09:15-15:30 IST on weekdays (Cron Trigger), IF the office
// backend is offline, take one snapshot of the ±10 strike ladder around ATM
// (nearest expiry) straight from Zerodha's quote API: LTP, OI, OI range,
// pending buy/sell quantity, day volume, bid/ask and 5-level depth. Minute
// OHLC/volume aren't in a quote; the backend's end-of-day completion fills
// those from Zerodha's minute history when it next runs.
//
// The backend supplies what this needs and drains what it collects:
//   POST /sync/kite         today's access token + api key      (secret header)
//   POST /sync/chain        NIFTY option list for the next expiries
//   GET  /sync/nifty?after= rows to merge into the local database
//   POST /sync/nifty/ack    {upto: id}: delete rows the backend has saved
// The token is stored in D1 and never returned by any endpoint.

const LADDER = 10;
const QUOTE_GAP_MS = 1100;            // Zerodha quote API: 1 request/second

function ist(d = new Date()) {
  const t = new Date(d.getTime() + 5.5 * 3600 * 1000);
  const iso = t.toISOString();        // shifted clock: read fields as IST
  return { date: iso.slice(0, 10), hm: iso.slice(11, 16), minute: iso.slice(0, 16).replace('T', ' ') + ':00', dow: t.getUTCDay() };
}

async function kvGet(env, k) {
  const row = await env.DB.prepare('SELECT v FROM kv WHERE k = ?').bind(k).first();
  return row ? JSON.parse(row.v) : null;
}

async function kvPut(env, k, v) {
  await env.DB.prepare('INSERT INTO kv (k, v, at) VALUES (?, ?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v, at = excluded.at')
    .bind(k, JSON.stringify(v), Date.now()).run();
}

async function kite(env, auth, path) {
  const r = await fetch(`https://api.kite.trade${path}`, {
    headers: { 'X-Kite-Version': '3', Authorization: `token ${auth.api_key}:${auth.access_token}` },
  });
  if (!r.ok) throw new Error(`Kite ${path.split('?')[0]} -> HTTP ${r.status}`);
  return (await r.json()).data || {};
}

export async function collect(env, { force = false } = {}) {
  const now = ist();
  if (!force && (now.dow === 0 || now.dow === 6 || now.hm < '09:15' || now.hm > '15:30')) return { skipped: 'outside market hours' };

  if (!force) {
    const st = await env.RELAY.get(env.RELAY.idFromName(env.DESK_INSTANCE)).fetch('https://desk/api/_relay/status');
    if ((await st.json()).online) return { skipped: 'office backend is collecting' };
  }
  const auth = await kvGet(env, 'kite');
  if (!auth || auth.date !== now.date) return { skipped: 'no Zerodha session for today (log in on the office PC)' };
  const chain = await kvGet(env, 'nifty_chain');
  if (!chain || !chain.contracts?.length) return { skipped: 'NIFTY option list not received yet' };

  const spotQ = await kite(env, auth, `/quote/ltp?i=${encodeURIComponent('NSE:NIFTY 50')}`);
  const spot = spotQ['NSE:NIFTY 50']?.last_price;
  if (!spot) return { skipped: 'no NIFTY spot price' };

  const expiry = chain.contracts.map(c => c.e).filter(e => e >= now.date).sort()[0];
  const ofExpiry = chain.contracts.filter(c => c.e === expiry);
  const strikes = [...new Set(ofExpiry.map(c => c.k))].sort((a, b) => a - b);
  let atm = 0;
  strikes.forEach((s, i) => { if (Math.abs(s - spot) < Math.abs(strikes[atm] - spot)) atm = i; });
  const ladder = new Map(strikes.slice(Math.max(0, atm - LADDER), atm + LADDER + 1).map(s => [s, strikes.indexOf(s) - atm]));
  const picked = ofExpiry.filter(c => ladder.has(c.k));

  await new Promise(r => setTimeout(r, QUOTE_GAP_MS));
  const q = await kite(env, auth, '/quote?' + picked.map(c => 'i=' + encodeURIComponent('NFO:' + c.s)).join('&'));

  const stmt = env.DB.prepare(`INSERT OR IGNORE INTO nifty_snapshots
    (date, snapshot_time, expiry, strike, option_type, moneyness_rank, nifty_spot, close, oi, oi_day_high, oi_day_low,
     buy_quantity, sell_quantity, day_volume, bid_price, ask_price, spread_pct, bid_depth, ask_depth)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const rows = [];
  for (const c of picked) {
    const d = q['NFO:' + c.s];
    if (!d) continue;
    const buys = d.depth?.buy || [], sells = d.depth?.sell || [];
    const bid = buys[0]?.price > 0 ? buys[0].price : null;
    const ask = sells[0]?.price > 0 ? sells[0].price : null;
    const spread = bid && ask ? Math.round((ask - bid) / ((ask + bid) / 2) * 10000) / 100 : null;
    rows.push(stmt.bind(now.date, now.minute, expiry, c.k, c.t, ladder.get(c.k), spot, d.last_price ?? null,
      d.oi ?? null, d.oi_day_high ?? null, d.oi_day_low ?? null, d.buy_quantity ?? null, d.sell_quantity ?? null,
      d.volume ?? null, bid, ask, spread, JSON.stringify(buys.slice(0, 5)), JSON.stringify(sells.slice(0, 5))));
  }
  if (rows.length) await env.DB.batch(rows);
  return { saved: rows.length, minute: now.minute, spot, expiry };
}

// ── /sync/* (office backend only) ────────────────────────────────────────────
export async function sync(request, env, url) {
  if (!env.RELAY_SECRET || request.headers.get('X-Relay-Secret') !== env.RELAY_SECRET) {
    return new Response('forbidden', { status: 403 });
  }
  const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json' } });
  const p = url.pathname;

  if (p === '/sync/kite' && request.method === 'POST') {
    const b = await request.json();
    if (!b.api_key || !b.access_token || !b.date) return json({ error: 'api_key, access_token and date required' }, 400);
    await kvPut(env, 'kite', { api_key: b.api_key, access_token: b.access_token, date: b.date });
    return json({ ok: true });
  }
  if (p === '/sync/chain' && request.method === 'POST') {
    const b = await request.json();
    await kvPut(env, 'nifty_chain', { contracts: b.contracts || [] });
    return json({ ok: true, contracts: (b.contracts || []).length });
  }
  if (p === '/sync/nifty' && request.method === 'GET') {
    const after = Number(url.searchParams.get('after') || 0);
    const limit = Math.min(2000, Number(url.searchParams.get('limit') || 1000));
    const { results } = await env.DB.prepare('SELECT * FROM nifty_snapshots WHERE id > ? ORDER BY id LIMIT ?').bind(after, limit).all();
    const left = await env.DB.prepare('SELECT COUNT(*) AS n FROM nifty_snapshots').first();
    return json({ rows: results, total: left.n });
  }
  if (p === '/sync/nifty/ack' && request.method === 'POST') {
    const b = await request.json();
    const upto = Number(b.upto || 0);
    if (!upto) return json({ error: 'upto required' }, 400);
    const r = await env.DB.prepare('DELETE FROM nifty_snapshots WHERE id <= ?').bind(upto).run();
    return json({ ok: true, deleted: r.meta?.changes ?? null });
  }
  if (p === '/sync/collect-now' && request.method === 'POST') {
    // Manual test of the collector (ignores market hours / backend state)
    try { return json(await collect(env, { force: true })); } catch (e) { return json({ error: String(e.message || e) }, 500); }
  }
  return json({ error: 'not found' }, 404);
}
