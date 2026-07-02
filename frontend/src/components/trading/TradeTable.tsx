import { useEffect, useState, useMemo } from 'react';

const API = 'http://localhost:8000';

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

  // ── Always show today's trades only ───────────────────────────────────────
  const rangedTrades = useMemo(() => {
    const cutoff = new Date();
    cutoff.setHours(0, 0, 0, 0);
    return trades.filter(t => t.entered_at && new Date(t.entered_at) >= cutoff);
  }, [trades]);

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

  return (
    <div className="bg-surface rounded-xl border border-border p-4 space-y-4">

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

      {/* Per-day breakdown removed — this view always shows today only */}

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
                      <td className="px-3 py-2 text-muted text-[10px] font-mono">{fmtTime(t.entered_at)}</td>
                      <td className="px-3 py-2 text-muted text-[10px] font-mono">{fmtTime(t.exited_at)}</td>
                    </tr>
                    {expanded === t.id && (
                      <tr key={`exp-${t.id}`} className="bg-bg/50">
                        <td colSpan={12} className="px-4 py-3 text-xs text-muted leading-relaxed">
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
