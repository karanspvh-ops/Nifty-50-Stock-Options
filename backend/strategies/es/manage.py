"""manage.py — ES live position management (trailing SL, peak tracking, exit triggers).

Edit this file when changing:
  - trailing SL activation threshold or gap
  - target exit logic
  - how the trail state is recovered after a restart
"""

from backend.core.market_state   import market
from backend.core.risk_engine    import risk_engine
from backend.execution.option_selector import current_premium, option_premium
from backend.core.stock_universe import get_option_token

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.es.params import ES_TAG


class ESManageMixin:
    """Live position management for ES.  Touch only this file for SL/target changes."""

    def _manage(self, env: TradeEnv):
        sl_pct  = float(self._p("hard_sl_pct"))
        tgt_pct = float(self._p("target_pct"))
        arm_pct = float(self._p("trail_activate_pct"))
        gap_pct = float(self._p("trail_gap_pct"))

        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.status == TradeStatus.OPEN,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
            ).all()

        for t in trades:
            if not t.entry_price:
                continue
            try:
                tok     = get_option_token(t.option_symbol)
                premium = option_premium(tok, t.option_symbol)
                if not premium:
                    continue
            except Exception:
                continue

            pnl_pct = (premium - t.entry_price) / t.entry_price * 100

            # Track highest premium; floor at entry_price
            peak_candidate = max(premium, t.entry_price)
            if t.highest_price is None or peak_candidate > t.highest_price:
                with DBSession() as hp_db:
                    hp_db.query(Trade).filter(Trade.id == t.id).update(
                        {"highest_price": peak_candidate}, synchronize_session=False)
                    hp_db.commit()
                t.highest_price = peak_candidate

            # Recover trail state from DB on restart
            tid = t.id
            if tid not in self._trail_armed:
                if t.dynamic_sl_price and t.dynamic_sl_price > t.trade_sl_price:
                    self._trail_armed[tid]  = True
                    recovered_lock = (t.dynamic_sl_price - t.entry_price) / t.entry_price * 100
                    self._trail_locked[tid] = recovered_lock
                    print(f"[ES] Trail state recovered from DB for {t.symbol}: locked={recovered_lock:+.1f}%")
                elif t.highest_price and t.highest_price >= t.entry_price * (1 + arm_pct / 100):
                    peak_pnl = (t.highest_price - t.entry_price) / t.entry_price * 100
                    self._trail_armed[tid]  = True
                    self._trail_locked[tid] = peak_pnl - gap_pct
                    print(f"[ES] Trail re-armed from highest_price for {t.symbol}: "
                          f"peak={peak_pnl:.1f}% locked={self._trail_locked[tid]:+.1f}%")
                else:
                    self._trail_armed[tid]  = False
                    self._trail_locked[tid] = -sl_pct

            if pnl_pct >= arm_pct and not self._trail_armed[tid]:
                self._trail_armed[tid] = True
                print(f"[ES] Trail armed on {t.symbol} at +{pnl_pct:.1f}%")

            if self._trail_armed[tid]:
                new_lock = pnl_pct - gap_pct
                if new_lock > self._trail_locked[tid]:
                    self._trail_locked[tid] = new_lock
                    new_dyn_sl = round(t.entry_price * (1 + self._trail_locked[tid] / 100), 2)
                    with DBSession() as db:
                        db.query(Trade).filter(Trade.id == tid).update(
                            {"dynamic_sl_price": new_dyn_sl}, synchronize_session=False)
                        db.commit()

            locked = self._trail_locked.get(tid, -sl_pct)

            exit_reason = None
            if pnl_pct >= tgt_pct:
                exit_reason = f"ES Target +{tgt_pct:.0f}%"
            elif pnl_pct <= locked:
                if self._trail_armed.get(tid):
                    exit_reason = f"ES Trail lock {locked:+.1f}%"
                else:
                    exit_reason = f"ES SL -{sl_pct:.0f}%"

            if exit_reason:
                try:
                    risk_engine.force_exit_trade(t.id, exit_reason)
                    self._trail_armed.pop(tid, None)
                    self._trail_locked.pop(tid, None)
                    print(f"[ES] Exit {t.symbol} | {exit_reason} | PnL {pnl_pct:+.1f}%")
                except Exception as e:
                    print(f"[ES] Exit error {t.symbol}: {e}")
