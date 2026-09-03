"""nifty_data_router.py — Read-only API for the NIFTY options data collector.

Serves nifty_options.db (a separate DB from trading.db) for the frontend's
"Nifty Data Collector" page: collection status, latest raw snapshot rows,
and live 1-min NIFTY spot candles for the chart. Never writes; the
collector (backend/market_data/nifty_options_collector.py) is the only
writer.
"""

from datetime import date
from fastapi import APIRouter, Query

from backend.storage.nifty_options_engine import NiftySession
from backend.storage.nifty_options_models import NiftyOptionSnapshot
from backend.core.market_state import market

router = APIRouter(prefix="/api/nifty-data", tags=["nifty_data"])


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
def snapshots(limit: int = Query(200, le=2000), date_str: str = Query(None, alias="date")):
    """Latest rows first (newest snapshot_time first), flat list — the frontend
    groups by minute for display."""
    target_date = date_str or str(date.today())
    db = NiftySession()
    try:
        rows = (
            db.query(NiftyOptionSnapshot)
            .filter(NiftyOptionSnapshot.date == target_date)
            .order_by(NiftyOptionSnapshot.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "snapshot_time":  r.snapshot_time.isoformat(),
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
def candles():
    """Live 1-min NIFTY 50 spot candles for today, tick-built by market_state —
    same mechanism every stock/option candle already uses. Empty until the
    collector has resolved the chain at least once (subscribes the index)."""
    from backend.market_data.nifty_options_collector import nifty_options_collector

    token = nifty_options_collector.get_index_token()
    if not token:
        return []
    raw = market.get_1m_candles(token, include_forming=True)
    return [
        {
            "time":   c["date"].isoformat(),
            "open":   c["open"],
            "high":   c["high"],
            "low":    c["low"],
            "close":  c["close"],
        }
        for c in raw
    ]
