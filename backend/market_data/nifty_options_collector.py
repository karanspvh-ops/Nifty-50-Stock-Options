"""nifty_options_collector.py — per-minute NIFTY option chain data collector.

Purely additive background module: subscribes 22 NIFTY option contracts
(5 ITM strikes + ATM + 5 OTM strikes, both CE and PE, nearest expiry) on
the SAME shared Zerodha WebSocket connection the strategies already use
(via tick_engine.subscribe_options — no new connection), then once a
minute writes their 1-min OHLCV + OI + bid/ask depth to a separate DB
(nifty_options.db). Never touches trading.db, never calls strategy code,
never places orders.

Data collected is deliberately raw (no Greeks) — see
backend/storage/nifty_options_models.py for why.
"""

import threading
import time as _time
from datetime import date, timedelta

from backend.core.clock        import now_ist
from backend.core.market_state import market
from backend.core.broker       import broker

from backend.storage.nifty_options_engine import NiftySession, init_nifty_options_db
from backend.storage.nifty_options_models import NiftyOptionSnapshot

_LADDER_SIZE   = 5      # strikes each side of ATM
_MARKET_OPEN   = (9, 15)
_MARKET_CLOSE  = (15, 30)


class NiftyOptionsCollector:
    """Background collector for the NIFTY option chain. Touch only this file
    for what gets captured or how often — see nifty_options_models.py for schema."""

    def __init__(self):
        self._running   = False
        self._thread    = None
        self._today     = None
        self._contracts = {}   # token(str) -> {"tradingsymbol", "strike", "option_type", "moneyness_rank"}
        self._expiry    = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        init_nifty_options_db()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NiftyOptionsCollector")
        self._thread.start()
        print("[NIFTY-DATA] Collector started.")

    def stop(self):
        self._running = False

    # ── Chain resolution (once per day) ─────────────────────────────────────────

    def _resolve_chain(self):
        """Fetch NIFTY option instruments fresh from Kite, pick nearest expiry,
        build an 11-strike ladder around ATM, subscribe on the shared WS feed."""
        if not broker.has_token():
            return False
        try:
            kite = broker.kite()
            nfo  = kite.instruments("NFO")
        except Exception as e:
            print(f"[NIFTY-DATA] instrument fetch failed: {e}")
            return False

        today = date.today()
        opts = [i for i in nfo if i.get("name") == "NIFTY" and i.get("instrument_type") in ("CE", "PE")]
        if not opts:
            print("[NIFTY-DATA] No NIFTY option instruments found.")
            return False

        def exp_date(i):
            e = i.get("expiry")
            try:    return e if isinstance(e, date) else date.fromisoformat(str(e))
            except Exception: return date(2099, 1, 1)

        future = [i for i in opts if exp_date(i) >= today]
        if not future:
            print("[NIFTY-DATA] No future NIFTY expiries found.")
            return False
        nearest = min(exp_date(i) for i in future)
        chain   = [i for i in future if exp_date(i) == nearest]

        try:
            q    = kite.quote(["NSE:NIFTY 50"])
            spot = float(q["NSE:NIFTY 50"]["last_price"])
        except Exception as e:
            print(f"[NIFTY-DATA] spot fetch failed: {e}")
            return False

        strikes = sorted({float(i["strike"]) for i in chain})
        if not strikes:
            return False
        atm_idx = min(range(len(strikes)), key=lambda k: abs(strikes[k] - spot))
        lo = max(0, atm_idx - _LADDER_SIZE)
        hi = min(len(strikes), atm_idx + _LADDER_SIZE + 1)
        ladder = strikes[lo:hi]
        rank_of = {s: (i - (atm_idx - lo)) for i, s in enumerate(ladder)}   # -5..0..+5

        contracts = {}
        subs = []
        for i in chain:
            strike = float(i["strike"])
            if strike not in rank_of:
                continue
            tok = str(i["instrument_token"])
            contracts[tok] = {
                "tradingsymbol":  i["tradingsymbol"],
                "strike":         strike,
                "option_type":    i["instrument_type"],
                "moneyness_rank": rank_of[strike],
            }
            subs.append({"token": tok, "tradingsymbol": i["tradingsymbol"], "name": "NIFTY"})

        try:
            from backend.core.tick_engine import tick_engine
            tick_engine.subscribe_options(subs)
        except Exception as e:
            print(f"[NIFTY-DATA] subscribe failed: {e}")
            return False

        self._contracts = contracts
        self._expiry    = nearest.isoformat()
        self._today     = today
        print(f"[NIFTY-DATA] Chain resolved: expiry={self._expiry} spot={spot:.1f} "
              f"strikes={ladder} contracts={len(contracts)}")
        return True

    # ── 1-minute collection loop ─────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                now = now_ist()
                t   = now.time()
                if not ((t.hour, t.minute) >= _MARKET_OPEN and (t.hour, t.minute) <= _MARKET_CLOSE):
                    _time.sleep(30)
                    continue
                if self._today != date.today() or not self._contracts:
                    if not self._resolve_chain():
                        _time.sleep(30)
                        continue
                if market.is_feed_connected():
                    self._collect_once()
            except Exception as e:
                print(f"[NIFTY-DATA] loop error: {e}")

            # Sleep to the next minute boundary
            now = now_ist()
            sleep_s = 60 - now.second - now.microsecond / 1_000_000
            _time.sleep(max(1.0, sleep_s))

    def _collect_once(self):
        if not self._contracts:
            return
        kite = broker.kite()
        keys = [f"NFO:{c['tradingsymbol']}" for c in self._contracts.values()] + ["NSE:NIFTY 50"]
        try:
            q = kite.quote(keys)
        except Exception as e:
            print(f"[NIFTY-DATA] quote fetch error: {e}")
            return

        spot = None
        idx_data = q.get("NSE:NIFTY 50")
        if idx_data:
            spot = float(idx_data.get("last_price") or 0) or None

        now   = now_ist()
        today = str(now.date())
        rows  = []

        # market_state closes a candle lazily — only on the first tick of the NEXT
        # minute. Right at our own minute-boundary wake-up, the just-finished minute's
        # final OHLC is still sitting in the "forming" slot (untouched since the minute
        # ended) unless a new tick has already rolled it over. So: fetch WITH forming
        # included, then pick the entry that actually matches the minute we want —
        # don't blindly trust [-1], which can silently be one minute stale or one
        # minute early depending on whether a next-minute tick has landed yet.
        target_minute = (now.replace(second=0, microsecond=0) - timedelta(minutes=1))

        for tok, meta in self._contracts.items():
            key  = f"NFO:{meta['tradingsymbol']}"
            data = q.get(key, {})

            candles = market.get_1m_candles(tok, include_forming=True)
            last = next((c for c in reversed(candles) if c["date"] == target_minute), None)
            if last is None and candles:
                last = candles[-1]   # fallback: illiquid strike, no tick yet this cycle

            depth      = data.get("depth", {}) or {}
            buy_levels  = depth.get("buy",  []) or []
            sell_levels = depth.get("sell", []) or []
            bid = float(buy_levels[0]["price"])  if buy_levels  and buy_levels[0]["price"]  > 0 else None
            ask = float(sell_levels[0]["price"]) if sell_levels and sell_levels[0]["price"] > 0 else None
            spread_pct = round((ask - bid) / ((ask + bid) / 2) * 100, 2) if bid and ask and (bid + ask) > 0 else None

            rows.append(NiftyOptionSnapshot(
                date=today, snapshot_time=now,
                expiry=self._expiry, strike=meta["strike"], option_type=meta["option_type"],
                moneyness_rank=meta["moneyness_rank"],
                nifty_spot=spot,
                open=last["open"] if last else None, high=last["high"] if last else None,
                low=last["low"] if last else None, close=last["close"] if last else None,
                volume=last["volume"] if last else None,
                oi=data.get("oi"),
                bid_price=bid, ask_price=ask, spread_pct=spread_pct,
                bid_depth=buy_levels[:5], ask_depth=sell_levels[:5],
            ))

        db = NiftySession()
        try:
            db.add_all(rows)
            db.commit()
        except Exception as e:
            print(f"[NIFTY-DATA] DB write error: {e}")
        finally:
            db.close()


nifty_options_collector = NiftyOptionsCollector()
