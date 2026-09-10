"""
reports_router.py — Backward-compatible shim.

All report logic has been modularized into backend/reporting/.
This file re-exports the FastAPI router so existing app.include_router()
calls keep working without changes.

To understand or edit:
  API endpoints       →  backend/reporting/router.py
  HTML generation     →  backend/reporting/html_builder.py
  Email sending       →  backend/reporting/email_sender.py
  PnL report builder  →  backend/reporting/pnl_builder.py
  ML analyzer         →  backend/reporting/ml_analyzer.py
  Cost calculations   →  backend/reporting/costs.py
  Stats aggregation   →  backend/reporting/stats.py
"""

from backend.reporting.router import router  # noqa: F401

__all__ = ["router"]
