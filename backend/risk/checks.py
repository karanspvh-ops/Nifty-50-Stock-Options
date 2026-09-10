"""checks.py — Per-trade and portfolio-level risk checks.

Edit this file to change:
  - The OB/ES skip logic
  - How highest_price is tracked
  - Hard SL / target hit conditions
  - Dynamic SL computation and break-even rules
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session as DBSession

from backend.database import (
    Session, Trade, TradeStatus, TradeEnv,
)
from backend.core.market_state    import market
from backend.core.session_manager import check_portfolio_sl
from backend.core.dynamic_sl      import compute_dynamic_sl, should_exit_breakeven
from backend.core.settings_manager import get_settings

log = logging.getLogger("risk_engine")


class RiskChecksMixin:

    def _run_checks(self):
        if not market.is_feed_connected():
            return

        for env in (TradeEnv.PAPER, TradeEnv.LIVE):
            if check_portfolio_sl(env):
                self._kill_all_open_trades(env, reason="Portfolio SL breached")
                return

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
                    try:
                        db.rollback()
                    except Exception:
                        pass
        finally:
            db.close()

    def _check_trade(self, db: DBSession, trade: Trade):
        # OB manages its own trail/SL/target — skip here
        if (trade.entry_logic or "").startswith("[OB]"):
            return

        ltp = self._trade_ltp(trade)
        if ltp is None:
            return

        settings  = get_settings()
        direction = trade.direction
        entry     = trade.entry_price

        # Track highest option premium seen
        candidate = max(ltp, trade.entry_price or ltp)
        if trade.highest_price is None or candidate > trade.highest_price:
            trade.highest_price = candidate
            db.commit()

        # 1. Hard trade SL
        hard_sl = round(entry * (1 - trade.trade_sl_pct / 100), 2)
        if ltp <= hard_sl:
            self._trigger_exit(db, trade, ltp, TradeStatus.SL_HIT,
                               f"Hard SL hit at {ltp:.2f} (SL: {hard_sl:.2f})")
            return

        # 2. Target profit
        if trade.target_price and ltp >= trade.target_price:
            self._trigger_exit(db, trade, ltp, TradeStatus.TARGET,
                               f"Target hit at {ltp:.2f} (target: {trade.target_price:.2f})")
            return

        # 3. Dynamic SL — ES manages its own trail in es/manage.py; skip here
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

                if decision.new_sl_price != trade.dynamic_sl_price:
                    trade.dynamic_sl_price = decision.new_sl_price
                    snap = dict(trade.indicators_snapshot or {})
                    snap["last_sl_update"] = {
                        "ts":     datetime.utcnow().isoformat(),
                        "sl":     decision.new_sl_price,
                        "lock":   decision.lock_type,
                        "prob":   decision.prob_continuation,
                        "reason": decision.reasoning,
                        "vp":     decision.vp_summary,
                    }
                    trade.indicators_snapshot = snap
                    db.commit()

                if should_exit_breakeven(entry, ltp, trade.dynamic_sl_price):
                    self._trigger_exit(db, trade, ltp, TradeStatus.BREAKEVEN,
                                       "Price returned to entry after profit lock — break-even exit")
                    return

                if ltp <= decision.new_sl_price and decision.lock_type != "initial":
                    self._trigger_exit(db, trade, ltp, TradeStatus.SL_HIT,
                                       f"Dynamic SL hit at {ltp:.2f} | {decision.reasoning}")
                    return

    @staticmethod
    def _get_token_for_symbol(symbol: str) -> Optional[str]:
        from backend.core.stock_universe import get_token
        return get_token(symbol)

    @staticmethod
    def _trade_ltp(trade: Trade) -> Optional[float]:
        from backend.execution.option_selector import current_premium
        return current_premium(trade)
