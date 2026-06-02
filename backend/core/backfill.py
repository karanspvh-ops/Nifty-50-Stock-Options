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


# ── Source 2: Angel historical API (optional) ─────────────────────────────────
def _load_config() -> dict:
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


def _historical_session():
    """Build a SmartConnect session using the historical_api_key, if present."""
    cfg = _load_config()
    hist_key = cfg.get("historical_api_key")
    if not hist_key:
        return None, "no historical_api_key in config"
    try:
        import pyotp
        from SmartApi import SmartConnect
        smart = SmartConnect(api_key=hist_key)
        data  = smart.generateSession(cfg["client_id"], cfg["mpin"],
                                      pyotp.TOTP(cfg["totp_secret"]).now())
        if not data.get("status"):
            return None, f"historical login failed: {data.get('message')}"
        return smart, "ok"
    except Exception as e:
        return None, f"historical login error: {e}"


def _fetch_api(smart, token: str) -> int:
    to_dt   = datetime.now()
    from_dt = to_dt - timedelta(days=LOOKBACK_DAYS)
    resp = smart.getCandleData({
        "exchange": "NSE", "symboltoken": token, "interval": INTERVAL,
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate":   to_dt.strftime("%Y-%m-%d %H:%M"),
    })
    if not isinstance(resp, dict) or not resp.get("status") or not resp.get("data"):
        return -1   # signal failure (e.g. invalid key)
    candles = [{
        "ts": row[0], "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]), "volume": int(row[5]),
    } for row in resp["data"]]
    if candles:
        market.seed_candles(token, candles)
    return len(candles)


# ── Orchestrator ──────────────────────────────────────────────────────────────
def _run():
    _status.update({"running": True, "finished": False})

    # 1) DB reload (instant, offline)
    db_loaded = _reload_from_db()
    _status["db_loaded"] = db_loaded
    _status["source"]    = "db"
    print(f"[BACKFILL] Reloaded candle history for {db_loaded} stocks from SQLite.")

    # 2) Optional Angel historical enrichment
    smart, why = _historical_session()
    if smart is None:
        _status["note"] = f"Historical API skipped ({why}). Using SQLite warmup."
        print(f"[BACKFILL] {_status['note']}")
        _status.update({"running": False, "finished": True})
        return

    stocks = get_stocks_for_index("NIFTY200")
    print(f"[BACKFILL] Historical key found — enriching {len(stocks)} stocks...")
    api_loaded = 0
    for i, s in enumerate(stocks):
        n = _fetch_api(smart, s["token"])
        if n == -1:
            if i == 0:
                _status["note"] = "Historical key present but API rejected it."
                print(f"[BACKFILL] {_status['note']} Falling back to SQLite warmup.")
                break
        elif n > 0:
            api_loaded += 1
            _status["api_loaded"] = api_loaded
        time.sleep(THROTTLE_SEC)

    _status.update({"running": False, "finished": True, "source": "db+api"})
    print(f"[BACKFILL] Complete. DB: {db_loaded} stocks, API enriched: {api_loaded}.")


def start_backfill():
    if _status["running"]:
        return
    threading.Thread(target=_run, daemon=True, name="Backfill").start()
