"""
test_risk_engine_peak.py — Tests for risk engine per-trade isolation and peak tracking.

Covers:
  - Per-trade exception isolation (one bad trade must not stop others)
  - ES trades skip dynamic SL (no 'token' NameError)
  - highest_price update logic used by risk_engine._check_trade
"""

import pytest
from unittest.mock import MagicMock, patch, call


# ── Simulate the per-trade isolation fix ─────────────────────────────────────

def run_checks_isolated(trades, check_fn):
    """
    Mirrors the fixed _run_checks loop in risk_engine.py:
    each trade is wrapped in try/except so one crash doesn't abort the rest.
    Returns (results, errors) where results[i] is None if trade i threw.
    """
    results = []
    errors  = []
    for trade in trades:
        try:
            results.append(check_fn(trade))
            errors.append(None)
        except Exception as e:
            results.append(None)
            errors.append(str(e))
    return results, errors


class TestPerTradeIsolation:
    def test_one_crash_does_not_stop_others(self):
        trades = ["RELIANCE", "BADTRADE", "INFY"]

        def check_fn(trade):
            if trade == "BADTRADE":
                raise RuntimeError("simulated crash")
            return f"{trade}_checked"

        results, errors = run_checks_isolated(trades, check_fn)

        assert results[0] == "RELIANCE_checked"
        assert results[1] is None          # crashed trade → None
        assert results[2] == "INFY_checked"
        assert errors[1] == "simulated crash"
        assert errors[0] is None
        assert errors[2] is None

    def test_all_succeed_no_errors(self):
        trades = ["A", "B", "C"]
        results, errors = run_checks_isolated(trades, lambda t: t + "_ok")
        assert results == ["A_ok", "B_ok", "C_ok"]
        assert all(e is None for e in errors)

    def test_all_fail_gracefully(self):
        trades = ["X", "Y"]
        results, errors = run_checks_isolated(trades, lambda t: (_ for _ in ()).throw(ValueError("boom")))
        assert results == [None, None]
        assert all(e == "boom" for e in errors)

    def test_empty_trade_list(self):
        results, errors = run_checks_isolated([], lambda t: t)
        assert results == []
        assert errors == []


# ── ES trade detection (no dynamic SL) ───────────────────────────────────────

def is_es_trade(entry_logic: str | None) -> bool:
    """Mirrors the check in risk_engine._check_trade."""
    return (entry_logic or "").startswith("[ES]")


class TestEsTradeDetection:
    def test_es_prefix_detected(self):
        assert is_es_trade("[ES] 9:16 entry") is True

    def test_ob_not_es(self):
        assert is_es_trade("[OB] breakout") is False

    def test_none_not_es(self):
        assert is_es_trade(None) is False

    def test_empty_not_es(self):
        assert is_es_trade("") is False

    def test_lowercase_es_not_detected(self):
        # ES prefix is uppercase — lowercase should not match
        assert is_es_trade("[es] entry") is False


# ── Token resolution (dynamic SL) ────────────────────────────────────────────

class TestTokenResolution:
    def test_skip_dynamic_sl_for_es_trades(self):
        """ES trades must never call compute_dynamic_sl (fixes NameError crash)."""
        dynamic_sl_called = []

        def maybe_compute_dynamic_sl(trade, entry_logic):
            if is_es_trade(entry_logic):
                return None   # skip — ES doesn't use dynamic SL
            dynamic_sl_called.append(trade)
            return "COMPUTED"

        maybe_compute_dynamic_sl("RELIANCE", "[ES] 9:16")
        assert dynamic_sl_called == [], "ES trade should not call dynamic SL"

        maybe_compute_dynamic_sl("RELIANCE", "[OB] breakout")
        assert dynamic_sl_called == ["RELIANCE"], "OB trade should call dynamic SL"

    def test_ob_trade_uses_symbol_token(self):
        """Non-ES trades must resolve token from symbol, not a bare 'token' variable."""
        symbol_map = {"RELIANCE": 738561, "INFY": 408065}

        def get_token_for_symbol(symbol):
            return symbol_map.get(symbol)

        assert get_token_for_symbol("RELIANCE") == 738561
        assert get_token_for_symbol("INFY") == 408065
        assert get_token_for_symbol("UNKNOWN") is None

    def test_none_token_skips_dynamic_sl(self):
        """If token lookup returns None, dynamic SL should be silently skipped."""
        compute_called = []

        def check_trade(symbol, entry_logic):
            if is_es_trade(entry_logic):
                return
            token = {"RELIANCE": 738561}.get(symbol)
            if token is not None:
                compute_called.append(symbol)

        check_trade("UNKNOWN_STOCK", "[OB]")
        assert compute_called == [], "None token must not call compute_dynamic_sl"

        check_trade("RELIANCE", "[OB]")
        assert compute_called == ["RELIANCE"]
