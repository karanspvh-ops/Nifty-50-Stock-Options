import { useEffect, useState } from 'react';

const API = 'http://localhost:8000';

interface Trade {
  id: number; symbol: string; direction: string; option_symbol: string;
  entry_price: number; exit_price: number | null; pnl: number; pnl_pct: number;
  status: string; entered_at: string; exited_at: string | null;
  entry_logic: string; exit_logic: string;
}

export default function TradeTable({ env }: { env: 'paper' | 'live' }) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    const load = async () => {
      const res = await fetch(`${API}/api/trades?env=${env}`);
      setTrades(await res.json());
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [env]);

  const totalPnL = trades.reduce((sum, t) => sum + (t.pnl || 0), 0);
  const wins     = trades.filter(t => (t.pnl || 0) > 0).length;

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      {/* Summary bar */}
      <div className="flex items-center gap-6 mb-4">
        <div>
          <div className="text-muted text-[10px] uppercase">Total Trades</div>
          <div className="text-white font-bold">{trades.length}</div>
        </div>
        <div>
          <div className="text-muted text-[10px] uppercase">Win Rate</div>
          <div className="text-white font-bold">
            {trades.length ? Math.round(wins / trades.length * 100) : 0}%
          </div>
        </div>
        <div>
          <div className="text-muted text-[10px] uppercase">Net PnL</div>
          <div className={`font-bold text-lg ${totalPnL >= 0 ? 'text-up' : 'text-down'}`}>
            {totalPnL >= 0 ? '+' : ''}₹{totalPnL.toFixed(2)}
          </div>
        </div>
      </div>

      {/* Table */}
      {!trades.length ? (
        <p className="text-muted text-xs">No trades recorded yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                <th className="px-3 pb-2">#</th>
                <th className="px-3 pb-2">Symbol</th>
                <th className="px-3 pb-2">Type</th>
                <th className="px-3 pb-2">Entry</th>
                <th className="px-3 pb-2">Exit</th>
                <th className="px-3 pb-2">PnL</th>
                <th className="px-3 pb-2">Status</th>
                <th className="px-3 pb-2">Time</th>
              </tr>
            </thead>
            <tbody>
              {trades.map(t => (
                <>
                  <tr
                    key={t.id}
                    className="border-b border-border/50 hover:bg-border/20 cursor-pointer"
                    onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                  >
                    <td className="px-3 py-2 text-muted text-xs">{t.id}</td>
                    <td className="px-3 py-2 text-white font-medium text-xs">{t.symbol}</td>
                    <td className="px-3 py-2">
                      <span className={`text-xs px-1.5 py-0.5 rounded font-semibold
                        ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                        {t.direction?.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted">₹{t.entry_price?.toFixed(2)}</td>
                    <td className="px-3 py-2 text-xs text-muted">
                      {t.exit_price ? `₹${t.exit_price.toFixed(2)}` : '—'}
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
                    <td className="px-3 py-2 text-muted text-[10px]">
                      {new Date(t.entered_at).toLocaleTimeString()}
                    </td>
                  </tr>
                  {expanded === t.id && (
                    <tr key={`exp-${t.id}`} className="bg-bg/50">
                      <td colSpan={8} className="px-4 py-3 text-xs text-muted leading-relaxed">
                        <p><span className="text-white font-semibold">Entry logic: </span>{t.entry_logic || 'N/A'}</p>
                        <p className="mt-1"><span className="text-white font-semibold">Exit logic: </span>{t.exit_logic || 'N/A'}</p>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
