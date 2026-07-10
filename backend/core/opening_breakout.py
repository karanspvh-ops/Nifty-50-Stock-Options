"""
opening_breakout.py — Backward-compatible shim.

All Opening Breakout logic has been modularized into backend/strategies/ob/.
This file re-exports the singleton and class so existing imports keep working.

To understand or edit the strategy:
  Filters / signals   →  backend/strategies/ob/filters.py
  Entry / plan build  →  backend/strategies/ob/entry.py
  Position management →  backend/strategies/ob/manage.py
  Square-off          →  backend/strategies/ob/exit.py
  DB state helpers    →  backend/strategies/ob/state.py
  ML auto-tuner       →  backend/strategies/ob/tuner.py
  Params / constants  →  backend/strategies/ob/params.py
  Types (dataclasses) →  backend/strategies/ob/types.py
  Coordinator         →  backend/strategies/ob/strategy.py
"""

from backend.strategies.ob.strategy import opening_breakout, OpeningBreakout  # noqa: F401
from backend.strategies.ob.types    import PlanStock, TradePlan  # noqa: F401

__all__ = ["opening_breakout", "OpeningBreakout", "PlanStock", "TradePlan"]
