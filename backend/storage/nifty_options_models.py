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
from sqlalchemy import Column, Integer, Float, String, DateTime, JSON, UniqueConstraint

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
    oi_day_high      = Column(Integer, nullable=True)   # from Kite quote() — today's OI range so far
    oi_day_low       = Column(Integer, nullable=True)

    # Pending order-book quantities from Kite quote() — NOT open interest (OI has no
    # buyer/seller split at the exchange level; every open contract has exactly one of
    # each). These are live order-flow demand/supply, commonly mislabeled "buy/sell OI"
    # on retail platforms.
    buy_quantity     = Column(Integer, nullable=True)
    sell_quantity    = Column(Integer, nullable=True)
    day_volume       = Column(Integer, nullable=True)   # cumulative day volume from quote() — distinct from `volume` (1-min bar volume) above

    bid_price        = Column(Float,   nullable=True)   # top-of-book
    ask_price        = Column(Float,   nullable=True)
    spread_pct       = Column(Float,   nullable=True)
    bid_depth        = Column(JSON,    nullable=True)   # up to 5 levels: [{"price":..,"qty":..,"orders":..}, ...]
    ask_depth        = Column(JSON,    nullable=True)

    created_at       = Column(DateTime, default=datetime.utcnow)


class NiftySpotCandle(NiftyBase):
    """Persisted NIFTY 50 index 1-min candles — one row per completed minute.

    market_state's tick-built candles (market.get_1m_candles) are in-memory
    only and reset on every backend restart. Persisting each closed candle
    here lets the frontend chart resume from the last recorded candle across
    a restart instead of starting blank, and makes multi-day history
    (7d/30d/90d/all) possible at all -- market_state only ever holds today.
    """
    __tablename__ = "nifty_spot_candles"
    __table_args__ = (UniqueConstraint("candle_time", name="uq_nifty_spot_candle_time"),)

    id          = Column(Integer, primary_key=True, autoincrement=True)
    date        = Column(String,   nullable=False, index=True)   # YYYY-MM-DD
    candle_time = Column(DateTime, nullable=False, index=True)   # candle open/minute timestamp (IST)

    open        = Column(Float, nullable=False)
    high        = Column(Float, nullable=False)
    low         = Column(Float, nullable=False)
    close       = Column(Float, nullable=False)
    volume      = Column(Integer, nullable=True)

    created_at  = Column(DateTime, default=datetime.utcnow)
