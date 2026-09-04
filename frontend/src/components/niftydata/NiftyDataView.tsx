import { useEffect, useMemo, useState, useRef } from 'react';

const API = 'http://localhost:8000';

interface Candle { time: string; open: number; high: number; low: number; close: number; }
type RangeKey = 'today' | '7d' | '30d' | '90d' | 'all';
const RANGES: { key: RangeKey; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: '7d',    label: '7 Days' },
  { key: '30d',   label: '30 Days' },
  { key: '90d',   label: '90 Days' },
  { key: 'all',   label: 'All Time' },
];

interface Snapshot {
  snapshot_time: string; date: string; strike: number; option_type: string; moneyness_rank: number;
  nifty_spot: number | null; open: number | null; high: number | null; low: number | null;
  close: number | null; volume: number | null; oi: number | null;
  oi_day_high: number | null; oi_day_low: number | null;
  buy_quantity: number | null; sell_quantity: number | null; day_volume: number | null;
  bid_price: number | null; ask_price: number | null; spread_pct: number | null;
}
interface Status {
  date: string; rows_today: number; last_snapshot_at: string | null;
  contracts: number; expiry: string | null; index_subscribed: boolean;
}

function fmtTime(iso: string, withDate = false) {
  const m = iso.match(/(\d{4}-\d{2}-\d{2})T?(\d{2}:\d{2}:\d{2})/);
  if (!m) return iso;
  return withDate ? `${m[1]} ${m[2]}` : m[2];
}

function secondsAgo(iso: string | null): number | null {
  if (!iso) return null;
  const t = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T')).getTime();
  return Math.max(0, Math.round((Date.now() - t) / 1000));
}

// ── Candle chart ─────────────────────────────────────────────────────────────
const MAX_CHART_CANDLES = 1500;   // beyond this, thin the series so the SVG stays responsive

function CandleChart({ candles, showDate }: { candles: Candle[]; showDate: boolean }) {
  if (!candles.length) {
    return (
      <div className="h-56 flex items-center justify-center text-muted text-sm">
        Waiting for NIFTY spot candles — appears once the collector resolves today's chain.
      </div>
    );
  }
  // Thin (not aggregate) for very wide ranges -- keeps every Nth real candle rather
  // than merging OHLC, so the shape stays honest even if some detail is dropped.
  const step = Math.max(1, Math.ceil(candles.length / MAX_CHART_CANDLES));
  const shown = step > 1 ? candles.filter((_, i) => i % step === 0) : candles;

  const W = 1000, H = 220, PL = 54, PR = 12, PT = 12, PB = 22;
  const n = shown.length;
  const highs = shown.map(c => c.high), lows = shown.map(c => c.low);
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
      {shown.map((c, i) => {
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
                {showDate ? fmtTime(c.time, true).slice(0, 10) : fmtTime(c.time).slice(0, 5)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────
// Table virtualization: with the row cap removed, "today" alone can be ~8,500
// rows and wider ranges far more -- mounting every row as a real <tr> (each
// with a hover transition) is what was freezing the tab. Only the rows within
// the scrolled viewport (+ overscan) are ever in the DOM at once.
const ROW_H = 25;          // px -- must match the rendered row height below
const VIEWPORT_H = 520;    // px -- must match the scroll container's max-h
const OVERSCAN = 15;       // rows rendered above/below the visible window

export default function NiftyDataView() {
  const [range, setRange] = useState<RangeKey>('today');
  const [status, setStatus] = useState<Status | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [rows, setRows] = useState<Snapshot[]>([]);
  const [nowTick, setNowTick] = useState(Date.now());
  const [scrollTop, setScrollTop] = useState(0);
  const poll = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const clock = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const load = async (r: RangeKey) => {
    try {
      const [s, c, rows] = await Promise.all([
        fetch(`${API}/api/nifty-data/status`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/candles?range=${r}`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/snapshots?range=${r}&limit=200000`).then(x => x.json()),
      ]);
      setStatus(s); setCandles(c); setRows(rows);
    } catch { /* backend not reachable this tick — keep showing last known data */ }
  };

  useEffect(() => {
    load(range);
    clearInterval(poll.current);
    // Only poll live for "today" -- historical ranges don't change once loaded.
    if (range === 'today') poll.current = setInterval(() => load(range), 5000);
    setScrollTop(0);
    scrollRef.current?.scrollTo(0, 0);
    return () => clearInterval(poll.current);
  }, [range]);

  useEffect(() => {
    clock.current = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(clock.current);
  }, []);

  const staleness = secondsAgo(status?.last_snapshot_at ?? null);
  const isLive = staleness !== null && staleness < 90;
  void nowTick; // re-render trigger for the staleness clock

  // Flatten latest-first rows into a single list, grouped by minute (newest
  // minute block first) -- computed once per data change, not on every render/
  // scroll tick, and sorted once rather than re-sorting each group on every render.
  const { flatRows, minuteCount } = useMemo(() => {
    const out: { row: Snapshot; isGroupStart: boolean }[] = [];
    let minutes = 0;
    let i = 0;
    while (i < rows.length) {
      const time = rows[i].snapshot_time;
      const group: Snapshot[] = [];
      while (i < rows.length && rows[i].snapshot_time === time) { group.push(rows[i]); i++; }
      group.sort((a, b) => a.strike - b.strike || a.option_type.localeCompare(b.option_type));
      group.forEach((row, gi) => out.push({ row, isGroupStart: gi === 0 }));
      minutes++;
    }
    return { flatRows: out, minuteCount: minutes };
  }, [rows]);

  const total = flatRows.length;
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const endIdx = Math.min(total, startIdx + Math.ceil(VIEWPORT_H / ROW_H) + OVERSCAN * 2);
  const visibleRows = flatRows.slice(startIdx, endIdx);
  const topPad = startIdx * ROW_H;
  const botPad = (total - endIdx) * ROW_H;

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

      {/* Range switch */}
      <div className="flex items-center gap-1 bg-surface border border-border rounded-lg p-1 w-fit">
        {RANGES.map(r => (
          <button
            key={r.key}
            onClick={() => setRange(r.key)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors
              ${range === r.key ? 'bg-accent text-white' : 'text-muted hover:text-white'}`}
          >
            {r.label}
          </button>
        ))}
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
          <span className="font-bold text-sm text-white">
            NIFTY 50 Spot — 1m candles {range === 'today' ? '(today, live)' : `(${RANGES.find(r => r.key === range)?.label})`}
          </span>
          <span className="text-xs text-muted">{candles.length} candles</span>
        </div>
        <div className="px-4 pt-3 pb-1">
          <CandleChart candles={candles} showDate={range !== 'today'} />
        </div>
      </div>

      {/* Raw data table — newest first */}
      <div className="bg-surface rounded-xl border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="font-bold text-sm text-white">
            Raw Snapshots — newest first {range !== 'today' && <span className="text-muted font-normal">({RANGES.find(r => r.key === range)?.label})</span>}
          </span>
          <span className="text-xs text-muted">Showing {rows.length} rows across {minuteCount} minute(s)</span>
        </div>
        <div ref={scrollRef} onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
             className="max-h-[520px] overflow-y-auto overflow-x-auto">
          <table className="w-full text-left" style={{ tableLayout: 'fixed' }}>
            <thead className="sticky top-0 bg-surface2 z-10">
              <tr className="text-[10px] uppercase tracking-wider text-muted border-b border-border">
                <th className="px-2 py-2">Time</th>
                <th className="px-2 py-2 text-right">Spot</th>
                <th className="px-2 py-2 text-right">Strike</th>
                <th className="px-2 py-2">Type</th>
                <th className="px-2 py-2 text-right">Rank</th>
                <th className="px-2 py-2 text-right">Close</th>
                <th className="px-2 py-2 text-right">OI</th>
                <th className="px-2 py-2 text-right">OI Hi/Lo</th>
                <th className="px-2 py-2 text-right">Buy Qty</th>
                <th className="px-2 py-2 text-right">Sell Qty</th>
                <th className="px-2 py-2 text-right">Day Vol</th>
                <th className="px-2 py-2 text-right">Bid</th>
                <th className="px-2 py-2 text-right">Ask</th>
                <th className="px-2 py-2 text-right">Spread%</th>
              </tr>
            </thead>
            <tbody>
              {total === 0 && (
                <tr><td colSpan={13} className="px-2 py-8 text-center text-muted text-sm">
                  No data yet today.
                </td></tr>
              )}
              {topPad > 0 && <tr aria-hidden style={{ height: topPad }}><td colSpan={13} /></tr>}
              {visibleRows.map(({ row: r, isGroupStart }) => (
                <tr key={`${r.snapshot_time}-${r.strike}-${r.option_type}`} style={{ height: ROW_H }}
                    className={`border-b border-border/30 hover:bg-white/[0.02] text-xs
                      ${isGroupStart ? 'border-t-2 border-t-bg' : ''}`}>
                  <td className="px-2 text-white font-medium">
                    {isGroupStart ? fmtTime(r.snapshot_time, range !== 'today') : ''}
                  </td>
                  <td className="px-2 text-right text-muted">
                    {isGroupStart && r.nifty_spot != null ? r.nifty_spot.toFixed(2) : ''}
                  </td>
                  <td className="px-2 text-right text-white">{r.strike.toFixed(0)}</td>
                  <td className={`px-2 font-semibold ${r.option_type === 'CE' ? 'text-up' : 'text-down'}`}>
                    {r.option_type}
                  </td>
                  <td className={`px-2 text-right ${r.moneyness_rank === 0 ? 'text-accent font-bold' : 'text-muted'}`}>
                    {r.moneyness_rank > 0 ? `+${r.moneyness_rank}` : r.moneyness_rank}
                  </td>
                  <td className="px-2 text-right text-white">{r.close?.toFixed(2) ?? '—'}</td>
                  <td className="px-2 text-right text-muted">{r.oi?.toLocaleString('en-IN') ?? '—'}</td>
                  <td className="px-2 text-right text-muted whitespace-nowrap">
                    {r.oi_day_high != null ? r.oi_day_high.toLocaleString('en-IN') : '—'}
                    {' / '}
                    {r.oi_day_low != null ? r.oi_day_low.toLocaleString('en-IN') : '—'}
                  </td>
                  <td className="px-2 text-right text-up">{r.buy_quantity?.toLocaleString('en-IN') ?? '—'}</td>
                  <td className="px-2 text-right text-down">{r.sell_quantity?.toLocaleString('en-IN') ?? '—'}</td>
                  <td className="px-2 text-right text-muted">{r.day_volume?.toLocaleString('en-IN') ?? '—'}</td>
                  <td className="px-2 text-right text-muted">{r.bid_price?.toFixed(2) ?? '—'}</td>
                  <td className="px-2 text-right text-muted">{r.ask_price?.toFixed(2) ?? '—'}</td>
                  <td className="px-2 text-right text-muted">{r.spread_pct?.toFixed(2) ?? '—'}</td>
                </tr>
              ))}
              {botPad > 0 && <tr aria-hidden style={{ height: botPad }}><td colSpan={13} /></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
