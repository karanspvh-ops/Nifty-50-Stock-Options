"""cloud_sync.py — Keep the Cloudflare NIFTY collector supplied, and drain it.

While the office backend is offline, the team site's Worker collects NIFTY
option snapshots itself (frontend/worker/collector.js) into Cloudflare D1.
This module, running inside the backend, every SYNC_SEC:

  1. sends today's Zerodha access token + API key (needed for the Worker to
     call the quote API) whenever a new session exists
  2. sends the NIFTY option list for the next two expiries, once a day
  3. pulls every row the Worker has collected, merges it into the local
     nifty_options.db (a minute already recorded locally keeps its values;
     only empty fields are filled), commits, and only THEN tells Cloudflare
     to delete those rows -- so nothing is deleted before it is safely here

Minute OHLC and volume aren't in a quote snapshot; nifty_backfill's daily
completion fills them from Zerodha's minute history.
"""

import json
import os
import threading
import time
from datetime import date, datetime

import requests

from backend.core.clock import now_ist

_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SYNC_SEC = 300
BATCH = 1000
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
            time.sleep(SYNC_SEC)

    def run_once(self):
        site, secret = self._site()
        if not site:
            return
        h = {"X-Relay-Secret": secret}
        self._push_session(site, h)
        self._push_chain(site, h)
        self.last_pulled = self._pull(site, h)
        self.last_run = now_ist().strftime("%H:%M:%S")

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

    def _pull(self, site, h) -> int:
        """Merge everything the cloud collected, deleting it there batch by batch once saved."""
        pulled = 0
        while True:
            r = requests.get(f"{site}/sync/nifty", headers=h, params={"after": 0, "limit": BATCH}, timeout=30)
            r.raise_for_status()
            rows = r.json().get("rows") or []
            if not rows:
                break
            self._merge(rows)                                  # commits locally first
            upto = max(int(x["id"]) for x in rows)
            a = requests.post(f"{site}/sync/nifty/ack", headers=h, json={"upto": upto}, timeout=20)
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
