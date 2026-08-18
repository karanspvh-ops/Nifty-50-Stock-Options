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
  trade_id: number; symbol: string; option_symbol?: string;
  direction: string; env: string;
  entry: number; ltp: number | null;
  pnl_pct: number; pnl?: number;
  quantity?: number | null; lot_size?: number | null;
  hard_sl: number; dynamic_sl: number | null; target: number | null;
  status: string; highest_price: number | null;
  entered_at?: string | null;
}
export interface TradeTick {
  trade_id: number;
  ltp: number;
  pnl_pct: number;
  dynamic_sl: number | null;
  hard_sl: number;
  target: number | null;
  peak_pct: number;
  trail_armed: boolean;
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
  activeView:   'dashboard' | 'testing_lab' | 'battle_ground' | 'reports' | 'backtest' | 'early_scalp';
  setFeedHealth:    (h: FeedHealth) => void;
  setHaltStatus:    (s: { halted: boolean; reason: string }) => void;
  setSectorMoves:   (s: Record<string, SectorData>) => void;
  setStockMoves:    (s: Record<string, StockMove>) => void;
  setOpenTrades:    (t: OpenTrade[]) => void;
  updateTradeTick:  (tick: TradeTick) => void;
  addTrade:         (t: OpenTrade) => void;
  removeTrade:      (trade_id: number) => void;
  setSettings:      (s: Partial<Settings>) => void;
  setActiveView:    (v: MarketStore['activeView']) => void;
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
  setFeedHealth:   (h) => set({ feedHealth: h }),
  setHaltStatus:   (s) => set({ haltStatus: s }),
  setSectorMoves:  (s) => set({ sectorMoves: s }),
  setStockMoves:   (s) => set({ stockMoves: s }),
  setOpenTrades:   (t) => set({ openTrades: t }),
  updateTradeTick: (tick) => set((st) => ({
    openTrades: st.openTrades.map(t =>
      t.trade_id === tick.trade_id
        ? {
            ...t,
            ltp:     tick.ltp,
            pnl_pct: tick.pnl_pct,
            hard_sl: tick.hard_sl,
            target:  tick.target,
            pnl:     undefined,
            // dynamic_sl only ever increases — never let a tick lower it
            dynamic_sl: tick.dynamic_sl != null
              ? (t.dynamic_sl != null ? Math.max(t.dynamic_sl, tick.dynamic_sl) : tick.dynamic_sl)
              : t.dynamic_sl,
          }
        : t
    ),
  })),
  addTrade: (t) => set((st) => ({
    openTrades: st.openTrades.some(o => o.trade_id === t.trade_id)
      ? st.openTrades
      : [...st.openTrades, t],
  })),
  removeTrade: (trade_id) => set((st) => ({
    openTrades: st.openTrades.filter(t => t.trade_id !== trade_id),
  })),
  setSettings:     (s) => set((st) => ({ settings: { ...st.settings, ...s } })),
  setActiveView:   (v) => set({ activeView: v }),
}));
