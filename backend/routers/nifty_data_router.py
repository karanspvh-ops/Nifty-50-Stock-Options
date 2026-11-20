"""nifty_data_router.py — Read-only API for the NIFTY options data collector.

Serves nifty_options.db (a separate DB from trading.db) for the frontend's
"Nifty Data Collector" page: collection status, raw snapshot rows, and
NIFTY spot candles for the chart — each with a today/7d/30d/90d/all range
switch. Reads only, except /backfill which starts the end-of-day completion
job (backend/market_data/nifty_backfill.py); the live collector
(backend/market_data/nifty_options_collector.py) is the other writer.
"""

from datetime import date, datetime, timedelta
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func

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
            .order_by(NiftyOptionSnapshot.snapshot_time.desc(), NiftyOptionSnapshot.id.desc())
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
    limit: int = Query(200_000, le=200_000),
):
    """Latest rows first (newest snapshot_time first), flat list — the frontend
    groups by minute for display. `range` picks how far back to look; `limit`
    is a hard safety cap only, not a "latest N" truncation — the frontend
    requests the full range and expects every row within it back."""
    db = NiftySession()
    try:
        q = db.query(NiftyOptionSnapshot)
        if range == "today":
            q = q.filter(NiftyOptionSnapshot.date == str(date.today()))
        elif range != "all":
            q = q.filter(NiftyOptionSnapshot.date >= _range_start(range))
        # Sort by actual snapshot_time, not row id -- id only tracks insertion
        # order, which matches time for the live collector's own minute-major
        # writes but breaks for anything inserted out of chronological order
        # (e.g. a historical backfill done contract-by-contract). id is kept
        # only as a tiebreaker so same-timestamp rows stay in a stable order.
        rows = q.order_by(NiftyOptionSnapshot.snapshot_time.desc(),
                           NiftyOptionSnapshot.id.desc()).limit(limit).all()
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


def _range_filter(q, range_key: str):
    if range_key == "today":
        return q.filter(NiftyOptionSnapshot.date == str(date.today()))
    if range_key != "all":
        return q.filter(NiftyOptionSnapshot.date >= _range_start(range_key))
    return q


@router.get("/minutes")
def minutes(range: str = Query("today", pattern="^(today|7d|30d|90d|all)$")):
    """One entry per snapshot minute, newest first: time, spot and how many
    contract rows it holds. `book` counts rows with a live bid/ask (rows filled
    by the end-of-day backfill have none). Rows load per minute via /minute."""
    minute = func.strftime("%Y-%m-%dT%H:%M", NiftyOptionSnapshot.snapshot_time)
    db = NiftySession()
    try:
        q = db.query(
            minute.label("m"),
            func.max(NiftyOptionSnapshot.nifty_spot),
            func.count(NiftyOptionSnapshot.id),
            func.count(NiftyOptionSnapshot.bid_price),
        )
        rows = _range_filter(q, range).group_by(minute).order_by(minute.desc()).all()
        return [{"t": m, "spot": spot, "rows": n, "book": book} for m, spot, n, book in rows]
    finally:
        db.close()


@router.get("/minute")
def minute_rows(t: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")):
    """Every contract row recorded in one minute (YYYY-MM-DDTHH:MM), by strike."""
    start = datetime.fromisoformat(t)
    db = NiftySession()
    try:
        rows = (db.query(NiftyOptionSnapshot)
                .filter(NiftyOptionSnapshot.snapshot_time >= start,
                        NiftyOptionSnapshot.snapshot_time < start + timedelta(minutes=1))
                .order_by(NiftyOptionSnapshot.strike, NiftyOptionSnapshot.option_type).all())
        return [{c: getattr(r, c) for c in (
            "strike", "option_type", "moneyness_rank", "expiry", "nifty_spot", "open", "high", "low",
            "close", "volume", "oi", "oi_day_high", "oi_day_low", "buy_quantity", "sell_quantity",
            "day_volume", "bid_price", "ask_price", "spread_pct")} for r in rows]
    finally:
        db.close()


@router.post("/backfill")
def start_backfill(day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Complete a day (or, with no day, the last 7 days) from Zerodha minute history."""
    from backend.market_data import nifty_backfill
    days = [date.fromisoformat(day)] if day else nifty_backfill.recent_trading_days(7)
    if not nifty_backfill.run_async(days):
        raise HTTPException(409, "A backfill is already running")
    return {"status": "started", "days": [d.isoformat() for d in days]}


@router.get("/backfill/status")
def backfill_status():
    from backend.market_data import nifty_backfill
    return nifty_backfill.get_status()
