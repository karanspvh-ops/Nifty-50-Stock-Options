"""clock.py — IST time helper. The whole system is India-only, so trade
timestamps are stored in IST wall-clock (naive) for correct display."""

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Current IST time as a naive datetime (for DB storage / display)."""
    return datetime.now(IST).replace(tzinfo=None)
