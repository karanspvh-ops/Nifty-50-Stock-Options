import { useEffect } from 'react';
import { useMarketStore } from '../store/marketStore';

const WS_URL       = 'ws://localhost:8000/api/market/ws/trades';
const RECONNECT_MS = 1000;
// The backend sends a snapshot every second, so silence for this long means the
// socket is dead even though the browser hasn't noticed (sleep, mobile data drop).
const STALE_MS     = 5000;

/**
 * One app-wide live-trades connection. Dashboard, Early Scalp and Testing Lab all
 * read the same store, so a trade looks identical (and equally current) on every
 * screen, and switching screens never drops or resets anything.
 */
export function useTradesSocket() {
  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let lastMsg = Date.now();
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      try {
        ws = new WebSocket(WS_URL);
        ws.onopen    = () => { lastMsg = Date.now(); };
        ws.onmessage = (evt) => {
          lastMsg = Date.now();
          const msg = JSON.parse(evt.data);
          const st  = useMarketStore.getState();
          if (msg.type === 'snapshot')          st.applyTradeSnapshot(msg.trades);
          else if (msg.type === 'trade_opened') st.applyTradeOpened({
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
            entry_logic:   msg.entry_logic,
          });
          else if (msg.type === 'trade_tick')   st.updateTradeTick(msg);
          else if (msg.type === 'trade_closed') st.applyTradeClosed(msg);
        };
        ws.onclose = () => { if (!stopped) reconnect = setTimeout(connect, RECONNECT_MS); };
        ws.onerror = () => { ws?.close(); };
      } catch {
        reconnect = setTimeout(connect, RECONNECT_MS);
      }
    };
    connect();

    // Watchdog + linger pruning, once a second
    const tick = setInterval(() => {
      useMarketStore.getState().pruneClosedTrades();
      if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastMsg > STALE_MS) ws.close();
    }, 1000);

    // Coming back to the tab: reconnect immediately rather than waiting for a timer
    const onVisible = () => {
      if (document.visibilityState === 'visible' &&
          (!ws || ws.readyState === WebSocket.CLOSED)) { clearTimeout(reconnect); connect(); }
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      stopped = true;
      clearTimeout(reconnect);
      clearInterval(tick);
      document.removeEventListener('visibilitychange', onVisible);
      ws?.close();
    };
  }, []);
}
