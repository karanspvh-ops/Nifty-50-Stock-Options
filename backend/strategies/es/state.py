"""state.py — ES trade-state DB helpers (count open, entered symbols, PnL today)."""

from datetime import date, datetime as _dt

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.es.params import ES_TAG


class ESStateMixin:
    """DB queries that answer: how many ES positions are open, what did we enter today?"""

    @staticmethod
    def _today_from_dt():
        d = date.today()
        return _dt(d.year, d.month, d.day, 0, 0, 0)

    def _entered_symbols(self, env: TradeEnv) -> set:
        """Every symbol ES touched today (open or closed) — prevents re-entry after SL/target."""
        from_dt = self._today_from_dt()
        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
                Trade.entered_at >= from_dt,
            ).all()
        return {t.symbol for t in trades}

    def _count_open(self, env: TradeEnv) -> int:
        with DBSession() as db:
            return db.query(Trade).filter(
                Trade.env == env,
                Trade.status == TradeStatus.OPEN,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
            ).count()

    def _count_today(self, env: TradeEnv) -> int:
        """Total ES trades entered today (open + closed)."""
        from_dt = self._today_from_dt()
        with DBSession() as db:
            return db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
                Trade.entered_at >= from_dt,
            ).count()

    def _net_pnl_today(self, env: TradeEnv) -> float:
        """Cumulative ES PnL from closed trades today."""
        from_dt = self._today_from_dt()
        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
                Trade.entered_at >= from_dt,
                Trade.status != TradeStatus.OPEN,
            ).all()
        return sum((t.pnl or 0) for t in trades)

    def _refills_blocked(self, env: TradeEnv) -> bool:
        """After 5 ES trades entered and day PnL is positive, protect the gain."""
        if self._count_today(env) < 5:
            return False
        return self._net_pnl_today(env) > 0
