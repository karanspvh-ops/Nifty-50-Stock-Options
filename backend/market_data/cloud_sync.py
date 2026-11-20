"""cloud_sync.py — Keep the Cloudflare NIFTY collector supplied, and drain it.

The team site's Worker is the only NIFTY collector (frontend/worker/collector.js):
every minute in market hours it stores option snapshots in Cloudflare D1,
whether or not this backend is running. This module, inside the backend:

  1. sends today's Zerodha access token + API key the moment a session exists
     (checked every TICK_SEC, and immediately after a login via poke())
  2. sends the NIFTY option list for the next two expiries, once a day
  3. pulls collected rows into nifty_options.db -- never while trading:
       before 09:00   everything, then the OHLC completion for past days
       09:00-15:40    only previous days' rows, and only with no ES/OB trade open
       after 15:40    everything incl. today, then today's OHLC completion
     Rows are committed locally first; only then does Cloudflare delete
     exactly those rows, so nothing is lost and its storage stays small.

Minute OHLC and volume aren't in a quote snapshot; nifty_backfill fills them
from Zerodha's minute history right after a full sync.
"""

import json
import os
import threading
import time
from datetime import date, datetime

import requests

from backend.core.clock import now_ist

_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TICK_SEC = 30            # session/option-list check
PULL_SEC = 300           # pull cadence inside an allowed window
BATCH = 1000
PREMARKET_END = (9, 0)
AFTER_CLOSE = (15, 40)
_FIELDS = ("nifty_spot", "close", "oi", "oi_day_high", "oi_day_low", "buy_quantity", "sell_quantity",
           "day_volume", "bid_price", "ask_price", "spread_pct", "bid_depth", "ask_depth")


def _config() -> dict:
    try:
        with open(os.path.join(_PROJECT, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class CloudSync:
    def __init__(self):
        self._running = False
        self._sent_token = None        # (date, token) last sent
        self._sent_chain = None        # date the chain was last sent
        self._next_pull = 0.0
        self._completed_on = None      # date the post-sync OHLC completion last ran
        self._wake = threading.Event()
        self.last_run = None
        self.last_pulled = 0
        self.error = ""

    def _site(self):
        cfg = _config()
        relay, secret = cfg.get("desk_relay"), cfg.get("desk_relay_secret")
        if not relay or not secret:
            return None, None
        return relay.replace("wss://", "https://").rsplit("/relay", 1)[0], secret

    def start(self):
        site, _ = self._site()
        if not site:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="CloudSync").start()

    def stop(self):
        self._running = False
        self._wake.set()

    def poke(self):
        """A Zerodha login just happened: send the new session to Cloudflare now."""
        self._wake.set()

    def status(self) -> dict:
        return {"last_run": self.last_run, "rows_pulled_last_run": self.last_pulled, "error": self.error or None}

    def _loop(self):
        time.sleep(20)                 # let the broker session and instrument cache load
        while self._running:
            try:
                self.run_once()
                self.error = ""
            except Exception as e:
                self.error = str(e)
                print(f"[CLOUD-SYNC] {e}")
            self._wake.wait(TICK_SEC)
            self._wake.clear()

    @staticmethod
    def _open_trades() -> int:
        from backend.database import Session, Trade, TradeStatus
        db = Session()
        try:
            return db.query(Trade).filter(Trade.status == TradeStatus.OPEN).count()
        finally:
            db.close()

    def run_once(self, force_pull: bool = False):
        site, secret = self._site()
        if not site:
            return
        h = {"X-Relay-Secret": secret}
        self._push_session(site, h)
        self._push_chain(site, h)

        if not force_pull and time.monotonic() < self._next_pull:
            return
        self._next_pull = time.monotonic() + PULL_SEC
        now = now_ist()
        hm, today = (now.hour, now.minute), now.date()
        full = hm < PREMARKET_END or hm >= AFTER_CLOSE or now.weekday() >= 5
        if not full and self._open_trades():
            return                      # trading in progress: sync later
        before = None if full else today.isoformat()
        self.last_pulled = self._pull(site, h, before_date=before)
        self.last_run = now.strftime("%H:%M:%S")

        # OHLC/volume completion once the day's cloud rows are local
        if full and self._completed_on != today:
            from backend.market_data import nifty_backfill
            if nifty_backfill.run_async(nifty_backfill.recent_trading_days(7)):
                self._completed_on = today
                print("[CLOUD-SYNC] Started the OHLC/volume completion for the last 7 trading days")

    def _push_session(self, site, h):
        from backend.core.broker import broker
        token = broker.access_token
        today = now_ist().date().isoformat()
        if not token or self._sent_token == (today, token):
            return
        r = requests.post(f"{site}/sync/kite", headers=h, timeout=15,
                          json={"api_key": broker.api_key, "access_token": token, "date": today})
        r.raise_for_status()
        self._sent_token = (today, token)
        print("[CLOUD-SYNC] Today's Zerodha session sent to the cloud collector")

    def _push_chain(self, site, h):
        from backend.universe.instrument_cache import get_nifty_option_chain
        today = now_ist().date().isoformat()
        if self._sent_chain == today:
            return
        chain = get_nifty_option_chain()
        expiries = sorted({str(m.get("expiry")) for m in chain.values() if str(m.get("expiry")) >= today})[:2]
        contracts = [{"s": m["tradingsymbol"], "k": float(m["strike"]), "t": m["instrument_type"], "e": str(m["expiry"])}
                     for m in chain.values() if str(m.get("expiry")) in expiries and m.get("instrument_type") in ("CE", "PE")]
        if not contracts:
            return
        r = requests.post(f"{site}/sync/chain", headers=h, timeout=20, json={"contracts": contracts})
        r.raise_for_status()
        self._sent_chain = today
        print(f"[CLOUD-SYNC] NIFTY option list sent ({len(contracts)} contracts, expiries {', '.join(expiries)})")

    def _pull(self, site, h, before_date=None) -> int:
        """Merge what the cloud collected (optionally only days before before_date),
        deleting exactly those rows there, batch by batch, once saved here."""
        pulled = 0
        params = {"after": 0, "limit": BATCH}
        if before_date:
            params["before_date"] = before_date
        while True:
            r = requests.get(f"{site}/sync/nifty", headers=h, params=params, timeout=30)
            r.raise_for_status()
            rows = r.json().get("rows") or []
            if not rows:
                break
            self._merge(rows)                                  # commits locally first
            a = requests.post(f"{site}/sync/nifty/ack", headers=h,
                              json={"ids": [int(x["id"]) for x in rows]}, timeout=30)
            a.raise_for_status()
            pulled += len(rows)
            if len(rows) < BATCH:
                break
        if pulled:
            print(f"[CLOUD-SYNC] Pulled {pulled} cloud-collected NIFTY rows into the local database; removed from Cloudflare")
        return pulled

    @staticmethod
    def _merge(rows):
        from backend.storage.nifty_options_engine import NiftySession
        from backend.storage.nifty_options_models import NiftyOptionSnapshot
        db = NiftySession()
        try:
            dates = sorted({r["date"] for r in rows})
            existing = {}
            for e in db.query(NiftyOptionSnapshot).filter(NiftyOptionSnapshot.date.in_(dates)).all():
                existing[(e.snapshot_time.replace(second=0, microsecond=0), e.strike, e.option_type)] = e
            # Minutes the office collector already recorded keep their own ladder:
            # the cloud may centre ATM one strike differently, so for those minutes
            # only empty fields are filled and no strikes are added.
            local_minutes = {k[0] for k in existing}
            for r in rows:
                snap = datetime.strptime(r["snapshot_time"], "%Y-%m-%d %H:%M:%S")
                if snap in local_minutes and (snap, float(r["strike"]), r["option_type"]) not in existing:
                    continue
                vals = {k: r.get(k) for k in _FIELDS}
                for k in ("bid_depth", "ask_depth"):
                    if isinstance(vals[k], str):
                        try:
                            vals[k] = json.loads(vals[k])
                        except Exception:
                            vals[k] = None
                cur = existing.get((snap, float(r["strike"]), r["option_type"]))
                if cur is None:
                    row = NiftyOptionSnapshot(date=r["date"], snapshot_time=snap, expiry=r["expiry"],
                                              strike=float(r["strike"]), option_type=r["option_type"],
                                              moneyness_rank=int(r["moneyness_rank"]), **vals)
                    db.add(row)
                    existing[(snap, float(r["strike"]), r["option_type"])] = row
                else:
                    for k, v in vals.items():
                        if getattr(cur, k) is None and v is not None:
                            setattr(cur, k, v)
            db.commit()
        finally:
            db.close()


cloud_sync = CloudSync()
