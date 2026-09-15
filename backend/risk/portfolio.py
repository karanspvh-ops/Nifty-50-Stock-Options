"""portfolio.py — Dashboard and manual override APIs for risk management.

Edit this file to change:
  - What fields are returned in the risk snapshot (for the dashboard)
  - Force-exit validation logic
"""

import logging
from sqlalchemy.orm import Session as DBSession

from backend.database import Session, Trade, TradeStatus

log = logging.getLogger("risk_engine")


class RiskPortfolioMixin:

    def force_exit_trade(self, trade_id: int, reason: str = "Manual exit") -> dict:
        db: DBSession = Session()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).first()
            if not trade:
                return {"error": "Trade not found"}
            if trade.status != TradeStatus.OPEN:
                return {"error": f"Trade is already {trade.status}"}

            ltp = self._trade_ltp(trade)
            if ltp is None:
                return {"error": "Option LTP unavailable — feed may be disconnected"}

            self._trigger_exit(db, trade, ltp, TradeStatus.CLOSED, reason)
            return {"status": "exited", "trade_id": trade_id, "exit_price": ltp}
        finally:
            db.close()

    def get_open_risk_snapshot(self) -> list:
        db: DBSession = Session()
        try:
            open_trades = db.query(Trade).filter(Trade.status == TradeStatus.OPEN).all()
            result = []
            for t in open_trades:
                ltp       = self._trade_ltp(t)
                ref       = ltp or t.entry_price
                qty_total = (t.quantity or 0) * (t.lot_size or 0)
                pnl_pct   = ((ref - t.entry_price) / t.entry_price * 100) if ltp else 0
                pnl_rs    = round((ref - t.entry_price) * qty_total, 2) if ltp else 0
                result.append({
                    "trade_id":      t.id,
                    "symbol":        t.symbol,
                    "option_symbol": t.option_symbol,
                    "direction":     t.direction,
                    "env":           t.env,
                    "entry":         t.entry_price,
                    "ltp":           ltp,
                    "pnl_pct":       round(pnl_pct, 2),
                    "pnl":           pnl_rs,
                    "quantity":      t.quantity,
                    "lot_size":      t.lot_size,
                    "hard_sl":       round(t.entry_price * (1 - t.trade_sl_pct / 100), 2),
                    "dynamic_sl":    t.dynamic_sl_price,
                    "target":        t.target_price,
                    "status":        t.status,
                    "highest_price": t.highest_price,
                    "entered_at":    str(t.entered_at) if t.entered_at else None,
                })
            return result
        finally:
            db.close()
