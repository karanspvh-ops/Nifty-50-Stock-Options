"""exit_trigger.py — Trade and portfolio exit mechanics.

Edit this file to change:
  - What gets written to the DB on exit (status, pnl, timestamps)
  - How portfolio balance is updated after an exit
  - The portfolio kill-switch sequence
"""

import logging
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
    ):
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
                self._trigger_exit(db, trade, ltp, TradeStatus.KILLED, reason)

            update_session_status(env, SessionStatus.KILLED)
            market.halt_trading(reason)
            print(f"[RISK] PORTFOLIO SL — all {env} trades killed. Reason: {reason}")
        finally:
            db.close()
