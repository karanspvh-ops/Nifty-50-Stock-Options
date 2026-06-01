import { useEffect } from 'react';
import { useMarketStore } from '../../store/marketStore';
import type { OpenTrade } from '../../store/marketStore';

const API = 'http://localhost:8000';

function TradeRow({ t }: { t: OpenTrade }) {
  const pnlColor = t.pnl_pct >= 0 ? 'text-up' : 'text-down';
  return (
    <tr className="border-b border-border hover:bg-border/30 transition-colors">
      <td className="px-3 py-2 text-white font-medium text-xs">{t.symbol}</td>
      <td className="px-3 py-2">
        <span className={`text-xs px-1.5 py-0.5 rounded font-semibold
          ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
          {t.direction.toUpperCase()}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-muted">₹{t.entry?.toFixed(2)}</td>
      <td className="px-3 py-2 text-xs text-white">₹{t.ltp?.toFixed(2) ?? '—'}</td>
      <td className={`px-3 py-2 text-xs font-semibold ${pnlColor}`}>
        {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct?.toFixed(2)}%
      </td>
      <td className="px-3 py-2 text-xs text-down">₹{t.hard_sl?.toFixed(2)}</td>
      <td className="px-3 py-2 text-xs text-accent">
        {t.dynamic_sl ? `₹${t.dynamic_sl.toFixed(2)}` : '—'}
      </td>
      <td className="px-3 py-2">
        <button
          onClick={async () => {
            await fetch(`${API}/api/risk/force-exit`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ trade_id: t.trade_id }),
            });
          }}
          className="text-[10px] px-2 py-1 bg-down/20 border border-down text-down rounded hover:bg-down/40"
        >
          EXIT
        </button>
      </td>
    </tr>
  );
}

export default function OpenTradesPanel() {
  const { openTrades, setOpenTrades, settings } = useMarketStore();

  useEffect(() => {
    const poll = async () => {
      const res = await fetch(`${API}/api/risk/snapshot`);
      const data = await res.json();
      setOpenTrades(data);
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  if (!openTrades.length) return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <h2 className="text-white font-semibold text-sm mb-2">Open Positions</h2>
      <p className="text-muted text-xs">No open trades.</p>
    </div>
  );

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <h2 className="text-white font-semibold text-sm mb-3">
        Open Positions
        <span className={`ml-2 text-xs font-semibold px-1.5 py-0.5 rounded
          ${settings.is_live ? 'bg-down/20 text-down' : 'bg-accent/20 text-accent'}`}>
          {settings.is_live ? 'LIVE' : 'PAPER'}
        </span>
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
              <th className="px-3 pb-2">Symbol</th>
              <th className="px-3 pb-2">Dir</th>
              <th className="px-3 pb-2">Entry</th>
              <th className="px-3 pb-2">LTP</th>
              <th className="px-3 pb-2">PnL%</th>
              <th className="px-3 pb-2">Hard SL</th>
              <th className="px-3 pb-2">Dyn SL</th>
              <th className="px-3 pb-2">Action</th>
            </tr>
          </thead>
          <tbody>
            {openTrades.map(t => <TradeRow key={t.trade_id} t={t} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
