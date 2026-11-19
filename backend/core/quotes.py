"""quotes.py — Shared, batched access to Zerodha's quote API.

Zerodha allows one quote request per second, but each request may carry up
to 500 instruments. Before this, every caller fetched its own quote: at 9:25
five ES entries each made ~3 quote calls (liquidity, bid/ask, order price),
so the fifth entry waited ~15 s for its turn. Now:

  - get(keys) serves anything fetched within max_age from a shared cache;
  - everything still missing is merged with every other caller's request
    arriving within BATCH_WINDOW_SEC into ONE quote call;
  - the batch is critical if any caller in it is (rate_gate decides who waits).
"""

import threading
import time

from backend.core import rate_gate

BATCH_WINDOW_SEC = 0.04
MAX_KEYS = 500
WAIT_TIMEOUT_SEC = 6.0


class QuoteBatcher:
    def __init__(self):
        self._cond = threading.Condition()
        self._cache: dict = {}           # key -> (monotonic fetched_at, quote dict)
        self._pending: dict = {}         # key -> True if any critical requester
        self._tried: dict = {}           # key -> monotonic time its last batch finished (hit or miss)
        self._worker = None

    def _ensure_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True, name="QuoteBatcher")
            self._worker.start()

    def get(self, keys, max_age: float = 1.0) -> dict:
        keys = list(dict.fromkeys(keys))
        critical = not rate_gate.is_background()
        asked = time.monotonic()
        deadline = asked + WAIT_TIMEOUT_SEC
        with self._cond:
            missing = [k for k in keys if k not in self._cache or asked - self._cache[k][0] > max_age]
            if missing:
                self._ensure_worker()
                for k in missing:
                    self._pending[k] = self._pending.get(k, False) or critical
                self._cond.notify_all()
                while True:
                    still = [k for k in missing if self._tried.get(k, 0.0) < asked]
                    if not still or time.monotonic() >= deadline:
                        break
                    self._cond.wait(min(0.25, max(0.01, deadline - time.monotonic())))
            return {k: self._cache[k][1] for k in keys
                    if k in self._cache and self._cache[k][0] >= asked - max_age}

    def _run(self):
        from backend.core.broker import broker
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait()
            time.sleep(BATCH_WINDOW_SEC)          # let simultaneous callers join this batch
            # Take a rate slot without blocking, re-checking priority each time,
            # so an urgent key arriving while a background batch waits upgrades it.
            while True:
                with self._cond:
                    critical = any(self._pending.values())
                if critical:
                    rate_gate.acquire("market.quote")
                    break
                if rate_gate.try_acquire("market.quote", bg=True):
                    break
                time.sleep(0.03)
            with self._cond:                       # merge everything pending by now
                batch = dict(list(self._pending.items())[:MAX_KEYS])
                for k in batch:
                    self._pending.pop(k, None)
            try:
                with rate_gate.preacquired():
                    data = broker.kite().quote(list(batch))
            except Exception as e:
                print(f"[QUOTES] batch of {len(batch)} failed: {e}")
                data = {}
            now = time.monotonic()
            with self._cond:
                for k, v in (data or {}).items():
                    self._cache[k] = (now, v)
                # Keys Zerodha didn't return still release their waiters (as missing)
                for k in batch:
                    self._tried[k] = now
                self._cond.notify_all()


quotes = QuoteBatcher()
