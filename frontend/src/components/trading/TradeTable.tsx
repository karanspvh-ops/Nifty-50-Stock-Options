import { useEffect, useState, useMemo } from 'react';

const API = 'http://localhost:8000';

type DateRange = 'today' | '7d' | 'all';
type StratFilter = 'ALL' | 'ES' | 'OB' | 'TE';

interface Trade {
  id: number; symbol: string; direction: string; option_symbol: string;
  strike: number | null; expiry: string | null; option_type: string | null;
  entry_price: number; exit_price: number | null;
  live_ltp: number | null;
  quantity: number | null; lot_size: number | null;
  pnl: number; pnl_pct: number;
  status: string; entered_at: string; exited_at: string | null;
  entry_logic: string; exit_logic: string;
}

function fmtTime(s: string | null | undefined) {
  if (!s) return '—';
  const m = s.match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : '—';
}

function fmtDate(s: string) {
  const d = new Date(s);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
}

function getStrategy(entry_logic: string): 'ES' | 'OB' | 'TE' {
  if (entry_logic?.startsWith('[ES]')) return 'ES';
  if (entry_logic?.startsWith('[OB]')) return 'OB';
  return 'TE';
}

function pnlColor(v: number) { return v >= 0 ? 'text-up' : 'text-down'; }
function fmtPnL(v: number) {
  return `${v >= 0 ? '+' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function PnLStat({ label, value, count, color }: { label: string; value: number; count: number; color: string }) {
  return (
    <div className={`flex flex-col px-4 py-2 rounded-lg border ${color}`}>
      <div className="text-[10px] uppercase tracking-wider mb-0.5 opacity-70">{label}</div>
      <div className={`font-bold text-base ${pnlColor(value)}`}>{fmtPnL(value)}</div>
      <div className="text-[10px] opacity-50">{count} trade{count !== 1 ? 's' : ''}</div>
    </div>
  );
}

export default function TradeTable({ env }: { env: 'paper' | 'live' }) {
  const [trades, setTrades]   = useState<Trade[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [range, setRange]     = useState<DateRange>('today');
  const [filter, setFilter]   = useState<StratFilter>('ALL');

  useEffect(() => {
    const load = async () => {
      const res = await fetch(`${API}/api/trades?env=${env}`);
      setTrades(await res.json());
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [env]);

  // ── Date range filter ──────────────────────────────────────────────────────
  const rangedTrades = useMemo(() => {
    if (range === 'all') return trades;
    const cutoff = new Date();
    if (range === 'today') {
      cutoff.setHours(0, 0, 0, 0);
    } else {
      cutoff.setDate(cutoff.getDate() - 7);
      cutoff.setHours(0, 0, 0, 0);
    }
    return trades.filter(t => t.entered_at && new Date(t.entered_at) >= cutoff);
  }, [trades, range]);

  // ── Per-day breakdown ──────────────────────────────────────────────────────
  const byDay = useMemo(() => {
    const map: Record<string, { es: number; ob: number; te: number; net: number; esN: number; obN: number; teN: number; wins: number; total: number }> = {};
    rangedTrades.forEach(t => {
      const date = t.entered_at?.slice(0, 10) ?? 'unknown';
      if (!map[date]) map[date] = { es: 0, ob: 0, te: 0, net: 0, esN: 0, obN: 0, teN: 0, wins: 0, total: 0 };
      const pnl = t.pnl || 0;
      const strat = getStrategy(t.entry_logic);
      if (strat === 'ES') { map[date].es += pnl; map[date].esN++; }
      else if (strat === 'OB') { map[date].ob += pnl; map[date].obN++; }
      else { map[date].te += pnl; map[date].teN++; }
      map[date].net += pnl;
      map[date].total++;
      if (pnl > 0) map[date].wins++;
    });
    return Object.entries(map).sort((a, b) => b[0].localeCompare(a[0]));
  }, [rangedTrades]);

  // ── Strategy-level totals (for the stat cards) ────────────────────────────
  const esTrades = rangedTrades.filter(t => getStrategy(t.entry_logic) === 'ES');
  const obTrades = rangedTrades.filter(t => getStrategy(t.entry_logic) === 'OB');
  const teTrades = rangedTrades.filter(t => getStrategy(t.entry_logic) === 'TE');
  const esPnL  = esTrades.reduce((s, t) => s + (t.pnl || 0), 0);
  const obPnL  = obTrades.reduce((s, t) => s + (t.pnl || 0), 0);
  const tePnL  = teTrades.reduce((s, t) => s + (t.pnl || 0), 0);
  const netPnL = rangedTrades.reduce((s, t) => s + (t.pnl || 0), 0);
  const wins   = rangedTrades.filter(t => (t.pnl || 0) > 0).length;

  // ── Strategy filter on visible rows ───────────────────────────────────────
  const visible = filter === 'ALL' ? rangedTrades : rangedTrades.filter(t => getStrategy(t.entry_logic) === filter);

  const RANGE_TABS: { key: DateRange; label: string }[] = [
    { key: 'today', label: 'Today' },
    { key: '7d',    label: 'Last 7 Days' },
    { key: 'all',   label: 'All Time' },
  ];

  return (
    <div className="bg-surface rounded-xl border border-border p-4 space-y-4">

      {/* ── Date range tabs ── */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Range</span>
        {RANGE_TABS.map(r => (
          <button
            key={r.key}
            onClick={() => setRange(r.key)}
            className={`text-[11px] px-3 py-1 rounded font-semibold transition-colors border ${
              range === r.key
                ? 'bg-accent/20 text-accent border-accent/40'
                : 'bg-border/20 text-muted border-border hover:bg-border/40'
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      {/* ── PnL stat cards ── */}
      <div className="flex flex-wrap gap-3">
        <PnLStat label="Early Scalp [ES]"       value={esPnL} count={esTrades.length} color="border-purple-500/30 bg-purple-500/5" />
        <PnLStat label="Opening Breakout [OB]"   value={obPnL} count={obTrades.length} color="border-blue-500/30 bg-blue-500/5" />
        {teTrades.length > 0 && (
          <PnLStat label="Trading Engine [TE]"   value={tePnL} count={teTrades.length} color="border-border bg-border/10" />
        )}
        <div className="flex flex-col px-4 py-2 rounded-lg border border-border bg-white/5 ml-auto">
          <div className="text-[10px] uppercase tracking-wider mb-0.5 text-muted">Net PnL</div>
          <div className={`font-bold text-xl ${pnlColor(netPnL)}`}>{fmtPnL(netPnL)}</div>
          <div className="text-[10px] text-muted">
            {rangedTrades.length} trades · {rangedTrades.length ? Math.round(wins / rangedTrades.length * 100) : 0}% win
          </div>
        </div>
      </div>

      {/* ── Per-day breakdown (7d + all) ── */}
      {range !== 'today' && byDay.length > 0 && (
        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="text-muted text-[10px] uppercase tracking-wider bg-border/20 border-b border-border">
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2 text-purple-400">ES PnL</th>
                <th className="px-3 py-2 text-blue-400">OB PnL</th>
                <th className="px-3 py-2">Net</th>
                <th className="px-3 py-2">Trades</th>
                <th className="px-3 py-2">Win%</th>
              </tr>
            </thead>
            <tbody>
              {byDay.map(([date, d]) => (
                <tr key={date} className="border-b border-border/40 hover:bg-border/10 text-xs">
                  <td className="px-3 py-2 text-white font-medium">{fmtDate(date)}</td>
                  <td className={`px-3 py-2 font-mono font-semibold ${pnlColor(d.es)}`}>
                    {d.esN > 0 ? fmtPnL(d.es) : <span className="text-muted">—</span>}
                    {d.esN > 0 && <span className="text-muted ml-1 font-normal text-[10px]">{d.esN}t</span>}
                  </td>
                  <td className={`px-3 py-2 font-mono font-semibold ${pnlColor(d.ob)}`}>
                    {d.obN > 0 ? fmtPnL(d.ob) : <span className="text-muted">—</span>}
                    {d.obN > 0 && <span className="text-muted ml-1 font-normal text-[10px]">{d.obN}t</span>}
                  </td>
                  <td className={`px-3 py-2 font-mono font-bold ${pnlColor(d.net)}`}>{fmtPnL(d.net)}</td>
                  <td className="px-3 py-2 text-muted">{d.total}</td>
                  <td className="px-3 py-2 text-muted">{d.total ? Math.round(d.wins / d.total * 100) : 0}%</td>
                </tr>
              ))}
              {/* Totals row */}
              <tr className="bg-border/20 text-xs font-bold border-t border-border">
                <td className="px-3 py-2 text-muted uppercase tracking-wider text-[10px]">Total</td>
                <td className={`px-3 py-2 font-mono ${pnlColor(esPnL)}`}>{fmtPnL(esPnL)}</td>
                <td className={`px-3 py-2 font-mono ${pnlColor(obPnL)}`}>{fmtPnL(obPnL)}</td>
                <td className={`px-3 py-2 font-mono ${pnlColor(netPnL)}`}>{fmtPnL(netPnL)}</td>
                <td className="px-3 py-2 text-muted">{rangedTrades.length}</td>
                <td className="px-3 py-2 text-muted">{rangedTrades.length ? Math.round(wins / rangedTrades.length * 100) : 0}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* ── Strategy filter tabs ── */}
      <div className="flex gap-2">
        {(['ALL', 'ES', 'OB', 'TE'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`text-[11px] px-3 py-1 rounded font-semibold transition-colors ${
              filter === f
                ? f === 'ES' ? 'bg-purple-500/30 text-purple-300 border border-purple-500/50'
                  : f === 'OB' ? 'bg-blue-500/30 text-blue-300 border border-blue-500/50'
                  : 'bg-accent/20 text-accent border border-accent/40'
                : 'bg-border/30 text-muted border border-border hover:bg-border/50'
            }`}
          >
            {f === 'ALL'
              ? `All (${rangedTrades.length})`
              : f === 'ES' ? `Early Scalp (${esTrades.length})`
              : f === 'OB' ? `Opening Breakout (${obTrades.length})`
              : `Engine (${teTrades.length})`}
          </button>
        ))}
      </div>

      {/* ── Trade table ── */}
      {!visible.length ? (
        <p className="text-muted text-xs">No trades for this range.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                <th className="px-3 pb-2">#</th>
                <th className="px-3 pb-2">Strategy</th>
                <th className="px-3 pb-2">Symbol</th>
                <th className="px-3 pb-2">Dir</th>
                <th className="px-3 pb-2">Contract</th>
                <th className="px-3 pb-2">Qty</th>
                <th className="px-3 pb-2">Entry</th>
                <th className="px-3 pb-2">LTP / Exit</th>
                <th className="px-3 pb-2">PnL</th>
                <th className="px-3 pb-2">Status</th>
                {range !== 'today' && <th className="px-3 pb-2">Date</th>}
                <th className="px-3 pb-2">In</th>
                <th className="px-3 pb-2">Out</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(t => {
                const strat = getStrategy(t.entry_logic);
                return (
                  <>
                    <tr
                      key={t.id}
                      className="border-b border-border/50 hover:bg-border/20 cursor-pointer"
                      onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                    >
                      <td className="px-3 py-2 text-muted text-xs">{t.id}</td>
                      <td className="px-3 py-2">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold tracking-wide
                          ${strat === 'ES' ? 'bg-purple-500/20 text-purple-300'
                            : strat === 'OB' ? 'bg-blue-500/20 text-blue-300'
                            : 'bg-border text-muted'}`}>
                          {strat}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-white font-medium text-xs">{t.symbol}</td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-1.5 py-0.5 rounded font-semibold
                          ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                          {t.direction?.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-[11px] text-white font-mono">
                        {t.strike ? `${t.strike} ${t.option_type || ''}` : (t.option_symbol || '—')}
                        {t.expiry && <span className="text-muted ml-1">{t.expiry.slice(5)}</span>}
                      </td>
                      <td className="px-3 py-2 text-[11px] text-white font-mono">
                        {t.quantity != null && t.lot_size != null
                          ? <>{t.quantity}×{t.lot_size}<span className="text-muted ml-1">={t.quantity * t.lot_size}</span></>
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted">₹{t.entry_price?.toFixed(2)}</td>
                      <td className="px-3 py-2 text-xs">
                        {t.status === 'open'
                          ? (t.live_ltp != null
                              ? <span className="text-white">₹{t.live_ltp.toFixed(2)} <span className="text-[9px] text-accent">live</span></span>
                              : <span className="text-muted">—</span>)
                          : (t.exit_price != null ? <span className="text-muted">₹{t.exit_price.toFixed(2)}</span> : '—')}
                      </td>
                      <td className={`px-3 py-2 text-xs font-semibold ${(t.pnl || 0) >= 0 ? 'text-up' : 'text-down'}`}>
                        {(t.pnl || 0) >= 0 ? '+' : ''}₹{(t.pnl || 0).toFixed(2)}
                        <span className="text-muted ml-1">({(t.pnl_pct || 0).toFixed(1)}%)</span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded capitalize
                          ${t.status === 'open'      ? 'bg-accent/20 text-accent' :
                            t.status === 'sl_hit'    ? 'bg-down/20 text-down'    :
                            t.status === 'target'    ? 'bg-up/20 text-up'        :
                            t.status === 'breakeven' ? 'bg-yellow-500/20 text-yellow-400' :
                            'bg-border text-muted'}`}>
                          {t.status}
                        </span>
                      </td>
                      {range !== 'today' && (
                        <td className="px-3 py-2 text-muted text-[10px]">{fmtDate(t.entered_at)}</td>
                      )}
                      <td className="px-3 py-2 text-muted text-[10px] font-mono">{fmtTime(t.entered_at)}</td>
                      <td className="px-3 py-2 text-muted text-[10px] font-mono">{fmtTime(t.exited_at)}</td>
                    </tr>
                    {expanded === t.id && (
                      <tr key={`exp-${t.id}`} className="bg-bg/50">
                        <td colSpan={range !== 'today' ? 13 : 12} className="px-4 py-3 text-xs text-muted leading-relaxed">
                          <p><span className="text-white font-semibold">Entry logic: </span>{t.entry_logic || 'N/A'}</p>
                          <p className="mt-1"><span className="text-white font-semibold">Exit logic: </span>{t.exit_logic || 'N/A'}</p>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
