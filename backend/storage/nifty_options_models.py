"""nifty_options_models.py — schema for per-minute NIFTY option chain snapshots.

One row per (strike, option_type) per completed 1-minute candle. Bound to
NiftyBase / nifty_options.db (backend/storage/nifty_options_engine.py) — a
separate database from trading.db.

Deliberately excludes computed Greeks (delta/gamma/theta/vega/IV) — Kite
Connect doesn't provide them, and they're not needed to store this data.
nifty_spot + strike + expiry + close (premium) + snapshot_time are enough
to compute them later via Black-Scholes, whenever that's built.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, JSON

from backend.storage.nifty_options_engine import NiftyBase


class NiftyOptionSnapshot(NiftyBase):
    __tablename__ = "nifty_option_snapshots"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    date             = Column(String,   nullable=False, index=True)   # YYYY-MM-DD
    snapshot_time    = Column(DateTime, nullable=False, index=True)   # candle close timestamp (IST)

    expiry           = Column(String,  nullable=False)                # YYYY-MM-DD
    strike           = Column(Float,   nullable=False)
    option_type      = Column(String,  nullable=False)                # CE / PE
    moneyness_rank   = Column(Integer, nullable=False)                # -5..-1 ITM, 0 ATM, +1..+5 OTM

    nifty_spot       = Column(Float,   nullable=True)   # NIFTY 50 index LTP at this minute — needed for Greeks later

    # 1-min OHLCV (tick-built, from market_state — same mechanism as highest_price tracking)
    open             = Column(Float,   nullable=True)
    high             = Column(Float,   nullable=True)
    low              = Column(Float,   nullable=True)
    close            = Column(Float,   nullable=True)
    volume           = Column(Integer, nullable=True)

    oi               = Column(Integer, nullable=True)

    bid_price        = Column(Float,   nullable=True)   # top-of-book
    ask_price        = Column(Float,   nullable=True)
    spread_pct       = Column(Float,   nullable=True)
    bid_depth        = Column(JSON,    nullable=True)   # up to 5 levels: [{"price":..,"qty":..,"orders":..}, ...]
    ask_depth        = Column(JSON,    nullable=True)

    created_at       = Column(DateTime, default=datetime.utcnow)
