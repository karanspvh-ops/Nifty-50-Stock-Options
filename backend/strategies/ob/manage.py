"""manage.py — OB live position management (trailing SL, peak tracking, target exits).

Edit this file when changing:
  - trailing stop activation threshold or gap percentage
  - single-lot vs multi-lot exit logic
  - how peak (highest_price) is tracked
"""

from backend.core.market_state import market
from backend.core.risk_engine  import risk_engine
from backend.execution.option_selector import current_premium

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.ob.params import OB_TAG


class OBManageMixin:
    """Live position management for OB.  Touch only this file for SL/target changes."""

    def _manage(self, env: TradeEnv):
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            ob_trades = [t for t in trades if (t.entry_logic or "").startswith(OB_TAG)]
        finally:
            db.close()

        for t in ob_trades:
            ltp = current_premium(t)
            if not ltp:
                continue
            entry   = t.entry_price
            pnl_pct = (ltp - entry) / entry * 100

            peak_candidate = max(ltp, entry)
            if t.highest_price is None or peak_candidate > t.highest_price:
                self._set_highest(t.id, peak_candidate)
                t.highest_price = peak_candidate

            hard_sl_pct  = self._p("hard_sl_pct")
            target_pct   = self._p("target_pct")
            second_pct   = self._p("second_target_pct")
            activate_pct = self._p("trail_activate_pct")
            gap_pct      = self._p("trail_gap_pct")

            hard_sl  = entry * (1 - hard_sl_pct / 100)
            peak     = t.highest_price or ltp
            peak_pct = (peak - entry) / entry * 100

            trail_stop = hard_sl
            if peak_pct >= activate_pct:
                trail_stop = max(hard_sl, peak * (1 - gap_pct / 100))
                new_dsl = round(trail_stop, 2)
                if t.dynamic_sl_price != new_dsl:
                    with DBSession() as db:
                        db.query(Trade).filter(Trade.id == t.id).update(
                            {"dynamic_sl_price": new_dsl}, synchronize_session=False)
                        db.commit()

            # Target exits
            if pnl_pct >= target_pct and t.quantity <= 1:
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Target +{target_pct:.0f}% hit")
                continue
            if pnl_pct >= second_pct:
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Runner target +{second_pct:.0f}% hit")
                continue

            # Stop / trail exit
            if ltp <= trail_stop:
                lock = (trail_stop - entry) / entry * 100
                if trail_stop > hard_sl:
                    risk_engine.force_exit_trade(t.id, f"{OB_TAG} Trailing stop hit (locked {lock:+.1f}%)")
                else:
                    risk_engine.force_exit_trade(t.id, f"{OB_TAG} Hard SL -{hard_sl_pct:.0f}% hit")

    def _set_highest(self, trade_id: int, price: float):
        db = DBSession()
        try:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
            if t:
                t.highest_price = price
                db.commit()
        finally:
            db.close()
