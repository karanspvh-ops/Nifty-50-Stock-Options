"""state.py — OB trade-state DB helpers (count open, today count, net PnL)."""

from datetime import date, datetime as _dt

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.ob.params import OB_TAG


class OBStateMixin:
    """DB queries that answer: how many OB positions are open, what fired today?"""

    def _count_open_ob(self, env: TradeEnv) -> int:
        db = DBSession()
        try:
            trades = db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN, Trade.env == env).all()
            return sum(1 for t in trades if (t.entry_logic or "").startswith(OB_TAG))
        finally:
            db.close()

    def _count_ob_today(self, env: TradeEnv) -> int:
        """All OB trades entered today (open or closed) — guards the extended hunt."""
        d = date.today()
        from_dt = _dt(d.year, d.month, d.day, 0, 0, 0)
        db = DBSession()
        try:
            return db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{OB_TAG}%"),
                Trade.entered_at >= from_dt,
            ).count()
        finally:
            db.close()

    def _ob_net_pnl_today(self, env: TradeEnv) -> float:
        """Net PnL of all CLOSED OB trades today (open positions excluded)."""
        d = date.today()
        from_dt = _dt(d.year, d.month, d.day, 0, 0, 0)
        db = DBSession()
        try:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{OB_TAG}%"),
                Trade.entered_at >= from_dt,
                Trade.status != TradeStatus.OPEN,
            ).all()
            return sum((t.pnl or 0) for t in trades)
        finally:
            db.close()
