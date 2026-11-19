import { useMarketStore } from '../../store/marketStore';
import type { OpenTrade } from '../../store/marketStore';

const API    = 'http://localhost:8000';

function fmtTime(s: string | null | undefined) {
  if (!s) return '—';
  const m = s.match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : '—';
}

function EnvTag({ env }: { env: string }) {
  const live = env === 'live';
  return (
    <span className={`ml-1.5 inline-flex items-center gap-1 align-middle text-[11px] font-bold px-1.5 py-px rounded-full
      ${live ? 'btn-gold' : 'bg-bg text-muted border border-border'}`}>
      {live && <span className="live-dot w-1.5 h-1.5 rounded-full bg-down" />}
      {live ? 'LIVE' : 'PAPER'}
    </span>
  );
}

function TradeRow({ t, closed }: { t: OpenTrade; closed?: boolean }) {
  const pnlColor = t.pnl_pct >= 0 ? 'text-up' : 'text-down';
  const qtyTotal = (t.quantity ?? 0) * (t.lot_size ?? 0);
  const pnlRs = t.pnl ?? (t.ltp != null ? (t.ltp - t.entry) * qtyTotal : 0);
  return (
    <tr className={`border-b border-border hover:bg-border/30 transition-colors
      ${closed ? 'opacity-50' : ''}`}>
      <td className="px-3 py-2 text-ink font-semibold text-sm whitespace-nowrap">
        {t.symbol}
        <EnvTag env={t.env} />
        {closed && (
          <span className="ml-1.5 text-[11px] px-1 py-0.5 rounded bg-border text-muted font-semibold align-middle">
            CLOSED
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        <span className={`text-xs px-1.5 py-0.5 rounded font-semibold
          ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
          {t.direction.toUpperCase()}
        </span>
      </td>
      <td className="px-3 py-2 text-[12.5px] text-ink font-mono">
        {t.quantity != null && t.lot_size != null
          ? <>{t.quantity}×{t.lot_size}<span className="text-muted ml-1">={qtyTotal}</span></>
          : '—'}
      </td>
      <td className="px-3 py-2 text-xs text-muted">₹{t.entry?.toFixed(2)}</td>
      <td className="px-3 py-2 text-xs text-ink">{t.ltp != null ? `₹${t.ltp.toFixed(2)}` : '—'}</td>
      <td className={`px-3 py-2 text-xs font-semibold ${pnlColor}`}>
        {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct?.toFixed(2)}%
        <span className="text-muted ml-1 text-[12px]">
          ({pnlRs >= 0 ? '+' : ''}₹{Math.round(pnlRs)})
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-down">₹{t.hard_sl?.toFixed(2)}</td>
      <td className="px-3 py-2 text-xs text-accent">
        {t.dynamic_sl ? `₹${t.dynamic_sl.toFixed(2)}` : '—'}
      </td>
      <td className="px-3 py-2 text-muted text-[12px] font-mono">{fmtTime(t.entered_at)}</td>
      <td className="px-3 py-2">
        {closed ? (
          <span className="text-[12px] px-2 py-1 bg-border/50 text-muted rounded">—</span>
        ) : (
          <button
            onClick={async () => {
              const real = t.env === 'live';
              if (!window.confirm(`${real ? 'SELL the live position' : 'Exit the paper trade'} in ${t.symbol}${real ? ' at Zerodha' : ''}?`)) return;
              await fetch(`${API}/api/risk/force-exit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ trade_id: t.trade_id }),
              });
            }}
            className="pressable text-xs font-bold px-3 py-1 rounded-full border border-down/50 text-down bg-white hover:bg-down hover:text-white"
          >
            Exit
          </button>
        )}
      </td>
    </tr>
  );
}

export default function OpenTradesPanel() {
  // Shared live-trades store (fed by the app-wide socket in useTradesSocket)
  const openTrades = useMarketStore(s => s.openTrades);
  const closedList = useMarketStore(s => s.closedTrades);
  const hasAny     = openTrades.length > 0 || closedList.length > 0;

  if (!hasAny) return (
    <div className="bg-surface rounded-panel border border-border p-4">
      <h2 className="text-ink font-bold text-base mb-2">Open Positions</h2>
      <p className="text-muted text-xs">No open trades.</p>
    </div>
  );

  return (
    <div className="bg-surface rounded-panel border border-border p-4">
      <h2 className="text-ink font-bold text-base mb-3">
        Open Positions
        {closedList.length > 0 && (
          <span className="ml-2 text-xs text-muted font-normal">
            ({closedList.length} recently closed)
          </span>
        )}
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="text-muted text-xs font-semibold border-b border-border">
              <th className="px-3 pb-2">Symbol</th>
              <th className="px-3 pb-2">Dir</th>
              <th className="px-3 pb-2">Qty</th>
              <th className="px-3 pb-2">Entry</th>
              <th className="px-3 pb-2">LTP</th>
              <th className="px-3 pb-2">PnL</th>
              <th className="px-3 pb-2">Hard SL</th>
              <th className="px-3 pb-2">Dyn SL</th>
              <th className="px-3 pb-2">Entry time</th>
              <th className="px-3 pb-2">Action</th>
            </tr>
          </thead>
          <tbody>
            {openTrades.map(t => <TradeRow key={t.trade_id} t={t} />)}
            {closedList.map(t => <TradeRow key={`closed-${t.trade_id}`} t={t} closed />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
