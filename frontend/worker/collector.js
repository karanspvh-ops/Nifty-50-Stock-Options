// Cloud NIFTY option collector + sync endpoints (D1 binding: DB).
//
// Every minute 09:15-15:30 IST on weekdays (Cron Trigger) -- whether or not
// the office backend is running; this is the only NIFTY collector -- take one
// snapshot of the ±10 strike ladder around ATM
// (nearest expiry) straight from Zerodha's quote API: LTP, OI, OI range,
// pending buy/sell quantity, day volume, bid/ask and 5-level depth. Minute
// OHLC/volume aren't in a quote; the backend's end-of-day completion fills
// those from Zerodha's minute history when it next runs.
//
// The backend supplies what this needs and drains what it collects:
//   POST /sync/kite         today's access token + api key      (secret header)
//   POST /sync/chain        NIFTY option list for the next expiries
//   GET  /sync/nifty?after= rows to merge into the local database
//   POST /sync/nifty/ack    {ids: [...]}: delete exactly the rows the backend has saved
// The token is stored in D1 and never returned by any endpoint.
//
// Public, read-only (no secrets; market data only), for the Nifty page and the
// backend's stall alert -- they work whether or not the office backend is on:
//   GET /api/_collector/status                 last run, last saved minute, why it skipped
//   GET /api/_collector/minutes?date=&since=   one row per collected minute (time, spot, rows, book)
//   GET /api/_collector/minute?t=YYYY-MM-DDTHH:MM   every contract row of that minute
// Rows stay readable here until the backend has merged them (after 15:40).

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
  const call = () => fetch(`https://api.kite.trade${path}`, {
    headers: { 'X-Kite-Version': '3', Authorization: `token ${auth.api_key}:${auth.access_token}` },
  });
  let r = await call();
  if (r.status === 429) {                      // shares the 1/s quote limit with the office backend
    await new Promise(res => setTimeout(res, QUOTE_GAP_MS));
    r = await call();
  }
  if (!r.ok) throw new Error(`Kite ${path.split('?')[0]} -> HTTP ${r.status}`);
  return (await r.json()).data || {};
}

let schemaReady = false;
async function ensureSchema(env) {
  if (schemaReady) return;
  await env.DB.batch([
    env.DB.prepare(`CREATE TABLE IF NOT EXISTS nifty_minutes (
      date TEXT NOT NULL, t TEXT NOT NULL, spot REAL, rows INTEGER NOT NULL, book INTEGER NOT NULL,
      expiry TEXT, PRIMARY KEY (date, t))`),
  ]);
  schemaReady = true;
}

// Scheduled entry point: run the collector and record the outcome, so the
// site and the backend can tell "collecting" from "stalled" at any time.
export async function collectAndRecord(env) {
  const now = ist();
  let r;
  try { r = await collect(env); } catch (e) { r = { error: String(e.message || e) }; }
  if (r.skipped === 'outside market hours') return r;
  const prev = (await kvGet(env, 'collector_status')) || {};
  const st = {
    ...prev,
    date: now.date,
    last_run_at: now.minute,
    last_result: r.saved ? 'saved' : r.closed ? 'closed' : r.error ? 'error' : 'skipped',
    last_reason: r.error || r.skipped || r.closed || null,
    market_closed: r.closed ? now.date : (prev.market_closed === now.date ? now.date : null),
  };
  if (r.saved) Object.assign(st, { last_saved_minute: r.minute, last_saved_rows: r.saved, expiry: r.expiry, spot: r.spot });
  await kvPut(env, 'collector_status', st);
  return r;
}

export async function collect(env, { force = false } = {}) {
  const now = ist();
  if (!force && (now.dow === 0 || now.dow === 6 || now.hm < '09:15' || now.hm > '15:30')) return { skipped: 'outside market hours' };
  await ensureSchema(env);

  const auth = await kvGet(env, 'kite');
  if (!auth || auth.date !== now.date) return { skipped: 'no Zerodha session for today (log in on the office PC)' };
  const chain = await kvGet(env, 'nifty_chain');
  if (!chain || !chain.contracts?.length) return { skipped: 'NIFTY option list not received yet' };

  // Full quote (not LTP) so the index's own timestamp shows whether the
  // market traded today: on an exchange holiday Zerodha still returns the
  // last prices, which must not be stored as a trading day.
  const spotQ = await kite(env, auth, `/quote?i=${encodeURIComponent('NSE:NIFTY 50')}`);
  const sq = spotQ['NSE:NIFTY 50'] || {};
  const spot = sq.last_price;
  if (!spot) return { skipped: 'no NIFTY spot price' };
  const stamp = String(sq.timestamp || sq.last_trade_time || '');
  if (!force && stamp && stamp.slice(0, 10) !== now.date) return { closed: `market closed today (index last updated ${stamp})` };

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
  let book = 0;
  for (const c of picked) {
    const d = q['NFO:' + c.s];
    if (!d) continue;
    const buys = d.depth?.buy || [], sells = d.depth?.sell || [];
    const bid = buys[0]?.price > 0 ? buys[0].price : null;
    const ask = sells[0]?.price > 0 ? sells[0].price : null;
    const spread = bid && ask ? Math.round((ask - bid) / ((ask + bid) / 2) * 10000) / 100 : null;
    if (bid && ask) book++;
    rows.push(stmt.bind(now.date, now.minute, expiry, c.k, c.t, ladder.get(c.k), spot, d.last_price ?? null,
      d.oi ?? null, d.oi_day_high ?? null, d.oi_day_low ?? null, d.buy_quantity ?? null, d.sell_quantity ?? null,
      d.volume ?? null, bid, ask, spread, JSON.stringify(buys.slice(0, 5)), JSON.stringify(sells.slice(0, 5))));
  }
  // No live order book across the ladder means the market isn't trading
  // (holiday, or a stale session): store nothing rather than last-price junk.
  if (!force && rows.length && book < rows.length / 4) {
    return { closed: `no live bids/asks (${book} of ${rows.length} contracts) -- market not trading` };
  }
  if (rows.length) {
    const n = rows.length;
    rows.push(env.DB.prepare(`INSERT INTO nifty_minutes (date, t, spot, rows, book, expiry) VALUES (?,?,?,?,?,?)
      ON CONFLICT(date, t) DO UPDATE SET spot = excluded.spot, rows = excluded.rows, book = excluded.book`)
      .bind(now.date, now.minute.slice(0, 16).replace(' ', 'T'), spot, n, book, expiry));
    await env.DB.batch(rows);
  }
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
    const before = url.searchParams.get('before_date') || '9999-12-31';     // date < before_date only
    const { results } = await env.DB.prepare('SELECT * FROM nifty_snapshots WHERE id > ? AND date < ? ORDER BY id LIMIT ?')
      .bind(after, before, limit).all();
    const left = await env.DB.prepare('SELECT COUNT(*) AS n FROM nifty_snapshots').first();
    return json({ rows: results, total: left.n });
  }
  if (p === '/sync/nifty/ack' && request.method === 'POST') {
    const b = await request.json();
    const ids = Array.isArray(b.ids) ? b.ids.map(Number).filter(Boolean) : [];
    if (!ids.length) return json({ error: 'ids required' }, 400);
    // Delete exactly the rows the backend saved (rows of other days stay until their turn)
    const stmts = [];
    for (let i = 0; i < ids.length; i += 90) {
      const part = ids.slice(i, i + 90);
      stmts.push(env.DB.prepare(`DELETE FROM nifty_snapshots WHERE id IN (${part.map(() => '?').join(',')})`).bind(...part));
    }
    stmts.push(env.DB.prepare("DELETE FROM nifty_minutes WHERE date < date('now', '+5 hours', '+30 minutes', '-3 days')"));
    const res = await env.DB.batch(stmts);
    const r = { meta: { changes: res.reduce((s, x) => s + (x.meta?.changes || 0), 0) } };
    return json({ ok: true, deleted: r.meta?.changes ?? null });
  }
  if (p === '/sync/collect-now' && request.method === 'POST') {
    // Manual test of the collector (ignores market hours / backend state)
    try { return json(await collect(env, { force: true })); } catch (e) { return json({ error: String(e.message || e) }, 500); }
  }
  return json({ error: 'not found' }, 404);
}

// ── /api/_collector/* (public, read-only) ───────────────────────────────────
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET', 'Cache-Control': 'no-store' };
const SNAP_COLS = ['strike', 'option_type', 'moneyness_rank', 'expiry', 'nifty_spot', 'close', 'oi', 'oi_day_high',
  'oi_day_low', 'buy_quantity', 'sell_quantity', 'day_volume', 'bid_price', 'ask_price', 'spread_pct'];

export async function collectorApi(request, env, url) {
  const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { 'Content-Type': 'application/json', ...CORS } });
  if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
  if (request.method !== 'GET') return json({ error: 'read only' }, 405);
  await ensureSchema(env);
  const p = url.pathname;
  const now = ist();

  if (p === '/api/_collector/status') {
    const st = (await kvGet(env, 'collector_status')) || {};
    const cnt = await env.DB.prepare('SELECT COUNT(*) AS m, COALESCE(SUM(rows),0) AS r FROM nifty_minutes WHERE date = ?').bind(now.date).first();
    const marketHours = now.dow >= 1 && now.dow <= 5 && now.hm >= '09:15' && now.hm <= '15:30';
    return json({ ...st, today: now.date, now: now.minute, market_hours: marketHours,
      minutes_today: cnt?.m ?? 0, rows_today: cnt?.r ?? 0 });
  }
  if (p === '/api/_collector/minutes') {
    const date = url.searchParams.get('date') || now.date;
    const since = url.searchParams.get('since') || '';
    const { results } = await env.DB.prepare(
      'SELECT t, spot, rows, book, expiry FROM nifty_minutes WHERE date = ? AND t > ? ORDER BY t DESC LIMIT 1000')
      .bind(date, since).all();
    return json(results);
  }
  if (p === '/api/_collector/minute') {
    const t = url.searchParams.get('t') || '';
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(t)) return json({ error: 't=YYYY-MM-DDTHH:MM required' }, 400);
    const from = t.replace('T', ' ') + ':00', to = t.replace('T', ' ') + ':59';
    const { results } = await env.DB.prepare(
      `SELECT ${SNAP_COLS.join(', ')} FROM nifty_snapshots WHERE snapshot_time >= ? AND snapshot_time <= ? ORDER BY strike, option_type`)
      .bind(from, to).all();
    return json(results.map(r => ({ ...r, open: null, high: null, low: null, volume: null })));
  }
  return json({ error: 'not found' }, 404);
}
