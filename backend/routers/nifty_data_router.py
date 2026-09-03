"""nifty_data_router.py — Read-only API for the NIFTY options data collector.

Serves nifty_options.db (a separate DB from trading.db) for the frontend's
"Nifty Data Collector" page: collection status, raw snapshot rows, and
NIFTY spot candles for the chart — each with a today/7d/30d/90d/all range
switch. Never writes; the collector
(backend/market_data/nifty_options_collector.py) is the only writer.
"""

from datetime import date, timedelta
from fastapi import APIRouter, Query

from backend.storage.nifty_options_engine import NiftySession
from backend.storage.nifty_options_models import NiftyOptionSnapshot, NiftySpotCandle
from backend.core.market_state import market

router = APIRouter(prefix="/api/nifty-data", tags=["nifty_data"])

_RANGE_DAYS = {"today": 0, "7d": 7, "30d": 30, "90d": 90}   # "all" handled separately


def _range_start(range_key: str) -> str | None:
    """Earliest `date` string (inclusive) for a range key, or None for 'today'/'all'
    (both of which skip the lower bound — 'today' via an exact-match filter instead,
    'all' via no filter at all)."""
    if range_key not in _RANGE_DAYS:
        return None
    days = _RANGE_DAYS[range_key]
    if days == 0:
        return None
    return str(date.today() - timedelta(days=days))


@router.get("/status")
def status():
    from backend.market_data.nifty_options_collector import nifty_options_collector

    today = str(date.today())
    db = NiftySession()
    try:
        rows_today = (
            db.query(NiftyOptionSnapshot)
            .filter(NiftyOptionSnapshot.date == today)
            .count()
        )
        last = (
            db.query(NiftyOptionSnapshot)
            .filter(NiftyOptionSnapshot.date == today)
            .order_by(NiftyOptionSnapshot.id.desc())
            .first()
        )
        return {
            "date":             today,
            "rows_today":       rows_today,
            "last_snapshot_at": last.snapshot_time.isoformat() if last else None,
            "contracts":        len(nifty_options_collector._contracts),
            "expiry":           nifty_options_collector._expiry,
            "index_subscribed": nifty_options_collector.get_index_token() is not None,
        }
    finally:
        db.close()


@router.get("/snapshots")
def snapshots(
    range: str = Query("today", pattern="^(today|7d|30d|90d|all)$"),
    limit: int = Query(300, le=20000),
):
    """Latest rows first (newest snapshot_time first), flat list — the frontend
    groups by minute for display. `range` picks how far back to look; `limit`
    caps the row count regardless (wider ranges have a lot more rows than one
    table page should render — this is a "latest N within the range", not a
    full dump)."""
    db = NiftySession()
    try:
        q = db.query(NiftyOptionSnapshot)
        if range == "today":
            q = q.filter(NiftyOptionSnapshot.date == str(date.today()))
        elif range != "all":
            q = q.filter(NiftyOptionSnapshot.date >= _range_start(range))
        rows = q.order_by(NiftyOptionSnapshot.id.desc()).limit(limit).all()
        return [
            {
                "snapshot_time":  r.snapshot_time.isoformat(),
                "date":           r.date,
                "strike":         r.strike,
                "option_type":    r.option_type,
                "moneyness_rank": r.moneyness_rank,
                "nifty_spot":     r.nifty_spot,
                "open":           r.open,
                "high":           r.high,
                "low":            r.low,
                "close":          r.close,
                "volume":         r.volume,
                "oi":             r.oi,
                "oi_day_high":    r.oi_day_high,
                "oi_day_low":     r.oi_day_low,
                "buy_quantity":   r.buy_quantity,
                "sell_quantity":  r.sell_quantity,
                "day_volume":     r.day_volume,
                "bid_price":      r.bid_price,
                "ask_price":      r.ask_price,
                "spread_pct":     r.spread_pct,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/candles")
def candles(range: str = Query("today", pattern="^(today|7d|30d|90d|all)$")):
    """NIFTY 50 spot candles, chronological (oldest first, chart order).

    'today': persisted candles for today + the current in-progress candle
    read live from market_state (so the rightmost bar keeps updating between
    minute closes, not just after each persist). Every other range reads
    purely from the persisted table -- market_state only ever holds today,
    it resets on every restart.
    """
    db = NiftySession()
    try:
        q = db.query(NiftySpotCandle)
        if range == "today":
            q = q.filter(NiftySpotCandle.date == str(date.today()))
        elif range != "all":
            q = q.filter(NiftySpotCandle.date >= _range_start(range))
        persisted = q.order_by(NiftySpotCandle.candle_time.asc()).all()
        out = [
            {"time": c.candle_time.isoformat(), "open": c.open, "high": c.high,
             "low": c.low, "close": c.close}
            for c in persisted
        ]
    finally:
        db.close()

    if range == "today":
        from backend.market_data.nifty_options_collector import nifty_options_collector
        token = nifty_options_collector.get_index_token()
        if token:
            live = market.get_1m_candles(token, include_forming=True)
            have = {o["time"] for o in out}
            for c in live:
                t = c["date"].isoformat()
                if t not in have:
                    out.append({"time": t, "open": c["open"], "high": c["high"],
                                "low": c["low"], "close": c["close"]})
            out.sort(key=lambda o: o["time"])
    return out
