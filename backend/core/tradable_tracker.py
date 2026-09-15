"""
tradable_tracker.py — Backward-compatible shim.

All tradable-window logic has been moved to backend/universe/tradable_tracker.py.
This file re-exports the singleton so existing imports keep working.
"""

from backend.universe.tradable_tracker import tradable_tracker, TradableTracker  # noqa: F401

__all__ = ["tradable_tracker", "TradableTracker"]
