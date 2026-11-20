"""rate_gate.py — One gate in front of every Zerodha REST call.

Zerodha limits (per API key): quote 1/s, historical 3/s, orders 10/s,
everything else 10/s. Over the limit, requests fail -- and a failed order
status check or exit is exactly what must never happen. So every REST call
passes through here (installed on the KiteConnect client in broker.py):

  - Trading work (entries, exits, order status, fills, positions) is CRITICAL
    and only waits for its own rate slot.
  - Housekeeping (balance monitor, Nifty collector, data backfills, portfolio
    snapshot) runs inside `with background():` and additionally yields: it
    waits while any critical call is queued, for a short quiet period after
    the last critical call, and keeps a share of each budget free.

Budgets are set just under Zerodha's limits to absorb clock jitter.
"""

import contextlib
import threading
import time

_BUDGET = {            # requests per second we allow ourselves
    "quote":      0.95,
    "historical": 2.7,
    "order":      8.0,
    "other":      8.0,
}
_BG_SHARE = 0.5         # background may use at most this share of a budget
_BG_QUIET_SEC = 0.6     # ...and only this long after the last critical call

_local = threading.local()
_cond = threading.Condition()
_last_at = {k: 0.0 for k in _BUDGET}       # monotonic time of last call per category
_critical_waiting = 0
_last_critical = 0.0


def category(route: str) -> str:
    if route.startswith("market.quote"):
        return "quote"
    if route == "market.historical":
        return "historical"
    if route.startswith("order.") or route == "orders" or route == "trades":
        return "order"
    return "other"


@contextlib.contextmanager
def background():
    """Mark the calls made inside this block as low priority."""
    prev = getattr(_local, "bg", False)
    _local.bg = True
    try:
        yield
    finally:
        _local.bg = prev


def is_background() -> bool:
    return getattr(_local, "bg", False)


def try_acquire(route: str, bg: bool) -> bool:
    """Take a slot now if one is free (never waits). Used by the quote batcher
    so a background batch can't block urgent keys that arrive meanwhile."""
    global _last_critical
    cat = category(route)
    gap = 1.0 / _BUDGET[cat]
    with _cond:
        now = time.monotonic()
        need = gap if not bg else gap / _BG_SHARE
        ok = now - _last_at[cat] >= need
        if bg:
            ok = ok and _critical_waiting == 0 and now - _last_critical >= _BG_QUIET_SEC
        if ok:
            _last_at[cat] = now
            if not bg:
                _last_critical = now
        return ok


@contextlib.contextmanager
def preacquired():
    """The caller already holds a slot for the next request (see try_acquire)."""
    _local.pre = True
    try:
        yield
    finally:
        _local.pre = False


def acquire(route: str):
    global _critical_waiting, _last_critical
    cat = category(route)
    gap = 1.0 / _BUDGET[cat]
    bg = is_background()
    with _cond:
        if not bg:
            _critical_waiting += 1
        try:
            while True:
                now = time.monotonic()
                need = gap if not bg else gap / _BG_SHARE
                ready = now - _last_at[cat] >= need
                if bg:
                    ready = ready and _critical_waiting == 0 and now - _last_critical >= _BG_QUIET_SEC
                if ready:
                    _last_at[cat] = now
                    if not bg:
                        _last_critical = now
                    return
                wait = max(0.01, need - (now - _last_at[cat]))
                if bg:
                    wait = max(wait, _BG_QUIET_SEC - (now - _last_critical), 0.05)
                _cond.wait(wait)
        finally:
            if not bg:
                _critical_waiting -= 1
            _cond.notify_all()


def install(kite):
    """Route every request of this KiteConnect client through the gate."""
    if getattr(kite, "_rate_gated", False):
        return kite
    orig = kite._request

    def gated(route, method, *args, **kwargs):
        if getattr(_local, "pre", False):
            _local.pre = False          # one pre-acquired slot covers one request
        else:
            acquire(route)
        return orig(route, method, *args, **kwargs)

    kite._request = gated
    kite._rate_gated = True
    return kite
