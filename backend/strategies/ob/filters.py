"""filters.py — OB signal detection (open-ref capture, gap qualify, conviction, already-in).

Edit this file when refining OB gap/sector/confirmation criteria.
"""

from datetime import datetime
from typing import Optional

from backend.core.market_state import market
from backend.strategies.ob.params import (
    SCAN_START, GAP_MIN_PCT, EARLY_SECTOR_MIN_PCT, OB_TAG,
)
from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv


class OBFiltersMixin:
    """Signal detection and guard checks for OB.  Touch only this file for filter changes."""

    # ── 9:15 open reference capture ───────────────────────────────────────────

    def _session_open_from_candles(self, token: str, day) -> Optional[float]:
        for c in market.get_candles(token, n=260):
            ts = c.get("ts")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if dt.date() == day and dt.time() >= SCAN_START:
                return c.get("open")
        return None

    def _capture_open_refs(self):
        """Lock each stock's 09:15 session-open as the reference for opening-move %.
        Prefer the true 09:15 candle open; fall back to first live price seen."""
        today = datetime.now().date()
        moves = market.get_all_stock_moves()
        for token, mv in moves.items():
            if token in self._open_ref_final:
                continue
            ref = self._session_open_from_candles(token, today)
            if ref is not None:
                self._open_ref[token] = ref
                self._open_ref_final.add(token)
            elif token not in self._open_ref and mv.get("ltp"):
                self._open_ref[token] = mv["ltp"]

    def _opening_move(self, token: str, ltp: float) -> float:
        ref = self._open_ref.get(token)
        if not ref:
            return 0.0
        return round((ltp - ref) / ref * 100, 2)

    # ── Gap acceleration ──────────────────────────────────────────────────────

    def _gap_qualifies(self, ps) -> bool:
        """True if the stock gapped >3% from prev close AND sector is strong.
        Allows early entry ~5 min before the normal window."""
        tick       = market.get_tick(ps.token)
        open_ref   = self._open_ref.get(ps.token)
        prev_close = tick.get("prev_close") if tick else None
        if not prev_close or not open_ref:
            return False
        gap_pct = abs((open_ref - prev_close) / prev_close * 100)
        own_sec_pct = market.get_sector_moves().get(ps.sector, {}).get("pct_change", 0)
        sector_strong = abs(own_sec_pct) >= EARLY_SECTOR_MIN_PCT
        return gap_pct >= GAP_MIN_PCT and sector_strong

    # ── Refill conviction guard ───────────────────────────────────────────────

    def _is_extreme_conviction(self, ps, bo, current_move: float) -> bool:
        """True when the setup is an outlier justifying a refill on a profitable day.
        Either a concentrated-volume spike OR very high R-factor + strong move."""
        spike_vol  = float(self._p("ob_spike_vol"))
        spike_rf   = float(self._p("ob_spike_rfactor"))
        spike_move = float(self._p("ob_spike_move"))
        vol_spike  = bo.vol_ratio >= spike_vol
        rf_spike   = ps.r_factor >= spike_rf and abs(current_move) >= spike_move
        return vol_spike or rf_spike

    # ── Duplicate position guard ──────────────────────────────────────────────

    def _already_in(self, env: TradeEnv, symbol: str) -> bool:
        db = DBSession()
        try:
            return db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN, Trade.env == env,
                Trade.symbol == symbol).count() > 0
        finally:
            db.close()
