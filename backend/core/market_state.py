"""
market_state.py — Thread-safe in-memory store for all live market data.

Single global instance (MarketState) shared across:
  - Tick engine (writer)
  - Candle builder (writer)
  - Trading logic (reader)
  - FastAPI WebSocket relay (reader)

No Redis required — pure Python threading.Lock().
"""

import threading
from datetime import datetime
from typing import Dict, List, Optional


class MarketState:
    def __init__(self):
        self._lock = threading.Lock()

        # token → latest tick
        # {"ltp": float, "volume": int, "timestamp": datetime, "prev_close": float}
        self._ticks: Dict[str, dict] = {}

        # token → {"pct_change": float, "direction": "up"/"down"/"flat"}
        self._stock_moves: Dict[str, dict] = {}

        # sector → {"pct_change": float, "direction": str, "stocks": [...]}
        self._sector_moves: Dict[str, dict] = {}

        # token → list of completed 5-min candles (max 100 kept in memory)
        # Each candle: {"ts": str, "open": f, "high": f, "low": f, "close": f, "volume": int}
        self._candles: Dict[str, List[dict]] = {}

        # Global feed health
        self._feed_connected: bool = False
        self._feed_last_tick: Optional[datetime] = None
        self._feed_error: Optional[str] = None

        # Trading halt flag (set by portfolio SL or feed disconnect)
        self._trading_halted: bool = False
        self._halt_reason: str = ""

    # ── Tick updates ──────────────────────────────────────────────────────────

    def update_tick(self, token: str, ltp: float, volume: int,
                    prev_close: float, timestamp: datetime):
        with self._lock:
            self._ticks[token] = {
                "ltp":        ltp,
                "volume":     volume,
                "prev_close": prev_close,
                "timestamp":  timestamp,
            }
            self._feed_last_tick = timestamp

            # Compute % move
            if prev_close and prev_close > 0:
                pct = round(((ltp - prev_close) / prev_close) * 100, 2)
                self._stock_moves[token] = {
                    "pct_change": pct,
                    "direction":  "up" if pct > 0 else ("down" if pct < 0 else "flat"),
                    "ltp":        ltp,
                }

    def get_tick(self, token: str) -> Optional[dict]:
        with self._lock:
            return self._ticks.get(token)

    def get_ltp(self, token: str) -> Optional[float]:
        with self._lock:
            t = self._ticks.get(token)
            return t["ltp"] if t else None

    def get_all_ticks(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._ticks)

    def get_stock_move(self, token: str) -> Optional[dict]:
        with self._lock:
            return self._stock_moves.get(token)

    def get_all_stock_moves(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._stock_moves)

    # ── Sector moves ──────────────────────────────────────────────────────────

    def update_sector_moves(self, sector_data: Dict[str, dict]):
        """Called by sector scanner every tick batch."""
        with self._lock:
            self._sector_moves = sector_data

    def get_sector_moves(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._sector_moves)

    def get_top_sector(self) -> Optional[dict]:
        """Return the sector with highest absolute % move."""
        with self._lock:
            if not self._sector_moves:
                return None
            return max(
                self._sector_moves.items(),
                key=lambda x: abs(x[1].get("pct_change", 0))
            )

    # ── Candles ───────────────────────────────────────────────────────────────

    def push_candle(self, token: str, candle: dict):
        with self._lock:
            if token not in self._candles:
                self._candles[token] = []
            self._candles[token].append(candle)
            # Keep last 100 candles per stock
            if len(self._candles[token]) > 100:
                self._candles[token] = self._candles[token][-100:]

    def get_candles(self, token: str, n: int = 50) -> List[dict]:
        with self._lock:
            return list(self._candles.get(token, []))[-n:]

    def get_latest_candle(self, token: str) -> Optional[dict]:
        with self._lock:
            candles = self._candles.get(token, [])
            return candles[-1] if candles else None

    # ── Feed health ───────────────────────────────────────────────────────────

    def set_feed_connected(self, connected: bool, error: str = ""):
        with self._lock:
            self._feed_connected = connected
            self._feed_error     = error if not connected else ""
            if not connected:
                # Critical: halt all trading on feed loss
                self._trading_halted = True
                self._halt_reason    = f"Feed disconnected: {error or 'unknown'}"

    def set_feed_reconnected(self):
        with self._lock:
            self._feed_connected  = True
            self._feed_error      = ""
            self._trading_halted  = False
            self._halt_reason     = ""

    def is_feed_connected(self) -> bool:
        with self._lock:
            return self._feed_connected

    def get_feed_health(self) -> dict:
        with self._lock:
            return {
                "connected":  self._feed_connected,
                "last_tick":  str(self._feed_last_tick) if self._feed_last_tick else None,
                "error":      self._feed_error,
            }

    # ── Trading halt ──────────────────────────────────────────────────────────

    def halt_trading(self, reason: str):
        with self._lock:
            self._trading_halted = True
            self._halt_reason    = reason

    def resume_trading(self):
        with self._lock:
            if self._feed_connected:
                self._trading_halted = False
                self._halt_reason    = ""

    def is_trading_halted(self) -> bool:
        with self._lock:
            return self._trading_halted

    def get_halt_reason(self) -> str:
        with self._lock:
            return self._halt_reason

    def get_status(self) -> dict:
        with self._lock:
            return {
                "feed_connected":  self._feed_connected,
                "trading_halted":  self._trading_halted,
                "halt_reason":     self._halt_reason,
                "stocks_tracked":  len(self._ticks),
                "last_tick":       str(self._feed_last_tick) if self._feed_last_tick else None,
            }


# ── Global singleton ──────────────────────────────────────────────────────────
market = MarketState()
