"""quantity.py — Position sizing: how many lots to buy.

Capital rule (both ES and OB):
    trading capital = min(account balance, capital limit)
    per-trade budget = trading capital / 5

  - The capital limit is the "available_funds" setting (default ₹5,00,000),
    adjustable from the dashboard. Money above the limit is left untouched.
  - Live: the account balance is Zerodha's start-of-day funds (opening
    balance + funds added today), so trades placed later in the day are not
    shrunk by premium already paid on the morning's entries. A loss carries
    into the next day's opening balance and shrinks every slot from then on.
  - Paper: there is no real balance, so the capital limit itself is used.
  - A slot that can't afford one lot is skipped, never rounded up to 1 lot.
"""

from typing import Optional, Tuple

SLOTS = 5
DEFAULT_LIMIT = 500_000.0


def calc_quantity(capital: float, premium: float, lot_size: int,
                  max_positions: int = SLOTS) -> int:
    """Lots affordable from capital / max_positions. 0 when not even one lot fits."""
    budget    = capital / max(max_positions, 1)
    cost_1lot = premium * lot_size
    if cost_1lot <= 0:
        return 0
    return int(budget // cost_1lot)


def capital_limit() -> float:
    from backend.core.settings_manager import get_settings
    return float(get_settings().get("available_funds") or DEFAULT_LIMIT)


def live_funds() -> Optional[dict]:
    """Zerodha balance from the account monitor (refreshed every 2 s); fetched
    directly if it's missing or stale. None when Zerodha can't be read."""
    from datetime import datetime
    from backend.core.account_monitor import account_monitor
    a = account_monitor.latest()
    fresh = False
    if a and a.get("ok") and a.get("fetched_at"):
        try:
            age = (datetime.now(datetime.fromisoformat(a["fetched_at"]).tzinfo)
                   - datetime.fromisoformat(a["fetched_at"])).total_seconds()
            fresh = age < 30
        except Exception:
            fresh = False
    if not fresh:
        a = account_monitor.refresh(critical=True)
    return a if a and a.get("ok") else None


def trading_capital(live: bool) -> Tuple[Optional[float], str]:
    """(capital to divide across the 5 slots, explanation). None = can't size."""
    limit = capital_limit()
    if not live:
        return limit, f"paper: capital limit ₹{limit:,.0f}"
    a = live_funds()
    if a is None:
        return None, "Zerodha balance unavailable"
    start_funds = float(a.get("opening_balance") or 0) + float(a.get("intraday_payin") or 0)
    cap = min(start_funds, limit)
    return cap, f"live: min(start-of-day funds ₹{start_funds:,.0f}, limit ₹{limit:,.0f}) = ₹{cap:,.0f}"
