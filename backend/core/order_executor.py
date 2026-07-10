"""
order_executor.py — Backward-compatible shim.

All execution logic has been modularized into backend/execution/.
This file re-exports everything so existing imports keep working.

To understand or edit:
  Position sizing     →  backend/execution/quantity.py
  Option selection    →  backend/execution/option_selector.py
  Liquidity guard     →  backend/execution/liquidity.py
  Order placement     →  backend/execution/order_executor.py
"""

from backend.execution.order_executor  import place_entry_order, place_exit_order  # noqa: F401
from backend.execution.option_selector import option_premium, current_premium, select_option  # noqa: F401
from backend.execution.quantity        import calc_quantity  # noqa: F401
from backend.execution.liquidity       import has_liquidity as _has_liquidity  # noqa: F401

__all__ = [
    "place_entry_order", "place_exit_order",
    "option_premium", "current_premium", "select_option",
    "calc_quantity", "_has_liquidity",
]
