import { useEffect, useRef } from 'react';
import { useMarketStore } from '../store/marketStore';

const WS_URL = 'ws://localhost:8000/api/market/ws/market';
const RECONNECT_MS = 3000;

export function useMarketSocket() {
  const ws = useRef<WebSocket | null>(null);
  const setFeedHealth  = useMarketStore(s => s.setFeedHealth);
  const setHaltStatus  = useMarketStore(s => s.setHaltStatus);
  const setSectorMoves = useMarketStore(s => s.setSectorMoves);
  const setStockMoves  = useMarketStore(s => s.setStockMoves);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const connect = () => {
    try {
      ws.current = new WebSocket(WS_URL);

      ws.current.onopen = () => {
        console.log('[WS] Connected to market feed');
        setFeedHealth({ connected: true, last_tick: null, error: '' });
      };

      ws.current.onmessage = (evt) => {
        const data = JSON.parse(evt.data);
        if (data.type === 'market_update') {
          setFeedHealth(data.feed_health);
          setHaltStatus(data.halt_status);
          setSectorMoves(data.sector_moves || {});
          setStockMoves(data.stock_moves  || {});
        }
      };

      ws.current.onclose = () => {
        setFeedHealth({ connected: false, last_tick: null, error: 'Disconnected' });
        reconnectTimer.current = setTimeout(connect, RECONNECT_MS);
      };

      ws.current.onerror = () => {
        setFeedHealth({ connected: false, last_tick: null, error: 'Connection error' });
      };
    } catch (e) {
      reconnectTimer.current = setTimeout(connect, RECONNECT_MS);
    }
  };

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, []);
}
