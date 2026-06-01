"""
candle_builder.py — Builds 5-minute OHLCV candles from raw ticks.

One CandleBuilder instance per stock token.
Completed candles are:
  1. Pushed to MarketState (in-memory)
  2. Written to SQLite candles table
"""

from datetime import datetime, timedelta
from typing import Optional
from backend.core.market_state import market
from backend.database import Session, Candle


class CandleBuilder:
    def __init__(self, token: str, symbol: str, interval_minutes: int = 5):
        self.token    = token
        self.symbol   = symbol
        self.interval = interval_minutes

        # Current open candle
        self._open:      Optional[float] = None
        self._high:      Optional[float] = None
        self._low:       Optional[float] = None
        self._close:     Optional[float] = None
        self._volume:    int = 0
        self._candle_ts: Optional[datetime] = None  # start of current bar

    def _bar_start(self, ts: datetime) -> datetime:
        """Snap timestamp to the nearest N-minute bar start."""
        minute = (ts.minute // self.interval) * self.interval
        return ts.replace(minute=minute, second=0, microsecond=0)

    def on_tick(self, ltp: float, volume: int, ts: datetime):
        """Process a single tick. Emits a candle when the bar closes."""
        bar = self._bar_start(ts)

        if self._candle_ts is None:
            # First tick ever
            self._open_bar(bar, ltp)
        elif bar > self._candle_ts:
            # New bar started — close previous
            self._close_bar()
            self._open_bar(bar, ltp)

        # Update OHLCV
        if ltp > (self._high or 0):
            self._high = ltp
        if ltp < (self._low or float("inf")):
            self._low = ltp
        self._close  = ltp
        self._volume += volume

    def _open_bar(self, bar: datetime, price: float):
        self._candle_ts = bar
        self._open  = price
        self._high  = price
        self._low   = price
        self._close = price
        self._volume = 0

    def _close_bar(self):
        if self._candle_ts is None or self._open is None:
            return

        candle = {
            "ts":     self._candle_ts.isoformat(),
            "open":   self._open,
            "high":   self._high,
            "low":    self._low,
            "close":  self._close,
            "volume": self._volume,
        }

        # 1. Push to market state
        market.push_candle(self.token, candle)

        # 2. Persist to SQLite (best-effort, non-blocking)
        self._persist(candle)

    def _persist(self, candle: dict):
        db = Session()
        try:
            row = Candle(
                symbol    = self.symbol,
                interval  = f"{self.interval}m",
                timestamp = datetime.fromisoformat(candle["ts"]),
                open      = candle["open"],
                high      = candle["high"],
                low       = candle["low"],
                close     = candle["close"],
                volume    = candle["volume"],
            )
            db.add(row)
            db.commit()
        except Exception as e:
            print(f"[CANDLE] Persist error {self.symbol}: {e}")
        finally:
            db.close()


# ── Global registry of candle builders ────────────────────────────────────────
_builders: dict[str, CandleBuilder] = {}

def get_builder(token: str, symbol: str) -> CandleBuilder:
    if token not in _builders:
        _builders[token] = CandleBuilder(token, symbol)
    return _builders[token]

def process_tick(token: str, symbol: str, ltp: float, volume: int, ts: datetime):
    """Entry point called by tick engine for every incoming tick."""
    builder = get_builder(token, symbol)
    builder.on_tick(ltp, volume, ts)
