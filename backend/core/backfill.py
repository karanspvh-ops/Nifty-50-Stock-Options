"""
backfill.py — Warm up indicators on startup so they don't need ~2 hours
of live ticks before producing signals.

Two sources, in order:
  1. SQLite candle history (always available, no API needed). The candle
     builder persists every completed 5-min candle; we reload the most
     recent ones per stock into memory on boot.
  2. Angel One historical API (optional enrichment). Requires a
     "Historical Data" API key in config.json as "historical_api_key".
     The default "Trading" key returns "Invalid API Key" for candles,
     so this step is skipped unless that key is provided.

Runs in a background thread; never blocks boot.
"""

import os, json, time, threading
from datetime import datetime, timedelta

from backend.core.market_state   import market
from backend.core.stock_universe import get_stocks_for_index, get_token
from backend.database import Session as DBSession, Candle

ROOT          = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THROTTLE_SEC  = 0.45
LOOKBACK_DAYS = 5
INTERVAL      = "FIVE_MINUTE"
KEEP_PER_STK  = 260

_status = {"db_loaded": 0, "api_loaded": 0, "running": False, "finished": False,
           "source": None, "note": ""}


def get_status() -> dict:
    return dict(_status)


# ── Source 1: reload from SQLite ──────────────────────────────────────────────
def _reload_from_db() -> int:
    """Seed market_state with the most recent persisted candles per symbol."""
    db = DBSession()
    loaded = 0
    try:
        symbols = [r[0] for r in db.query(Candle.symbol).distinct().all()]
        for symbol in symbols:
            token = get_token(symbol)
            if not token:
                continue
            rows = (
                db.query(Candle)
                .filter(Candle.symbol == symbol)
                .order_by(Candle.timestamp.desc())
                .limit(KEEP_PER_STK)
                .all()
            )
            if not rows:
                continue
            rows.reverse()   # chronological
            candles = [{
                "ts":     r.timestamp.isoformat(),
                "open":   r.open, "high": r.high, "low": r.low,
                "close":  r.close, "volume": r.volume,
            } for r in rows]
            market.seed_candles(token, candles)
            loaded += 1
    finally:
        db.close()
    return loaded


# ── Source 2: Zerodha Kite historical (needs Historical Data subscription) ────
def _fetch_kite(kite, token: str) -> int:
    """Pull recent 5-min candles via Kite. Returns -1 on API failure."""
    try:
        to_dt   = datetime.now()
        from_dt = to_dt - timedelta(days=LOOKBACK_DAYS)
        rows = kite.historical_data(int(token), from_dt, to_dt, "5minute")
        if not rows:
            return 0
        candles = [{
            "ts": r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"]),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": int(r.get("volume", 0)),
        } for r in rows]
        market.seed_candles(token, candles)
        return len(candles)
    except Exception:
        return -1


# ── Orchestrator ──────────────────────────────────────────────────────────────
def _run():
    _status.update({"running": True, "finished": False})

    # 1) DB reload (instant, offline)
    db_loaded = _reload_from_db()
    _status["db_loaded"] = db_loaded
    _status["source"]    = "db"
    print(f"[BACKFILL] Reloaded candle history for {db_loaded} stocks from SQLite.")

    # 2) Kite historical enrichment (if logged in + subscription active)
    from backend.core.broker import broker
    if not broker.has_token():
        _status.update({"running": False, "finished": True,
                        "note": "No Zerodha token — SQLite warmup only."})
        print(f"[BACKFILL] {_status['note']}")
        return

    kite   = broker.kite()
    stocks = get_stocks_for_index("NIFTY200")
    print(f"[BACKFILL] Enriching {len(stocks)} stocks via Kite historical...")
    api_loaded = 0
    for i, s in enumerate(stocks):
        n = _fetch_kite(kite, s["token"])
        if n == -1 and i == 0:
            _status["note"] = "Kite historical unavailable (needs Historical Data subscription). SQLite warmup only."
            print(f"[BACKFILL] {_status['note']}")
            break
        if n > 0:
            api_loaded += 1
            _status["api_loaded"] = api_loaded
        time.sleep(THROTTLE_SEC)

    _status.update({"running": False, "finished": True, "source": "db+kite"})
    print(f"[BACKFILL] Complete. DB: {db_loaded} stocks, Kite enriched: {api_loaded}.")


def start_backfill():
    if _status["running"]:
        return
    threading.Thread(target=_run, daemon=True, name="Backfill").start()
