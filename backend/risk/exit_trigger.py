"""exit_trigger.py — Trade and portfolio exit mechanics.

Edit this file to change:
  - What gets written to the DB on exit (status, pnl, timestamps)
  - How portfolio balance is updated after an exit
  - The portfolio kill-switch sequence
"""

import logging
from typing import Optional
from sqlalchemy.orm import Session as DBSession

from backend.database import (
    Session, Trade, TradingSession, TradeStatus,
    TradeEnv, SessionStatus,
)
from backend.core.session_manager import (
    update_portfolio_balance, update_session_pnl, update_session_status,
)
from backend.core.market_state import market

log = logging.getLogger("risk_engine")


class RiskExitMixin:

    def _trigger_exit(
        self,
        db:     DBSession,
        trade:  Trade,
        price:  float,
        status: TradeStatus,
        reason: str,
        broker_fill: Optional[float] = None,
    ):
        # Live trades: sell at Zerodha FIRST and only close the DB row once the
        # SELL has filled, at the real fill price. If the SELL fails the trade
        # stays OPEN (LiveOrderError propagates) and the next check retries it.
        # broker_fill: Zerodha already sold it (the resting stop filled).
        if broker_fill is not None:
            price = broker_fill
        elif trade.env == TradeEnv.LIVE:
            price = self._live_sell(db, trade)

        pnl     = (price - trade.entry_price) * trade.quantity * trade.lot_size
        pnl_pct = ((price - trade.entry_price) / trade.entry_price) * 100

        from backend.core.clock import now_ist
        from backend.execution.option_selector import option_bid_ask
        from backend.core.stock_universe import get_option_token
        try:
            opt_tok = get_option_token(trade.option_symbol)
            bid_x, ask_x, spr_x = option_bid_ask(opt_tok, trade.option_symbol)
        except Exception:
            bid_x, ask_x, spr_x = None, None, None

        trade_id              = trade.id
        trade.status          = status
        trade.exit_price      = price
        trade.exited_at       = now_ist()
        trade.pnl             = round(pnl, 2)
        trade.pnl_pct         = round(pnl_pct, 2)
        trade.exit_logic      = reason
        trade.bid_at_exit     = bid_x
        trade.ask_at_exit     = ask_x
        trade.spread_pct_at_exit = spr_x
        db.commit()

        # Tell every connected dashboard/ES screen the moment the row is committed.
        # This is the one choke point all exits pass through (ES/OB strategy exits,
        # the generic hard-SL/target checker, the portfolio kill switch), so no
        # close path can skip the push. Previously only the ES/OB manage loops
        # pushed it, so checker-closed trades stayed "open" on screen.
        market.push_trade_update({
            "type":       "trade_closed",
            "trade_id":   trade_id,
            "exit_price": price,
            "pnl":        round(pnl, 2),
            "pnl_pct":    round(pnl_pct, 2),
            "status":     str(getattr(status, "value", status)),
            "exit_logic": reason,
        })

        from backend.core.settings_manager import get_settings
        settings      = get_settings()
        current_funds = settings.get("available_funds", 0)
        update_portfolio_balance(trade.env, current_funds + pnl)
        update_session_pnl(trade.env, pnl)

        print(f"[RISK] EXIT trade #{trade.id} | {trade.symbol} | "
              f"PnL: ₹{pnl:.2f} ({pnl_pct:.1f}%) | Reason: {reason}")

        # Notify report scheduler — it checks if all trades are now closed
        try:
            from backend.core.daily_report_scheduler import daily_report_scheduler
            daily_report_scheduler.on_trade_closed()
        except Exception as e:
            print(f"[REPORT] on_trade_closed notification failed: {e}")

        # Notify live order execution module (if registered)
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

    @staticmethod
    def _live_sell(db: DBSession, trade: Trade) -> float:
        """Sell the whole live position and return the average fill price."""
        from backend.execution.live_orders import (
            LiveOrderError, claim_exit, release_exit, sell,
        )
        if not claim_exit(trade.id):
            raise LiveOrderError(f"trade #{trade.id} exit already in progress")
        units = trade.quantity * trade.lot_size
        from backend.execution import stop_orders
        from backend.core import alerts
        try:
            if stop_orders.get(trade.id):
                # Convert the resting stop into an immediate sell -- never a second SELL order
                fill = stop_orders.exit_now(trade.id)
            else:
                fill = sell(trade.option_symbol, units)
        except LiveOrderError as e:
            release_exit(trade.id, sold=False)
            if e.sold_qty:
                # Part of the position is gone: shrink it so the retry only
                # sells what is still held.
                trade.quantity = (units - e.sold_qty) // trade.lot_size
                db.commit()
            print(f"[LIVE] CRITICAL exit failed for trade #{trade.id} "
                  f"{trade.option_symbol}: {e} -- position still open, will retry")
            alerts.raise_alert(f"exit:{trade.id}", f"{trade.option_symbol}: could not sell ({e}). "
                               f"The position is still LIVE and exposed; the system keeps retrying.",
                               trade_id=trade.id, symbol=trade.option_symbol)
            raise
        except Exception:
            release_exit(trade.id, sold=False)
            raise
        release_exit(trade.id, sold=True)
        alerts.clear_alert(f"exit:{trade.id}")
        alerts.clear_alert(f"stop:{trade.id}")
        return fill.avg_price

    def close_from_broker_fill(self, trade_id: int, price: float, reason: str) -> dict:
        """Zerodha sold the position itself (resting stop filled): book it."""
        from backend.execution.live_orders import claim_exit, release_exit
        from backend.core import alerts
        if not claim_exit(trade_id):
            return {"error": "exit already being handled"}
        db: DBSession = Session()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).first()
            if not trade or trade.status != TradeStatus.OPEN:
                release_exit(trade_id, sold=True)
                return {"error": "trade not open"}
            self._trigger_exit(db, trade, price, TradeStatus.SL_HIT, reason, broker_fill=price)
            release_exit(trade_id, sold=True)
            alerts.clear_alert(f"exit:{trade_id}")
            alerts.clear_alert(f"stop:{trade_id}")
            return {"status": "exited", "exit_price": price}
        except Exception as e:
            release_exit(trade_id, sold=True)     # it IS sold at Zerodha; never re-sell
            alerts.raise_alert(f"book:{trade_id}", f"Trade #{trade_id} was sold at Zerodha @ {price} "
                               f"but could not be recorded ({e}).", trade_id=trade_id)
            return {"error": str(e)}
        finally:
            db.close()

    def _kill_all_open_trades(self, env: TradeEnv, reason: str):
        db: DBSession = Session()
        try:
            open_trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            for trade in open_trades:
                ltp = self._trade_ltp(trade) or trade.entry_price
                try:
                    self._trigger_exit(db, trade, ltp, TradeStatus.KILLED, reason)
                except Exception as e:
                    # Keep killing the rest; a failed live SELL stays OPEN and
                    # the next risk cycle retries it.
                    print(f"[RISK] Kill-switch exit failed for trade #{trade.id}: {e}")
                    db.rollback()

            update_session_status(env, SessionStatus.KILLED)
            market.halt_trading(reason)
            print(f"[RISK] PORTFOLIO SL — all {env} trades killed. Reason: {reason}")
        finally:
            db.close()
