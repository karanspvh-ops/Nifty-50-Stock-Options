"""
pnl_agent.py — Backward-compatible shim.

All PnL report logic has been moved to backend/reporting/pnl_builder.py.

To understand or edit:
  Report generation   →  backend/reporting/pnl_builder.py
  Trade statement     →  backend/reporting/pnl_builder.py  (_build_trade_statement)
"""

from backend.reporting.pnl_builder import pnl_agent, PnLAgent  # noqa: F401

__all__ = ["pnl_agent", "PnLAgent"]
