"""
risk_engine.py — Core risk monitor. Runs as a background thread.

Every second, for every open trade:
  1. Check if feed is connected (halt all if not)
  2. Check portfolio SL kill-switch
  3. Apply hard Trade SL
  4. Apply Target Profit (if set)
  5. Compute and update Dynamic SL
  6. Trigger exit if any condition met

Exit is handled by calling execute_exit() which will be implemented
by Module 5 (Order Execution). For now it updates the DB directly
(paper mode) or calls Angel One API (live mode).
"""

import threading, time
from datetime import datetime
from typing import Optional, Callable
from sqlalchemy.orm import Session as DBSession

from backend.database import (
    Session, Trade, TradingSession, TradeStatus,
    TradeEnv, TradeDirection
)
from backend.core.market_state   import market
from backend.core.session_manager import (
    check_portfolio_sl, update_portfolio_balance,
    update_session_pnl, update_session_status
)
from backend.core.dynamic_sl     import compute_dynamic_sl, should_exit_breakeven
from backend.core.settings_manager import get_settings
from backend.database import SessionStatus

import logging
log = logging.getLogger("risk_engine")

CHECK_INTERVAL = 1.0   # seconds between risk checks


class RiskEngine:
    def __init__(self):
        self._running   = False
        self._thread: Optional[threading.Thread] = None
        # External callback — Module 5 will inject its execute_exit function
        self._exit_callback: Optional[Callable] = None

    def register_exit_callback(self, fn: Callable):
        """
        Module 5 calls this to register its order execution function.
        Signature: fn(trade_id: int, exit_price: float, reason: str, env: TradeEnv)
        """
        self._exit_callback = fn

    # ── Main loop ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="RiskEngine")
        self._thread.start()
        log.info("[RISK] Engine started.")
        print("[RISK] Engine started.")

    def stop(self):
        self._running = False
        log.info("[RISK] Engine stopped.")
        print("[RISK] Engine stopped.")

    def _loop(self):
        while self._running:
            try:
                self._run_checks()
            except Exception as e:
                log.error(f"[RISK] Loop error: {e}")
                print(f"[RISK] Loop error: {e}")
            time.sleep(CHECK_INTERVAL)

    def _run_checks(self):
        # ── Guard 1: feed must be connected ───────────────────────────────────
        if not market.is_feed_connected():
            return   # trading already halted by market_state

        # ── Guard 2: portfolio kill-switch ────────────────────────────────────
        for env in (TradeEnv.PAPER, TradeEnv.LIVE):
            if check_portfolio_sl(env):
                self._kill_all_open_trades(env, reason="Portfolio SL breached")
                return

        # ── Per-trade checks ──────────────────────────────────────────────────
        db: DBSession = Session()
        try:
            open_trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN)
                .all()
            )
            for trade in open_trades:
                try:
                    self._check_trade(db, trade)
                except Exception as e:
                    log.error(f"[RISK] Trade #{trade.id} check error: {e}")
        finally:
            db.close()

    # ── Per-trade risk check ──────────────────────────────────────────────────

    def _check_trade(self, db: DBSession, trade: Trade):
        # Opening Breakout trades are managed by their own engine (custom
        # 10% SL / trail / 50% target). Skip them here.
        if (trade.entry_logic or "").startswith("[OB]"):
            return

        ltp = self._trade_ltp(trade)   # OPTION premium, not the underlying
        if ltp is None:
            return

        settings   = get_settings()
        direction  = trade.direction   # "call" or "put"
        entry      = trade.entry_price
        current_sl = trade.dynamic_sl_price or trade.trade_sl_price

        # ── Update highest price seen (for trailing reference) ─────────────────
        # Floor at entry_price so highest_price is always >= entry when first set
        candidate = max(ltp, trade.entry_price or ltp)
        if trade.highest_price is None or candidate > trade.highest_price:
            trade.highest_price = candidate
            db.commit()

        # ── 1. Hard trade SL hit ───────────────────────────────────────────────
        hard_sl = round(entry * (1 - trade.trade_sl_pct / 100), 2)
        if ltp <= hard_sl:
            self._trigger_exit(db, trade, ltp, TradeStatus.SL_HIT,
                               f"Hard SL hit at {ltp:.2f} (SL: {hard_sl:.2f})")
            return

        # ── 2. Target profit hit ───────────────────────────────────────────────
        if trade.target_price and ltp >= trade.target_price:
            self._trigger_exit(db, trade, ltp, TradeStatus.TARGET,
                               f"Target hit at {ltp:.2f} (target: {trade.target_price:.2f})")
            return

        # ── 3. Dynamic SL engine ───────────────────────────────────────────────
        # ES trades manage their own trail/SL in early_scalp._manage — skip here
        # to avoid conflicting with that logic.
        is_es = (trade.entry_logic or "").startswith("[ES]")
        if not is_es and settings.get("dynamic_sl_enabled", True):
            underlying_token = self._get_token_for_symbol(trade.symbol)
            if underlying_token:
                decision = compute_dynamic_sl(
                    token          = underlying_token,
                    direction      = direction,
                    entry_price    = entry,
                    current_ltp    = ltp,
                    trade_sl_pct   = trade.trade_sl_pct,
                    current_dyn_sl = trade.dynamic_sl_price,
                )

                # Update dynamic SL in DB if it changed
                if decision.new_sl_price != trade.dynamic_sl_price:
                    trade.dynamic_sl_price = decision.new_sl_price
                    snap = dict(trade.indicators_snapshot or {})
                    snap["last_sl_update"] = {
                        "ts":       datetime.utcnow().isoformat(),
                        "sl":       decision.new_sl_price,
                        "lock":     decision.lock_type,
                        "prob":     decision.prob_continuation,
                        "reason":   decision.reasoning,
                        "vp":       decision.vp_summary,
                    }
                    trade.indicators_snapshot = snap
                    db.commit()

                # Break-even exit
                if should_exit_breakeven(entry, ltp, trade.dynamic_sl_price):
                    self._trigger_exit(db, trade, ltp, TradeStatus.BREAKEVEN,
                                       "Price returned to entry after profit lock — break-even exit")
                    return

                # Dynamic SL hit
                if ltp <= decision.new_sl_price and decision.lock_type != "initial":
                    self._trigger_exit(db, trade, ltp, TradeStatus.SL_HIT,
                                       f"Dynamic SL hit at {ltp:.2f} | {decision.reasoning}")
                    return

    # ── Exit trigger ──────────────────────────────────────────────────────────

    def _trigger_exit(
        self,
        db:     DBSession,
        trade:  Trade,
        price:  float,
        status: TradeStatus,
        reason: str,
    ):
        pnl     = (price - trade.entry_price) * trade.quantity * trade.lot_size
        pnl_pct = ((price - trade.entry_price) / trade.entry_price) * 100

        from backend.core.clock import now_ist
        trade.status     = status
        trade.exit_price = price
        trade.exited_at  = now_ist()
        trade.pnl        = round(pnl, 2)
        trade.pnl_pct    = round(pnl_pct, 2)
        trade.exit_logic = reason
        db.commit()

        # Update portfolio balance
        settings        = get_settings()
        current_funds   = settings.get("available_funds", 0)
        new_balance     = current_funds + pnl
        update_portfolio_balance(trade.env, new_balance)
        update_session_pnl(trade.env, pnl)

        print(f"[RISK] EXIT trade #{trade.id} | {trade.symbol} | "
              f"PnL: ₹{pnl:.2f} ({pnl_pct:.1f}%) | Reason: {reason}")

        # Notify Module 5 to place actual exit order (live mode)
        if self._exit_callback:
            try:
                self._exit_callback(
                    trade_id   = trade.id,
                    exit_price = price,
                    reason     = reason,
                    env        = trade.env,
                )
            except Exception as e:
                print(f"[RISK] Exit callback error: {e}")

    # ── Portfolio kill ────────────────────────────────────────────────────────

    def _kill_all_open_trades(self, env: TradeEnv, reason: str):
        db: DBSession = Session()
        try:
            open_trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            for trade in open_trades:
                ltp = self._trade_ltp(trade) or trade.entry_price   # option premium
                self._trigger_exit(db, trade, ltp, TradeStatus.KILLED, reason)

            update_session_status(env, SessionStatus.KILLED)
            market.halt_trading(reason)
            print(f"[RISK] PORTFOLIO SL — all {env} trades killed. Reason: {reason}")
        finally:
            db.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_token_for_symbol(symbol: str) -> Optional[str]:
        from backend.core.stock_universe import get_token
        return get_token(symbol)

    @staticmethod
    def _trade_ltp(trade: Trade) -> Optional[float]:
        """Current price of the trade's OPTION premium (not the underlying)."""
        from backend.core.order_executor import current_premium
        return current_premium(trade)

    # ── Manual risk overrides (called from API) ───────────────────────────────

    def force_exit_trade(self, trade_id: int, reason: str = "Manual exit") -> dict:
        db: DBSession = Session()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).first()
            if not trade:
                return {"error": "Trade not found"}
            if trade.status != TradeStatus.OPEN:
                return {"error": f"Trade is already {trade.status}"}

            ltp = self._trade_ltp(trade)            # OPTION premium
            if ltp is None:
                return {"error": "Option LTP unavailable — feed may be disconnected"}

            self._trigger_exit(db, trade, ltp, TradeStatus.CLOSED, reason)
            return {"status": "exited", "trade_id": trade_id, "exit_price": ltp}
        finally:
            db.close()

    def get_open_risk_snapshot(self) -> list:
        """Return risk status of all open trades (for dashboard)."""
        db: DBSession = Session()
        try:
            open_trades = db.query(Trade).filter(Trade.status == TradeStatus.OPEN).all()
            result = []
            for t in open_trades:
                ltp      = self._trade_ltp(t)        # OPTION premium
                pnl_pct  = (((ltp or t.entry_price) - t.entry_price) / t.entry_price * 100) if ltp else 0
                qty_total = (t.quantity or 0) * (t.lot_size or 0)
                pnl_rs    = round((((ltp or t.entry_price) - t.entry_price) * qty_total), 2) if ltp else 0
                result.append({
                    "trade_id":       t.id,
                    "symbol":         t.symbol,
                    "option_symbol":  t.option_symbol,
                    "direction":      t.direction,
                    "env":            t.env,
                    "entry":          t.entry_price,
                    "ltp":            ltp,
                    "pnl_pct":        round(pnl_pct, 2),
                    "pnl":            pnl_rs,
                    "quantity":       t.quantity,
                    "lot_size":       t.lot_size,
                    "hard_sl":        round(t.entry_price * (1 - t.trade_sl_pct / 100), 2),
                    "dynamic_sl":     t.dynamic_sl_price,
                    "target":         t.target_price,
                    "status":         t.status,
                    "highest_price":  t.highest_price,
                    "entered_at":     str(t.entered_at) if t.entered_at else None,
                })
            return result
        finally:
            db.close()


# ── Global singleton ──────────────────────────────────────────────────────────
risk_engine = RiskEngine()
