import { useEffect, useRef, useState } from 'react';
import { useMarketStore } from '../../store/marketStore';
import type { OpenTrade } from '../../store/marketStore';

const API    = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/api/market/ws/trades';
const RECONNECT_MS   = 3000;
const CLOSED_LINGER_MS = 60_000;

type ClosedTrade = OpenTrade & { _closed_at: number };

function fmtTime(s: string | null | undefined) {
  if (!s) return '—';
  const m = s.match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : '—';
}

function TradeRow({ t, closed }: { t: OpenTrade; closed?: boolean }) {
  const pnlColor = t.pnl_pct >= 0 ? 'text-up' : 'text-down';
  const qtyTotal = (t.quantity ?? 0) * (t.lot_size ?? 0);
  const pnlRs = t.pnl ?? (t.ltp != null ? (t.ltp - t.entry) * qtyTotal : 0);
  return (
    <tr className={`border-b border-border hover:bg-border/30 transition-colors
      ${closed ? 'opacity-50' : ''}`}>
      <td className="px-3 py-2 text-white font-medium text-xs">
        {t.symbol}
        {closed && (
          <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-border text-muted font-semibold align-middle">
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
      <td className="px-3 py-2 text-[11px] text-white font-mono">
        {t.quantity != null && t.lot_size != null
          ? <>{t.quantity}×{t.lot_size}<span className="text-muted ml-1">={qtyTotal}</span></>
          : '—'}
      </td>
      <td className="px-3 py-2 text-xs text-muted">₹{t.entry?.toFixed(2)}</td>
      <td className="px-3 py-2 text-xs text-white">{t.ltp != null ? `₹${t.ltp.toFixed(2)}` : '—'}</td>
      <td className={`px-3 py-2 text-xs font-semibold ${pnlColor}`}>
        {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct?.toFixed(2)}%
        <span className="text-muted ml-1 text-[10px]">
          ({pnlRs >= 0 ? '+' : ''}₹{Math.round(pnlRs)})
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-down">₹{t.hard_sl?.toFixed(2)}</td>
      <td className="px-3 py-2 text-xs text-accent">
        {t.dynamic_sl ? `₹${t.dynamic_sl.toFixed(2)}` : '—'}
      </td>
      <td className="px-3 py-2 text-muted text-[10px] font-mono">{fmtTime(t.entered_at)}</td>
      <td className="px-3 py-2">
        {closed ? (
          <span className="text-[10px] px-2 py-1 bg-border/50 text-muted rounded">—</span>
        ) : (
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
        )}
      </td>
    </tr>
  );
}

export default function OpenTradesPanel() {
  const { openTrades, setOpenTrades, updateTradeTick, removeTrade, addTrade, settings } = useMarketStore();
  const ws             = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closeTimers    = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
  const [closedTrades, setClosedTrades] = useState<Map<number, ClosedTrade>>(new Map());

  useEffect(() => {
    const connect = () => {
      try {
        ws.current = new WebSocket(WS_URL);

        ws.current.onmessage = (evt) => {
          const msg = JSON.parse(evt.data);

          if (msg.type === 'snapshot') {
            setOpenTrades(msg.trades);

          } else if (msg.type === 'trade_opened') {
            addTrade({
              trade_id:      msg.trade_id,
              symbol:        msg.symbol,
              option_symbol: msg.option_symbol,
              direction:     msg.direction,
              env:           msg.env,
              entry:         msg.entry,
              ltp:           null,
              pnl_pct:       0,
              quantity:      msg.quantity,
              lot_size:      msg.lot_size,
              hard_sl:       msg.hard_sl,
              dynamic_sl:    null,
              target:        msg.target,
              status:        'open',
              highest_price: null,
              entered_at:    msg.entered_at,
            });

          } else if (msg.type === 'trade_tick') {
            updateTradeTick(msg);

          } else if (msg.type === 'trade_closed') {
            // Capture the trade before removing so we can linger it for 60s
            const trade = useMarketStore.getState().openTrades.find(t => t.trade_id === msg.trade_id);
            if (trade) {
              const closed: ClosedTrade = { ...trade, _closed_at: Date.now() };
              setClosedTrades(prev => new Map(prev).set(msg.trade_id, closed));
              // Clear any prior timer for this trade (shouldn't happen, but safety)
              if (closeTimers.current.has(msg.trade_id)) {
                clearTimeout(closeTimers.current.get(msg.trade_id));
              }
              const timer = setTimeout(() => {
                setClosedTrades(prev => {
                  const next = new Map(prev);
                  next.delete(msg.trade_id);
                  return next;
                });
                closeTimers.current.delete(msg.trade_id);
              }, CLOSED_LINGER_MS);
              closeTimers.current.set(msg.trade_id, timer);
            }
            removeTrade(msg.trade_id);
          }
        };

        ws.current.onclose = () => {
          reconnectTimer.current = setTimeout(connect, RECONNECT_MS);
        };

        ws.current.onerror = () => {
          ws.current?.close();
        };
      } catch {
        reconnectTimer.current = setTimeout(connect, RECONNECT_MS);
      }
    };

    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
      closeTimers.current.forEach(t => clearTimeout(t));
    };
  }, []);

  const closedList = Array.from(closedTrades.values());
  const hasAny     = openTrades.length > 0 || closedList.length > 0;

  if (!hasAny) return (
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
        {closedList.length > 0 && (
          <span className="ml-2 text-xs text-muted font-normal">
            ({closedList.length} recently closed)
          </span>
        )}
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
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
