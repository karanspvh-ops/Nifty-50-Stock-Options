import { create } from 'zustand';

export interface SectorData {
  pct_change: number;
  direction: 'up' | 'down' | 'flat';
  stocks: StockMove[];
}
export interface StockMove {
  token: string; symbol: string;
  pct_change: number; direction: string; ltp: number;
}
export interface FeedHealth {
  connected: boolean; last_tick: string | null; error: string;
}
export interface OpenTrade {
  trade_id: number; symbol: string; direction: string; env: string;
  entry: number; ltp: number | null; pnl_pct: number;
  hard_sl: number; dynamic_sl: number | null; target: number | null;
  status: string; highest_price: number | null;
}
export interface Settings {
  is_live: boolean; available_funds: number;
  target_profit_pct: number; trade_sl_pct: number;
  portfolio_sl_pct: number; dynamic_sl_enabled: boolean;
  active_index: string;
}

interface MarketStore {
  feedHealth:   FeedHealth;
  haltStatus:   { halted: boolean; reason: string };
  sectorMoves:  Record<string, SectorData>;
  stockMoves:   Record<string, StockMove>;
  openTrades:   OpenTrade[];
  settings:     Settings;
  activeView:   'dashboard' | 'testing_lab' | 'battle_ground' | 'reports' | 'backtest';
  setFeedHealth:   (h: FeedHealth) => void;
  setHaltStatus:   (s: { halted: boolean; reason: string }) => void;
  setSectorMoves:  (s: Record<string, SectorData>) => void;
  setStockMoves:   (s: Record<string, StockMove>) => void;
  setOpenTrades:   (t: OpenTrade[]) => void;
  setSettings:     (s: Partial<Settings>) => void;
  setActiveView:   (v: MarketStore['activeView']) => void;
}

export const useMarketStore = create<MarketStore>((set) => ({
  feedHealth:  { connected: false, last_tick: null, error: '' },
  haltStatus:  { halted: false, reason: '' },
  sectorMoves: {},
  stockMoves:  {},
  openTrades:  [],
  settings: {
    is_live: false, available_funds: 0, target_profit_pct: 0,
    trade_sl_pct: 5, portfolio_sl_pct: 10,
    dynamic_sl_enabled: true, active_index: 'NIFTY50',
  },
  activeView: 'dashboard',
  setFeedHealth:  (h) => set({ feedHealth: h }),
  setHaltStatus:  (s) => set({ haltStatus: s }),
  setSectorMoves: (s) => set({ sectorMoves: s }),
  setStockMoves:  (s) => set({ stockMoves: s }),
  setOpenTrades:  (t) => set({ openTrades: t }),
  setSettings:    (s) => set((st) => ({ settings: { ...st.settings, ...s } })),
  setActiveView:  (v) => set({ activeView: v }),
}));
