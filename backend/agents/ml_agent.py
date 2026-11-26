"""
ml_agent.py — Backward-compatible shim.

All ML analysis logic has been moved to backend/reporting/ml_analyzer.py.

To understand or edit:
  Loss pattern detection  →  backend/reporting/ml_analyzer.py  (_detect_patterns)
  Recommendations         →  backend/reporting/ml_analyzer.py  (_build_recommendations)
  Weight auto-adjustment  →  backend/reporting/ml_analyzer.py  (_adjust_weights)
"""

from backend.reporting.ml_analyzer import ml_agent, MLAgent  # noqa: F401

__all__ = ["ml_agent", "MLAgent"]
