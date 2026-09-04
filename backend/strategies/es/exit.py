"""exit.py — ES square-off: close all open ES positions at 10:30."""

from backend.core.risk_engine import risk_engine
from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.es.params import ES_TAG


class ESExitMixin:
    """Time-stop square-off for ES.  Touch only this file for exit timing changes."""

    def _square_off(self, env: TradeEnv):
        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.status == TradeStatus.OPEN,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
            ).all()
            trade_ids = [t.id for t in trades]

        if not trade_ids:
            return

        for tid in trade_ids:
            try:
                risk_engine.force_exit_trade(tid, "ES Time-stop 10:30")
                print(f"[ES] Squared off trade #{tid} at 10:30")
            except Exception as e:
                print(f"[ES] Square off error #{tid}: {e}")
