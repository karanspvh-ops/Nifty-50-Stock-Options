"""sl_confirm.py — Confirm a stop-loss breach before acting on it.

Thinly traded stock options print stray trades far below the real market
(BLUESTARCO 19 Nov: one print at 23.10 under a 23.18 stop while the bid sat
at 23.70; AUBANK 17 Nov: a 12.95 print with the bid at 15.50). Acting on the
first print below the stop sold positions the market never actually offered
that low. A breach now counts only when it is confirmed:

  1. Tick path (on_tick): the price is still at or below the stop on a later
     tick at least CONFIRM_SEC after the first breaching tick. Any tick back
     above the stop clears the breach.
  2. Loop path (on_poll): the 1-second manage loops and the risk checker only
     see the latest LTP, which may be that same stray print. They never count
     as a second observation. If a breach has gone STALE_SEC without a
     confirming tick (illiquid option, no further trades), the real bid is
     fetched from Zerodha and decides: bid at or below the stop -> exit, bid
     above -> stray print, breach cleared.

One shared instance, so the strategy tick handlers, their loops and the risk
checker all agree on the same breach state per trade.
"""

import threading
import time
from typing import Callable, Optional

CONFIRM_SEC = 1.0
STALE_SEC   = 3.0


class SLConfirm:
    def __init__(self):
        self._lock = threading.Lock()
        self._first: dict = {}      # trade_id -> (monotonic time of first breach, ticks seen)

    def on_tick(self, trade_id: int, price: float, stop: float) -> bool:
        """Feed every live tick. True when the breach is confirmed."""
        now = time.monotonic()
        with self._lock:
            if price > stop:
                self._first.pop(trade_id, None)
                return False
            t0, n = self._first.get(trade_id, (now, 0))
            n += 1
            self._first[trade_id] = (t0, n)
            return n >= 2 and now - t0 >= CONFIRM_SEC

    def on_poll(self, trade_id: int, price: float, stop: float,
                bid_fn: Callable[[], Optional[float]]) -> bool:
        """Feed loop/checker observations (latest LTP, not a new trade).
        True when the breach is confirmed, by an aged breach and the real bid."""
        now = time.monotonic()
        with self._lock:
            if price > stop:
                self._first.pop(trade_id, None)
                return False
            if trade_id not in self._first:
                # First sight of a breach (e.g. before the tick callback was
                # registered). Start the clock; ticks or a later poll decide.
                self._first[trade_id] = (now, 1)
                return False
            t0, n = self._first[trade_id]
            if n >= 2 and now - t0 >= CONFIRM_SEC:
                return True             # already confirmed by ticks
            if now - t0 < STALE_SEC:
                return False

        bid = None
        try:
            bid = bid_fn()
        except Exception:
            pass
        if bid is None:
            # No depth available: the breach has persisted STALE_SEC with no
            # tick back above the stop -- treat as real.
            return True
        if bid <= stop:
            return True
        print(f"[SL] trade #{trade_id}: last price {price} under stop {stop} "
              f"but bid is {bid} -- ignoring stray print")
        with self._lock:
            self._first.pop(trade_id, None)
        return False

    def clear(self, trade_id: int):
        with self._lock:
            self._first.pop(trade_id, None)


sl_confirm = SLConfirm()


def bid_for(option_symbol: str) -> Callable[[], Optional[float]]:
    """Lazy bid lookup (one Kite quote) used only when a breach goes stale."""
    def _fn():
        from backend.core.stock_universe import get_option_token
        from backend.execution.option_selector import option_bid_ask
        bid, _, _ = option_bid_ask(get_option_token(option_symbol), option_symbol)
        return bid
    return _fn
