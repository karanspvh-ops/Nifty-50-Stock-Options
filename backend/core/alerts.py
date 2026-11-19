"""alerts.py — Live-trading warnings that must reach the screen at once.

Raised when the system can't do something it must (sell a position, place
its stop at Zerodha) or finds Zerodha and its own records disagreeing.
Each alert has a stable key, so repeating the same problem updates one
alert instead of flooding. Pushed to every dashboard over the trades
WebSocket and printed loudly to the backend console.
"""

import threading
from typing import Optional

from backend.core.clock import now_ist
from backend.core.market_state import market

_lock = threading.Lock()
_active: dict = {}       # key -> alert dict


def _push():
    market.push_trade_update({"type": "alerts", "alerts": active()})


def raise_alert(key: str, message: str, level: str = "critical",
                trade_id: Optional[int] = None, symbol: Optional[str] = None):
    with _lock:
        prev = _active.get(key)
        _active[key] = {
            "key": key, "level": level, "message": message,
            "trade_id": trade_id, "symbol": symbol,
            "first_at": prev["first_at"] if prev else now_ist().isoformat(),
            "last_at": now_ist().isoformat(),
            "count": (prev["count"] + 1) if prev else 1,
        }
        changed = not prev or prev["message"] != message
    if changed:
        print(f"[ALERT:{level.upper()}] {message}")
    _push()


def clear_alert(key: str):
    with _lock:
        existed = _active.pop(key, None) is not None
    if existed:
        _push()


def clear_prefix(prefix: str):
    with _lock:
        gone = [k for k in _active if k.startswith(prefix)]
        for k in gone:
            _active.pop(k)
    if gone:
        _push()


def active() -> list:
    with _lock:
        return sorted(_active.values(), key=lambda a: a["first_at"])
