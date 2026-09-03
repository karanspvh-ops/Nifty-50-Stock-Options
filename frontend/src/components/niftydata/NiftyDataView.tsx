import { Fragment, useEffect, useState, useRef } from 'react';

const API = 'http://localhost:8000';

interface Candle { time: string; open: number; high: number; low: number; close: number; }
interface Snapshot {
  snapshot_time: string; strike: number; option_type: string; moneyness_rank: number;
  nifty_spot: number | null; open: number | null; high: number | null; low: number | null;
  close: number | null; volume: number | null; oi: number | null;
  bid_price: number | null; ask_price: number | null; spread_pct: number | null;
}
interface Status {
  date: string; rows_today: number; last_snapshot_at: string | null;
  contracts: number; expiry: string | null; index_subscribed: boolean;
}

function fmtTime(iso: string) {
  const m = iso.match(/T?(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : iso;
}

function secondsAgo(iso: string | null): number | null {
  if (!iso) return null;
  const t = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T')).getTime();
  return Math.max(0, Math.round((Date.now() - t) / 1000));
}

// ── Candle chart ─────────────────────────────────────────────────────────────
function CandleChart({ candles }: { candles: Candle[] }) {
  if (!candles.length) {
    return (
      <div className="h-56 flex items-center justify-center text-muted text-sm">
        Waiting for NIFTY spot candles — appears once the collector resolves today's chain.
      </div>
    );
  }
  const W = 1000, H = 220, PL = 54, PR = 12, PT = 12, PB = 22;
  const n = candles.length;
  const highs = candles.map(c => c.high), lows = candles.map(c => c.low);
  let mn = Math.min(...lows), mx = Math.max(...highs);
  const rng = Math.max(mx - mn, 1);
  mn -= rng * 0.08; mx += rng * 0.08;
  const innerW = W - PL - PR, innerH = H - PT - PB;
  const slot = innerW / n;
  const bw = Math.max(2, Math.min(10, slot * 0.6));
  const x = (i: number) => PL + i * slot + slot / 2;
  const y = (v: number) => PT + (1 - (v - mn) / (mx - mn)) * innerH;

  const gridVals = [0, 1, 2, 3].map(k => mn + (mx - mn) * k / 3);
  const showEvery = Math.max(1, Math.ceil(n / 10));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
      {gridVals.map((v, i) => (
        <g key={i}>
          <line x1={PL} y1={y(v)} x2={W - PR} y2={y(v)} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
          <text x={PL - 6} y={y(v)} textAnchor="end" dominantBaseline="middle" fontSize="9" fill="#6b7280">
            {v.toFixed(0)}
          </text>
        </g>
      ))}
      {candles.map((c, i) => {
        const up = c.close >= c.open;
        const color = up ? '#4ade80' : '#f87171';
        const bodyTop = y(Math.max(c.open, c.close));
        const bodyBot = y(Math.min(c.open, c.close));
        return (
          <g key={c.time}>
            <line x1={x(i)} y1={y(c.high)} x2={x(i)} y2={y(c.low)} stroke={color} strokeWidth="1" />
            <rect x={x(i) - bw / 2} y={bodyTop} width={bw} height={Math.max(1, bodyBot - bodyTop)} fill={color} />
            {i % showEvery === 0 && (
              <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="9" fill="#6b7280">
                {fmtTime(c.time).slice(0, 5)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────
export default function NiftyDataView() {
  const [status, setStatus] = useState<Status | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [rows, setRows] = useState<Snapshot[]>([]);
  const [nowTick, setNowTick] = useState(Date.now());
  const poll = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const clock = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const load = async () => {
    try {
      const [s, c, r] = await Promise.all([
        fetch(`${API}/api/nifty-data/status`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/candles`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/snapshots?limit=300`).then(x => x.json()),
      ]);
      setStatus(s); setCandles(c); setRows(r);
    } catch { /* backend not reachable this tick — keep showing last known data */ }
  };

  useEffect(() => {
    load();
    poll.current = setInterval(load, 5000);
    clock.current = setInterval(() => setNowTick(Date.now()), 1000);
    return () => { clearInterval(poll.current); clearInterval(clock.current); };
  }, []);

  const staleness = secondsAgo(status?.last_snapshot_at ?? null);
  const isLive = staleness !== null && staleness < 90;
  void nowTick; // re-render trigger for the staleness clock

  // Group latest-first rows by minute for a readable table (newest minute block first)
  const grouped: { time: string; items: Snapshot[] }[] = [];
  for (const r of rows) {
    const bucket = grouped[grouped.length - 1];
    if (bucket && bucket.time === r.snapshot_time) bucket.items.push(r);
    else grouped.push({ time: r.snapshot_time, items: [r] });
  }

  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <h1 className="text-white font-bold text-lg">Nifty Data Collector</h1>
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-up' : 'bg-down'}`} />
          <span className="text-muted">
            {isLive ? 'Live' : 'Not receiving data'}
            {staleness !== null && ` — last row ${staleness}s ago`}
          </span>
        </div>
      </div>

      {/* Status strip */}
      <div className="grid grid-cols-5 gap-3">
        <div className="bg-surface rounded-lg border border-border p-3">
          <div className="text-[10px] uppercase text-muted mb-1">Rows Today</div>
          <div className="text-lg font-bold text-white">{status?.rows_today ?? '—'}</div>
        </div>
        <div className="bg-surface rounded-lg border border-border p-3">
          <div className="text-[10px] uppercase text-muted mb-1">Last Snapshot</div>
          <div className="text-lg font-bold text-white">
            {status?.last_snapshot_at ? fmtTime(status.last_snapshot_at) : '—'}
          </div>
        </div>
        <div className="bg-surface rounded-lg border border-border p-3">
          <div className="text-[10px] uppercase text-muted mb-1">Contracts Tracked</div>
          <div className="text-lg font-bold text-white">{status?.contracts ?? '—'}</div>
        </div>
        <div className="bg-surface rounded-lg border border-border p-3">
          <div className="text-[10px] uppercase text-muted mb-1">Expiry</div>
          <div className="text-lg font-bold text-white">{status?.expiry ?? '—'}</div>
        </div>
        <div className="bg-surface rounded-lg border border-border p-3">
          <div className="text-[10px] uppercase text-muted mb-1">Index Feed</div>
          <div className={`text-lg font-bold ${status?.index_subscribed ? 'text-up' : 'text-down'}`}>
            {status?.index_subscribed ? 'Subscribed' : 'Not yet'}
          </div>
        </div>
      </div>

      {/* Candle chart */}
      <div className="bg-surface rounded-xl border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="font-bold text-sm text-white">NIFTY 50 Spot — 1m candles (today, live)</span>
          <span className="text-xs text-muted">{candles.length} candles</span>
        </div>
        <div className="px-4 pt-3 pb-1">
          <CandleChart candles={candles} />
        </div>
      </div>

      {/* Raw data table — newest first */}
      <div className="bg-surface rounded-xl border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="font-bold text-sm text-white">Raw Snapshots — newest first</span>
          <span className="text-xs text-muted">Showing latest {rows.length} rows across {grouped.length} minute(s)</span>
        </div>
        <div className="max-h-[520px] overflow-y-auto overflow-x-auto">
          <table className="w-full text-left">
            <thead className="sticky top-0 bg-surface2 z-10">
              <tr className="text-[10px] uppercase tracking-wider text-muted border-b border-border">
                <th className="px-2 py-2">Time</th>
                <th className="px-2 py-2 text-right">Spot</th>
                <th className="px-2 py-2 text-right">Strike</th>
                <th className="px-2 py-2">Type</th>
                <th className="px-2 py-2 text-right">Rank</th>
                <th className="px-2 py-2 text-right">Close</th>
                <th className="px-2 py-2 text-right">OI</th>
                <th className="px-2 py-2 text-right">Bid</th>
                <th className="px-2 py-2 text-right">Ask</th>
                <th className="px-2 py-2 text-right">Spread%</th>
              </tr>
            </thead>
            <tbody>
              {grouped.length === 0 && (
                <tr><td colSpan={10} className="px-2 py-8 text-center text-muted text-sm">
                  No data yet today.
                </td></tr>
              )}
              {grouped.map((g, gi) => (
                <Fragment key={g.time}>
                  {gi > 0 && (
                    <tr><td colSpan={10} className="h-1 bg-bg" /></tr>
                  )}
                  {g.items
                    .sort((a, b) => a.strike - b.strike || a.option_type.localeCompare(b.option_type))
                    .map((r, ri) => (
                    <tr key={`${g.time}-${r.strike}-${r.option_type}`} className="border-b border-border/30 hover:bg-white/[0.02] text-xs">
                      <td className="px-2 py-1 text-white font-medium">
                        {ri === 0 ? fmtTime(r.snapshot_time) : ''}
                      </td>
                      <td className="px-2 py-1 text-right text-muted">
                        {ri === 0 && r.nifty_spot != null ? r.nifty_spot.toFixed(2) : ''}
                      </td>
                      <td className="px-2 py-1 text-right text-white">{r.strike.toFixed(0)}</td>
                      <td className={`px-2 py-1 font-semibold ${r.option_type === 'CE' ? 'text-up' : 'text-down'}`}>
                        {r.option_type}
                      </td>
                      <td className={`px-2 py-1 text-right ${r.moneyness_rank === 0 ? 'text-accent font-bold' : 'text-muted'}`}>
                        {r.moneyness_rank > 0 ? `+${r.moneyness_rank}` : r.moneyness_rank}
                      </td>
                      <td className="px-2 py-1 text-right text-white">{r.close?.toFixed(2) ?? '—'}</td>
                      <td className="px-2 py-1 text-right text-muted">{r.oi?.toLocaleString('en-IN') ?? '—'}</td>
                      <td className="px-2 py-1 text-right text-muted">{r.bid_price?.toFixed(2) ?? '—'}</td>
                      <td className="px-2 py-1 text-right text-muted">{r.ask_price?.toFixed(2) ?? '—'}</td>
                      <td className="px-2 py-1 text-right text-muted">{r.spread_pct?.toFixed(2) ?? '—'}</td>
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
