"""
risk_engine.py — Backward-compatible shim.

All risk logic has been modularized into backend/risk/.
This file re-exports the singleton so existing imports keep working.

To understand or edit:
  Per-trade checks    →  backend/risk/checks.py
  Exit mechanics      →  backend/risk/exit_trigger.py
  Dashboard / manual  →  backend/risk/portfolio.py
  Coordinator         →  backend/risk/engine.py
"""

from backend.risk.engine import risk_engine, RiskEngine  # noqa: F401

__all__ = ["risk_engine", "RiskEngine"]
