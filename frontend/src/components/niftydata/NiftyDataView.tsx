import { Fragment, useEffect, useState, useRef } from 'react';
import { CaretRight, ArrowsClockwise, CircleNotch } from '@phosphor-icons/react';
import Segmented from '../ui/Segmented';

import { API } from '../../lib/api';

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
  strike: number; option_type: string; moneyness_rank: number; expiry: string;
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
          <line x1={PL} y1={y(v)} x2={W - PR} y2={y(v)} stroke="rgb(22 24 29 / 0.07)" strokeWidth="1" />
          <text x={PL - 6} y={y(v)} textAnchor="end" dominantBaseline="middle" fontSize="9" fill="rgb(92 98 112)">
            {v.toFixed(0)}
          </text>
        </g>
      ))}
      {shown.map((c, i) => {
        const up = c.close >= c.open;
        const color = up ? 'rgb(11 125 69)' : 'rgb(196 43 35)';
        const bodyTop = y(Math.max(c.open, c.close));
        const bodyBot = y(Math.min(c.open, c.close));
        return (
          <g key={c.time}>
            <line x1={x(i)} y1={y(c.high)} x2={x(i)} y2={y(c.low)} stroke={color} strokeWidth="1" />
            <rect x={x(i) - bw / 2} y={bodyTop} width={bw} height={Math.max(1, bodyBot - bodyTop)} fill={color} />
            {i % showEvery === 0 && (
              <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="9" fill="rgb(92 98 112)">
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
// The minute list shows one compact row per snapshot minute (time + spot);
// a minute's ±10-strike rows are fetched only when it is expanded, so a long
// range never mounts tens of thousands of table rows.
interface MinuteRow { t: string; spot: number | null; rows: number; book: number; }
interface Backfill { running: boolean; started_at: string | null;
  results: { date: string; status: string; inserted?: number; filled?: number; note?: string }[]; }

const PAGE = 120;
const n0 = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString('en-IN'));
const p2 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2));

function MinuteDetail({ rows }: { rows: Snapshot[] | 'loading' }) {
  if (rows === 'loading') return (
    <div className="flex items-center gap-2 px-5 py-4 text-sm text-muted">
      <CircleNotch size={16} className="animate-spin" /> Loading strikes
    </div>
  );
  if (!rows.length) return <div className="px-5 py-4 text-sm text-muted">No contract rows for this minute.</div>;
  const th = 'px-2 py-1.5 text-right font-semibold';
  return (
    <div className="overflow-x-auto bg-white/60 border-b border-border">
      <table className="w-full text-xs whitespace-nowrap" style={{ minWidth: 1320 }}>
        <thead>
          <tr className="text-muted border-b border-border bg-bg/70">
            <th className={`${th} sticky left-0 z-[1] bg-bg`}>Strike</th><th className="px-2 py-1.5 text-left font-semibold">Type</th><th className={th}>Rank</th>
            <th className={th}>Open</th><th className={th}>High</th><th className={th}>Low</th><th className={th}>Close</th>
            <th className={th}>Vol (1m)</th><th className={th}>Day vol</th><th className={th}>OI</th><th className={th}>OI high / low</th>
            <th className={th}>Buy qty</th><th className={th}>Sell qty</th><th className={th}>Bid</th><th className={th}>Ask</th><th className={th}>Spread %</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const atm = r.moneyness_rank === 0;
            return (
              <tr key={`${r.strike}-${r.option_type}`}
                  className={`border-b border-border/50 tabular-nums ${atm ? 'bg-spvh-100/70' : r.option_type === 'PE' ? 'bg-bg/40' : ''}`}>
                <td className={`sticky left-0 z-[1] px-2 py-1 text-right text-ink ${atm ? 'font-bold bg-spvh-100' : 'bg-white'}`}>{r.strike.toFixed(0)}</td>
                <td className={`px-2 py-1 font-semibold ${r.option_type === 'CE' ? 'text-up' : 'text-down'}`}>{r.option_type}</td>
                <td className={`px-2 py-1 text-right ${atm ? 'text-accent font-bold' : 'text-muted'}`}>
                  {atm ? 'ATM' : r.moneyness_rank > 0 ? `+${r.moneyness_rank}` : r.moneyness_rank}
                </td>
                <td className="px-2 py-1 text-right text-ink2">{p2(r.open)}</td>
                <td className="px-2 py-1 text-right text-ink2">{p2(r.high)}</td>
                <td className="px-2 py-1 text-right text-ink2">{p2(r.low)}</td>
                <td className="px-2 py-1 text-right text-ink font-semibold">{p2(r.close)}</td>
                <td className="px-2 py-1 text-right text-ink2">{n0(r.volume)}</td>
                <td className="px-2 py-1 text-right text-ink2">{n0(r.day_volume)}</td>
                <td className="px-2 py-1 text-right text-ink">{n0(r.oi)}</td>
                <td className="px-2 py-1 text-right text-muted whitespace-nowrap">{n0(r.oi_day_high)} / {n0(r.oi_day_low)}</td>
                <td className="px-2 py-1 text-right text-ink2">{n0(r.buy_quantity)}</td>
                <td className="px-2 py-1 text-right text-ink2">{n0(r.sell_quantity)}</td>
                <td className="px-2 py-1 text-right text-ink2">{p2(r.bid_price)}</td>
                <td className="px-2 py-1 text-right text-ink2">{p2(r.ask_price)}</td>
                <td className="px-2 py-1 text-right text-muted">{p2(r.spread_pct)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function fmtDay(d: string) {
  return new Date(d + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
}

export default function NiftyDataView() {
  const [range, setRange] = useState<RangeKey>('today');
  const [status, setStatus] = useState<Status | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [minutes, setMinutes] = useState<MinuteRow[]>([]);
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<Record<string, Snapshot[] | 'loading'>>({});
  const [bf, setBf] = useState<Backfill | null>(null);
  const [nowTick, setNowTick] = useState(Date.now());
  const poll = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const clock = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const load = async (r: RangeKey) => {
    try {
      const [s, c, m, b] = await Promise.all([
        fetch(`${API}/api/nifty-data/status`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/candles?range=${r}`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/minutes?range=${r}`).then(x => x.json()),
        fetch(`${API}/api/nifty-data/backfill/status`).then(x => (x.ok ? x.json() : null)).catch(() => null),
      ]);
      setStatus(s); setCandles(c); setMinutes(Array.isArray(m) ? m : []); setBf(b);
    } catch { /* backend not reachable this tick — keep showing last known data */ }
  };

  useEffect(() => {
    load(range);
    setShown(PAGE);
    setOpen({});
    clearInterval(poll.current);
    // Only poll live for "today" -- historical ranges don't change once loaded.
    if (range === 'today') poll.current = setInterval(() => load(range), 5000);
    return () => clearInterval(poll.current);
  }, [range]);

  useEffect(() => {
    clock.current = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(clock.current);
  }, []);

  // While a completion run is going, follow its progress and reload once it ends
  useEffect(() => {
    if (!bf?.running) return;
    const id = setInterval(async () => {
      const b = await fetch(`${API}/api/nifty-data/backfill/status`).then(x => x.json()).catch(() => null);
      setBf(b);
      if (b && !b.running) load(range);
    }, 2000);
    return () => clearInterval(id);
  }, [bf?.running, range]);

  const toggle = async (t: string) => {
    if (open[t]) { setOpen(o => { const n = { ...o }; delete n[t]; return n; }); return; }
    setOpen(o => ({ ...o, [t]: 'loading' }));
    try {
      const rows = await fetch(`${API}/api/nifty-data/minute?t=${encodeURIComponent(t)}`).then(x => x.json());
      setOpen(o => (t in o ? { ...o, [t]: Array.isArray(rows) ? rows : [] } : o));
    } catch {
      setOpen(o => (t in o ? { ...o, [t]: [] } : o));
    }
  };

  const startBackfill = async () => {
    const r = await fetch(`${API}/api/nifty-data/backfill`, { method: 'POST' }).catch(() => null);
    if (r?.ok) setBf({ running: true, started_at: new Date().toISOString(), results: [] });
  };

  const staleness = secondsAgo(status?.last_snapshot_at ?? null);
  const isLive = staleness !== null && staleness < 90;
  void nowTick; // re-render trigger for the staleness clock

  const multiDay = range !== 'today';
  const visible = minutes.slice(0, shown);
  const lastRun = bf?.results ?? [];

  return (
    <div className="page"><div className="page-inner space-y-3 md:space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
          <h1 className="text-ink font-bold text-xl md:text-2xl leading-tight">Nifty Data Collector</h1>
          <span className="flex items-center gap-1.5 text-xs pb-1">
            <span className={`w-2 h-2 rounded-full ${isLive ? 'bg-up' : 'bg-down'}`} />
            <span className="text-muted">
              {isLive ? 'Live' : 'Not receiving data'}{staleness !== null && `, last row ${staleness}s ago`}
            </span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 w-full sm:w-auto">
          <span className="text-xs text-muted sm:text-right">
            {bf?.running
              ? `Completing from Zerodha (${lastRun.length} day${lastRun.length === 1 ? '' : 's'} done)`
              : 'Runs automatically at 3:35 pm'}
          </span>
          <button
            onClick={startBackfill}
            disabled={!!bf?.running}
            title="Fill every missing minute, strike, OI and volume for the last 7 trading days from Zerodha's minute history"
            className="pressable flex items-center gap-1.5 px-3.5 h-10 md:h-8 rounded-full border border-border bg-white text-sm font-semibold text-ink2 hover:text-ink hover:border-spvh-400 disabled:opacity-50"
          >
            {bf?.running ? <CircleNotch size={15} className="animate-spin" /> : <ArrowsClockwise size={15} weight="bold" />}
            Complete from Zerodha
          </button>
        </div>
      </div>

      {!bf?.running && lastRun.length > 0 && (
        <div className="bg-surface rounded-panel border border-border px-4 py-3 text-xs space-y-1">
          <div className="font-semibold text-ink text-sm">Last completion run</div>
          {lastRun.map(r => (
            <div key={r.date} className="flex flex-wrap gap-x-3">
              <span className="w-24 shrink-0 text-ink2 tabular-nums">{r.date}</span>
              <span className={r.status === 'ok' ? 'text-up' : r.status === 'error' ? 'text-down' : 'text-muted'}>
                {r.status === 'ok'
                  ? `${(r.inserted ?? 0).toLocaleString('en-IN')} rows added, ${r.filled ?? 0} filled`
                  : r.note}
              </span>
            </div>
          ))}
        </div>
      )}

      <Segmented label="Range" value={range} onChange={setRange} options={RANGES} />

      {/* Status strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { l: 'Rows today', v: status?.rows_today?.toLocaleString('en-IN') ?? '—' },
          { l: 'Last snapshot', v: status?.last_snapshot_at ? fmtTime(status.last_snapshot_at) : '—' },
          { l: 'Contracts tracked', v: status?.contracts ?? '—' },
          { l: 'Expiry', v: status?.expiry ?? '—' },
        ].map(c => (
          <div key={c.l} className="min-w-0 bg-surface rounded-panel border border-border p-3">
            <div className="text-xs font-semibold text-muted mb-1">{c.l}</div>
            <div className="text-lg font-bold text-ink">{c.v}</div>
          </div>
        ))}
        <div className="bg-surface rounded-panel border border-border p-3">
          <div className="text-xs font-semibold text-muted mb-1">Index feed</div>
          <div className={`text-lg font-bold ${status?.index_subscribed ? 'text-up' : 'text-down'}`}>
            {status?.index_subscribed ? 'Subscribed' : 'Not yet'}
          </div>
        </div>
      </div>

      {/* Candle chart */}
      <div className="bg-surface rounded-panel border border-border overflow-hidden">
        <div className="px-3 md:px-4 py-3 border-b border-border flex flex-wrap items-center justify-between gap-x-3">
          <span className="font-bold text-base text-ink">
            NIFTY 50 spot, 1-minute candles {range === 'today' ? '(today, live)' : `(${RANGES.find(r => r.key === range)?.label})`}
          </span>
          <span className="text-xs text-muted">{candles.length} candles</span>
        </div>
        <div className="px-2 md:px-4 pt-3 pb-1">
          <CandleChart candles={candles} showDate={multiDay} />
        </div>
      </div>

      {/* Minute snapshots: collapsed to time + spot, expand for the strike ladder */}
      <div className="bg-surface rounded-panel border border-border overflow-hidden">
        <div className="px-3 md:px-4 py-3 border-b border-border flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5">
          <span className="font-bold text-base text-ink">Minute snapshots, newest first</span>
          <span className="text-xs text-muted">{minutes.length.toLocaleString('en-IN')} minutes · click a minute for its strikes</span>
        </div>
        {minutes.length === 0 && (
          <div className="px-4 py-10 text-center text-muted text-sm">No snapshots in this range yet.</div>
        )}
        <div>
          {visible.map((m, i) => {
            const day = m.t.slice(0, 10);
            const newDay = multiDay && (i === 0 || visible[i - 1].t.slice(0, 10) !== day);
            const isOpen = !!open[m.t];
            return (
              <Fragment key={m.t}>
                {newDay && (
                  <div className="px-4 pt-3 pb-1.5 text-xs font-bold text-ink2 bg-bg/60 border-b border-border">{fmtDay(day)}</div>
                )}
                <button
                  onClick={() => toggle(m.t)}
                  aria-expanded={isOpen}
                  className={`w-full flex items-center gap-3 md:gap-4 px-3 md:px-4 h-12 md:h-10 text-sm border-b border-border/60 text-left
                    transition-colors ${isOpen ? 'bg-spvh-50' : 'hover:bg-ink/[0.025]'}`}
                >
                  <CaretRight size={14} weight="bold"
                    className={`shrink-0 transition-transform duration-200 ${isOpen ? 'rotate-90 text-accent' : 'text-muted'}`} />
                  <span className="w-14 font-semibold text-ink tabular-nums">{m.t.slice(11, 16)}</span>
                  <span className="w-28 md:w-32 text-ink2 tabular-nums">
                    <span className="text-muted text-xs mr-1.5">Spot</span>{m.spot != null ? m.spot.toFixed(2) : '—'}
                  </span>
                  <span className="hidden sm:inline text-xs text-muted">{m.rows} contracts</span>
                  <span className={`ml-auto text-[11px] font-semibold px-2 py-0.5 rounded-full
                    ${m.book > 0 ? 'bg-up/10 text-up' : 'bg-bg text-muted border border-border'}`}
                    title={m.book > 0 ? 'Recorded live: includes bid/ask and order book' : 'Filled from Zerodha history: price, volume and OI only'}>
                    {m.book > 0 ? 'Live + order book' : 'History only'}
                  </span>
                </button>
                {isOpen && <MinuteDetail rows={open[m.t]} />}
              </Fragment>
            );
          })}
        </div>
        {shown < minutes.length && (
          <div className="px-4 py-3 flex justify-center">
            <button onClick={() => setShown(s => s + PAGE)}
              className="pressable px-4 py-1.5 rounded-full border border-border bg-white text-sm font-semibold text-ink2 hover:text-ink hover:border-spvh-400">
              Show {Math.min(PAGE, minutes.length - shown)} more minutes
            </button>
          </div>
        )}
      </div>
    </div></div>
  );
}
