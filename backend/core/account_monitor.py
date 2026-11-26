"""account_monitor.py — Live Zerodha account balance for the dashboard.

Zerodha has no streaming balance feed; funds are only readable through the
REST margins API, which shares the account's ~10 requests/second budget with
order placement. The balance is refreshed:

  - every REFRESH_SEC during the trading window (weekdays 09:00-11:00, when
    ES/OB enter and manage trades), and outside it only while a LIVE trade
    is still open -- the rest of the day the system just collects data, so
    polling would only spend API budget;
  - immediately whenever Zerodha reports an order event on the ticker
    WebSocket (placed, filled, cancelled, rejected) via poke(), at any hour;
  - once at start-up / sign-in, so the dashboard always has a figure.

Every refresh is pushed straight to all open dashboards over the trades
WebSocket as {"type": "account", ...}, so the screen updates the moment
Zerodha answers.
"""

import threading
from typing import Optional

from backend.core.broker import broker
from backend.core.clock import now_ist
from backend.core.market_state import market
from backend.core import rate_gate

REFRESH_SEC = 1.0
MIN_GAP_SEC = 0.35      # never more than ~3 calls/second, even under a burst of order events
WINDOW = ((9, 0), (11, 0))   # IST, weekdays: the hours the strategies trade
IDLE_CHECK_SEC = 30          # outside the window: how often to re-check (window start, open live trades)


def in_trading_window(now=None) -> bool:
    now = now or now_ist()
    hm = (now.hour, now.minute)
    return now.weekday() < 5 and WINDOW[0] <= hm < WINDOW[1]


def _live_trade_open() -> bool:
    try:
        from backend.database import Session, Trade, TradeStatus, TradeEnv
        db = Session()
        try:
            return db.query(Trade).filter(Trade.status == TradeStatus.OPEN, Trade.env == TradeEnv.LIVE).count() > 0
        finally:
            db.close()
    except Exception:
        return False


class AccountMonitor:
    def __init__(self):
        self._wake = threading.Event()
        self._running = False
        self._latest: Optional[dict] = None
        self._lock = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="AccountMonitor").start()

    def stop(self):
        self._running = False
        self._wake.set()

    def poke(self):
        """Refresh now (called on every Zerodha order update)."""
        self._wake.set()

    def latest(self) -> Optional[dict]:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def watching(self) -> bool:
        """True while the balance is polled every second."""
        return in_trading_window() or _live_trade_open()

    def _loop(self):
        import time
        while self._running:
            active = self.watching()
            poked = self._wake.wait(REFRESH_SEC if active else IDLE_CHECK_SEC)
            self._wake.clear()
            if not self._running:
                break
            # Idle hours: only an order event, the first figure of the session,
            # or the window opening triggers a fetch
            if poked or self.watching() or self._latest is None or not self._latest.get("ok"):
                self.refresh()
            time.sleep(MIN_GAP_SEC)

    def refresh(self, critical: bool = False) -> Optional[dict]:
        """Fetch the balance now. Background priority unless an order is
        waiting on it (critical=True, used by live position sizing)."""
        if not broker.has_token():
            return None
        try:
            if critical:
                eq = broker.kite().margins("equity") or {}
            else:
                with rate_gate.background():
                    eq = broker.kite().margins("equity") or {}
            av, ut = eq.get("available", {}) or {}, eq.get("utilised", {}) or {}
            data = {
                "ok":              True,
                "live_balance":    float(av.get("live_balance") or 0),
                "cash":            float(av.get("cash") or 0),
                "opening_balance": float(av.get("opening_balance") or 0),
                "intraday_payin":  float(av.get("intraday_payin") or 0),
                "net":             float(eq.get("net") or 0),
                "used_margin":     float(ut.get("debits") or 0),
                "m2m_realised":    float(ut.get("m2m_realised") or 0),
                "m2m_unrealised":  float(ut.get("m2m_unrealised") or 0),
                "fetched_at":      now_ist().isoformat(),
            }
        except Exception as e:
            prev = self.latest() or {}
            data = {**prev, "ok": False, "error": str(e), "failed_at": now_ist().isoformat()}
        data["watching"] = self.watching()     # dashboard: "live" vs "refreshes 9-11 AM"
        with self._lock:
            self._latest = data
        market.push_trade_update({"type": "account", **data})
        return data


account_monitor = AccountMonitor()
