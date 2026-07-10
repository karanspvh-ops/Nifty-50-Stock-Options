"""
early_scalp.py — Backward-compatible shim.

All Early Scalp logic has been modularized into backend/strategies/es/.
This file re-exports the singleton and class so existing imports keep working.

To understand or edit the strategy:
  Filters / signals   →  backend/strategies/es/filters.py
  Entry / plan build  →  backend/strategies/es/entry.py
  Position management →  backend/strategies/es/manage.py
  Square-off          →  backend/strategies/es/exit.py
  Trail / DB helpers  →  backend/strategies/es/state.py
  Params / constants  →  backend/strategies/es/params.py
  Types (dataclasses) →  backend/strategies/es/types.py
  Coordinator         →  backend/strategies/es/strategy.py
"""

from backend.strategies.es.strategy import early_scalp, EarlyScalp  # noqa: F401
from backend.strategies.es.types    import ScalpCandidate, EarlyScalpPlan  # noqa: F401

__all__ = ["early_scalp", "EarlyScalp", "ScalpCandidate", "EarlyScalpPlan"]
