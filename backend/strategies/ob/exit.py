"""exit.py — OB square-off: close all open OB positions at 15:15."""

from backend.core.risk_engine import risk_engine
from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.ob.params import OB_TAG


class OBExitMixin:
    """Time-stop square-off for OB.  Touch only this file for exit timing changes."""

    def _square_off(self, env: TradeEnv):
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            ob = [t for t in trades if (t.entry_logic or "").startswith(OB_TAG)]
        finally:
            db.close()

        for t in ob:
            risk_engine.force_exit_trade(t.id, f"{OB_TAG} Square-off 3:15 PM")
