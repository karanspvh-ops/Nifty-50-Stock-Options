"""
test_peak_logic.py — Unit tests for highest_price / peak_pct logic.

Covers the bugs found and fixed on 2026-07-07:
  - IDEA trade: highest_price=0.87 < entry=0.88 rendered as "-1.1%" peak
  - BDL/BEL/INDUSTOWER: highest_price=NULL (never tracked for trail exits)
  - peak_pct showing negative when highest_price < entry_price
"""

import pytest


# ── Pure peak_pct calculation (mirrors reports_router + scheduler logic) ──────

def peak_pct(highest_price, entry_price) -> float | None:
    """
    Returns peak % gain above entry, or None if peak is at/below entry.
    This is the corrected logic from reports_router.py and daily_report_scheduler.py.
    """
    if highest_price and entry_price and highest_price > entry_price:
        return round((highest_price - entry_price) / entry_price * 100, 1)
    return None


class TestPeakPct:
    def test_above_entry_returns_positive(self):
        # EXIDEIND: entry=14.95, highest=15.00 → +0.33%
        assert peak_pct(15.00, 14.95) == pytest.approx(0.3, abs=0.05)

    def test_below_entry_returns_none(self):
        # IDEA bug: entry=0.88, highest=0.87 — must return None not -1.1%
        assert peak_pct(0.87, 0.88) is None

    def test_equal_to_entry_returns_none(self):
        # Exactly at entry — no gain, treat as None
        assert peak_pct(10.0, 10.0) is None

    def test_null_highest_returns_none(self):
        # BDL/BEL/INDUSTOWER: highest_price=NULL
        assert peak_pct(None, 55.25) is None

    def test_null_entry_returns_none(self):
        assert peak_pct(15.0, None) is None

    def test_zero_highest_returns_none(self):
        # 0.0 is falsy — treat same as NULL
        assert peak_pct(0.0, 14.95) is None

    def test_large_gain(self):
        # POWERINDIA: entry=1550, highest=1750 → +12.9%
        assert peak_pct(1750.0, 1550.0) == pytest.approx(12.9, abs=0.05)

    def test_fractional_option_premium(self):
        # Small option premiums: entry=0.88, highest=1.05 → +19.3%
        result = peak_pct(1.05, 0.88)
        assert result is not None
        assert result > 0


# ── highest_price floor logic (mirrors risk_engine + early_scalp + OB) ────────

def apply_highest_price_update(current_highest, ltp, entry_price):
    """
    Returns the new highest_price after a tick at `ltp`.
    Mirrors the fixed logic in risk_engine._check_trade,
    early_scalp._manage, and opening_breakout._manage.
    """
    candidate = max(ltp, entry_price or ltp)
    if current_highest is None or candidate > current_highest:
        return candidate
    return current_highest


class TestHighestPriceTracking:
    def test_first_tick_below_entry_floored_at_entry(self):
        # IDEA bug: entry=0.88, first tick=0.87 — must floor at entry
        result = apply_highest_price_update(None, ltp=0.87, entry_price=0.88)
        assert result == 0.88

    def test_first_tick_above_entry_uses_tick(self):
        result = apply_highest_price_update(None, ltp=15.00, entry_price=14.95)
        assert result == 15.00

    def test_subsequent_tick_higher_updates(self):
        result = apply_highest_price_update(current_highest=15.00, ltp=15.50, entry_price=14.95)
        assert result == 15.50

    def test_subsequent_tick_lower_no_update(self):
        result = apply_highest_price_update(current_highest=15.50, ltp=14.80, entry_price=14.95)
        assert result == 15.50

    def test_null_initial_with_tick_above_entry(self):
        # Normal case: no history, tick above entry
        result = apply_highest_price_update(None, ltp=1750.0, entry_price=1550.0)
        assert result == 1750.0

    def test_null_entry_price_uses_ltp(self):
        # Edge case: entry_price somehow None — fallback to ltp
        result = apply_highest_price_update(None, ltp=10.0, entry_price=None)
        assert result == 10.0

    def test_trail_exit_highest_preserved(self):
        # BDL trail lock exit scenario: highest was 58.5 before exit
        result = apply_highest_price_update(current_highest=58.5, ltp=57.8, entry_price=55.25)
        assert result == 58.5   # doesn't drop on exit tick
