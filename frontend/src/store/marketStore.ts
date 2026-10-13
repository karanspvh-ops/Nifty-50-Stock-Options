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
  entry_logic?: string | null;
}
/** A trade that just exited; kept on screen (greyed) for CLOSED_LINGER_MS. */
export interface ClosedTrade extends OpenTrade {
  closed_at:    number;          // Date.now() when we learned it closed
  exit_price?:  number | null;
  exit_reason?: string | null;
}
export const CLOSED_LINGER_MS = 30_000;
// A just-opened trade is kept even if an older in-flight snapshot lacks it.
const OPEN_GRACE_MS = 4_000;

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
  closedTrades: ClosedTrade[];
  tradesVersion: number;   // bumps on every open/close so DB-backed tables refetch at once
  settings:     Settings;
  activeView:   'dashboard' | 'testing_lab' | 'battle_ground' | 'reports' | 'nifty_data' | 'early_scalp';
  setFeedHealth:    (h: FeedHealth) => void;
  setHaltStatus:    (s: { halted: boolean; reason: string }) => void;
  setSectorMoves:   (s: Record<string, SectorData>) => void;
  setStockMoves:    (s: Record<string, StockMove>) => void;
  setOpenTrades:    (t: OpenTrade[]) => void;
  updateTradeTick:  (tick: TradeTick) => void;
  addTrade:         (t: OpenTrade) => void;
  removeTrade:      (trade_id: number) => void;
  applyTradeSnapshot: (trades: OpenTrade[]) => void;
  applyTradeOpened:   (t: OpenTrade) => void;
  applyTradeClosed:   (msg: { trade_id: number; exit_price?: number; pnl?: number;
                              pnl_pct?: number; status?: string; exit_logic?: string }) => void;
  pruneClosedTrades:  () => void;
  setSettings:      (s: Partial<Settings>) => void;
  setActiveView:    (v: MarketStore['activeView']) => void;
}

// When each trade appeared locally (non-reactive bookkeeping for OPEN_GRACE_MS)
const openedAt = new Map<number, number>();

function toClosed(t: OpenTrade, extra: Partial<ClosedTrade> = {}): ClosedTrade {
  const exit = extra.exit_price ?? t.ltp ?? null;
  return {
    ...t, ...extra,
    status:      'closed',
    ltp:         exit,
    closed_at:   Date.now(),
    exit_price:  exit,
  };
}

export const useMarketStore = create<MarketStore>((set, get) => ({
  feedHealth:  { connected: false, last_tick: null, error: '' },
  haltStatus:  { halted: false, reason: '' },
  sectorMoves: {},
  stockMoves:  {},
  openTrades:  [],
  closedTrades: [],
  tradesVersion: 0,
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
            pnl:     Math.round((tick.ltp - t.entry) * (t.quantity ?? 0) * (t.lot_size ?? 0) * 100) / 100,
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
  applyTradeSnapshot: (snap) => set((st) => {
    const now       = Date.now();
    const closedIds = new Set(st.closedTrades.map(c => c.trade_id));
    const snapIds   = new Set(snap.map(t => t.trade_id));
    const prevById  = new Map(st.openTrades.map(t => [t.trade_id, t]));

    // Snapshot is authoritative for what is open; ignore anything we already closed.
    const open = snap.filter(t => !closedIds.has(t.trade_id)).map(t => {
      const prev = prevById.get(t.trade_id);
      if (!prev) { if (!openedAt.has(t.trade_id)) openedAt.set(t.trade_id, now); return t; }
      return {
        ...t,
        // dynamic SL only ever rises; a snapshot must not lower what a tick showed
        dynamic_sl: t.dynamic_sl != null && prev.dynamic_sl != null
          ? Math.max(t.dynamic_sl, prev.dynamic_sl) : (t.dynamic_sl ?? prev.dynamic_sl),
      };
    });

    // A trade we showed as open but the snapshot no longer lists has closed
    // (we missed its close event): move it to the greyed-out list, don't just drop it.
    const newlyClosed: ClosedTrade[] = [];
    for (const t of st.openTrades) {
      if (snapIds.has(t.trade_id)) continue;
      if (now - (openedAt.get(t.trade_id) ?? 0) < OPEN_GRACE_MS) { open.push(t); continue; }
      newlyClosed.push(toClosed(t));
    }
    if (!newlyClosed.length && open.length === st.openTrades.length &&
        open.every((t, i) => t === st.openTrades[i])) return {};
    return {
      openTrades:   open,
      closedTrades: [...st.closedTrades, ...newlyClosed],
      tradesVersion: newlyClosed.length ? st.tradesVersion + 1 : st.tradesVersion,
    };
  }),
  applyTradeOpened: (t) => set((st) => {
    if (st.openTrades.some(o => o.trade_id === t.trade_id) ||
        st.closedTrades.some(c => c.trade_id === t.trade_id)) return {};
    openedAt.set(t.trade_id, Date.now());
    return { openTrades: [...st.openTrades, t], tradesVersion: st.tradesVersion + 1 };
  }),
  applyTradeClosed: (msg) => set((st) => {
    if (st.closedTrades.some(c => c.trade_id === msg.trade_id)) return {};   // duplicate push
    const t = st.openTrades.find(o => o.trade_id === msg.trade_id);
    if (!t) return { tradesVersion: st.tradesVersion + 1 };
    const closed = toClosed(t, {
      exit_price:  msg.exit_price ?? t.ltp,
      pnl:         msg.pnl ?? t.pnl,
      pnl_pct:     msg.pnl_pct ?? t.pnl_pct,
      exit_reason: msg.exit_logic ?? null,
    });
    return {
      openTrades:   st.openTrades.filter(o => o.trade_id !== msg.trade_id),
      closedTrades: [...st.closedTrades, closed],
      tradesVersion: st.tradesVersion + 1,
    };
  }),
  pruneClosedTrades: () => {
    const now  = Date.now();
    const keep = get().closedTrades.filter(c => now - c.closed_at < CLOSED_LINGER_MS);
    if (keep.length !== get().closedTrades.length) set({ closedTrades: keep });
  },
  setSettings:     (s) => set((st) => ({ settings: { ...st.settings, ...s } })),
  setActiveView:   (v) => set({ activeView: v }),
}));
