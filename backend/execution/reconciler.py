"""reconciler.py — Keep the system's records and Zerodha's account in agreement.

Every CHECK_SEC during market hours it compares, for every symbol the system
itself traded today (orders tagged SPVHALGO -- manual trades in the Kite app
are never touched):

  Zerodha net position   vs   open LIVE trades in the DB

and acts on a mismatch that persists across two checks (a fill or exit in
flight is skipped, so normal order flow is never mistaken for a fault):

  - Zerodha holds MORE than the system knows (e.g. an order the system saw
    as failed actually filled): the system owns the error and sells the
    excess at the best price it can get. If it can't, a critical alert stays
    up for that symbol: "LIVE and unprotected".
  - Zerodha holds a SHORT position (an extra SELL went through): bought back
    at once, critical alert.
  - Zerodha holds LESS than the system thinks (sold outside the system):
    the DB trades are closed at Zerodha's actual sell price, warning raised.
  - More than one working SELL order on a symbol, or working sells bigger
    than the position: extras are cancelled, keeping the trade's resting
    stop-loss order, warning raised.
"""

import threading
import time
from collections import defaultdict
from datetime import time as dtime

from backend.core.broker import broker
from backend.core.clock import now_ist
from backend.core import alerts
from backend.execution import live_orders, stop_orders

CHECK_SEC = 3.0
WORKING = ("OPEN", "TRIGGER PENDING", "OPEN PENDING", "VALIDATION PENDING", "PUT ORDER REQ RECEIVED",
           "MODIFY VALIDATION PENDING", "MODIFY PENDING", "AMO REQ RECEIVED")
ACTIVE_FROM, ACTIVE_TO = dtime(9, 10), dtime(15, 40)


class Reconciler:
    def __init__(self):
        self._running = False
        self._suspect: dict = {}       # (kind, symbol) -> first seen (monotonic)
        self.last_run = None
        self.last_summary = {}

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="Reconciler").start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            time.sleep(CHECK_SEC)
            now = now_ist().time()
            if not (ACTIVE_FROM <= now <= ACTIVE_TO) or not broker.has_token():
                continue
            try:
                self.check()
            except Exception as e:
                print(f"[RECON] check failed: {e}")

    def _persisted(self, kind: str, symbol: str) -> bool:
        """True once the same mismatch has been seen on two checks in a row."""
        key = (kind, symbol)
        if key in self._suspect:
            return True
        self._suspect[key] = time.monotonic()
        return False

    @staticmethod
    def _open_live():
        from backend.database import Session, Trade, TradeStatus, TradeEnv
        db = Session()
        try:
            return (db.query(Trade)
                    .filter(Trade.status == TradeStatus.OPEN, Trade.env == TradeEnv.LIVE).all())
        finally:
            db.close()

    def check(self):
        kite = broker.kite()
        orders = kite.orders() or []
        positions = (kite.positions() or {}).get("net", []) or []

        ours = {o["tradingsymbol"] for o in orders
                if o.get("tag") == live_orders.ORDER_TAG and o.get("exchange") == "NFO"}
        held = {p["tradingsymbol"]: int(p.get("quantity") or 0) for p in positions
                if p.get("exchange") == "NFO" and p.get("product") == "MIS"}

        open_live = self._open_live()
        expected = defaultdict(int)
        trades_by_sym = defaultdict(list)
        for t in open_live:
            expected[t.option_symbol] += (t.quantity or 0) * (t.lot_size or 0)
            trades_by_sym[t.option_symbol].append(t)

        working_sells = defaultdict(list)
        for o in orders:
            if (o.get("transaction_type") == "SELL" and o.get("status") in WORKING
                    and o.get("tradingsymbol") in ours):
                working_sells[o["tradingsymbol"]].append(o)

        busy = live_orders.inflight_symbols()
        seen = set()
        for sym in ours | set(expected):
            if sym in busy:
                continue
            have, want = held.get(sym, 0), expected.get(sym, 0)
            if have == want:
                self._clear(sym)
                self._dedupe_sells(sym, working_sells.get(sym, []), have)
                continue
            seen.add(sym)
            if have < 0:
                if self._persisted("short", sym):
                    self._fix_short(sym, -have)
            elif have > want:
                if self._persisted("excess", sym):
                    self._fix_excess(sym, have - want, working_sells.get(sym, []))
            else:
                if self._persisted("missing", sym):
                    self._fix_missing(sym, have, trades_by_sym.get(sym, []), orders)
        # Mismatches that resolved themselves since the last check
        for key in list(self._suspect):
            if key[1] not in seen:
                self._suspect.pop(key, None)
        self.last_run = now_ist().isoformat()
        self.last_summary = {"symbols": len(ours | set(expected)), "mismatches": len(seen)}

    def _clear(self, sym):
        for kind in ("short", "excess", "missing"):
            self._suspect.pop((kind, sym), None)
        alerts.clear_alert(f"recon:{sym}")

    # ── Fixes ───────────────────────────────────────────────────────────────

    def _fix_excess(self, sym, qty, sells):
        # A working SELL that isn't a known trade stop may already be dealing with it
        stops = stop_orders.order_ids()
        for o in sells:
            if str(o["order_id"]) not in stops:
                try:
                    broker.kite().cancel_order(variety=o.get("variety") or "regular", order_id=o["order_id"])
                except Exception as e:
                    print(f"[RECON] cancel stray sell {o['order_id']} failed: {e}")
        try:
            fill = live_orders.sell(sym, qty)
            alerts.raise_alert(f"recon:{sym}", f"{sym}: Zerodha held {qty} units the system had no record of "
                               f"(an order it saw as failed went through). Sold them @ {fill.avg_price:.2f}.",
                               level="warning", symbol=sym)
        except Exception as e:
            alerts.raise_alert(f"recon:{sym}", f"{sym}: {qty} units are open at Zerodha that the system did not "
                               f"track, and it could NOT sell them ({e}). LIVE and unprotected: sell manually.",
                               symbol=sym)
        self._suspect.pop(("excess", sym), None)

    def _fix_short(self, sym, qty):
        kite = broker.kite()
        try:
            fill = live_orders.buy(sym, qty)
            alerts.raise_alert(f"recon:{sym}", f"{sym}: an extra SELL had made the account SHORT {qty} units. "
                               f"Bought back {fill.filled_qty} @ {fill.avg_price:.2f}.", symbol=sym)
        except Exception as e:
            alerts.raise_alert(f"recon:{sym}", f"{sym}: account is SHORT {qty} units and the buy-back failed ({e}). "
                               f"Close it manually now.", symbol=sym)
        self._suspect.pop(("short", sym), None)

    def _fix_missing(self, sym, have, trades, orders):
        """Zerodha holds fewer units than the DB: book the outside sell."""
        from backend.core.risk_engine import risk_engine
        sells = [o for o in orders if o.get("tradingsymbol") == sym and o.get("transaction_type") == "SELL"
                 and o.get("status") == "COMPLETE"]
        price = float(sells[-1].get("average_price") or 0) if sells else 0.0
        if have == 0:
            for t in trades:
                stop_orders.forget(t.id)
                res = risk_engine.close_from_broker_fill(
                    t.id, price or t.entry_price, f"Closed at Zerodha outside the system @ {price:.2f}")
                print(f"[RECON] {sym} trade #{t.id}: {res}")
            alerts.raise_alert(f"recon:{sym}", f"{sym}: the position was closed at Zerodha outside the system; "
                               f"recorded at {price:.2f}.", level="warning", symbol=sym)
        else:
            alerts.raise_alert(f"recon:{sym}", f"{sym}: Zerodha holds {have} units but the system expects more. "
                               f"Check the position.", level="warning", symbol=sym)
        self._suspect.pop(("missing", sym), None)

    def _dedupe_sells(self, sym, sells, have):
        """Keep one SELL (the trade's resting stop when there is one); cancel extras."""
        if len(sells) <= 1 and sum(int(o.get("pending_quantity") or o.get("quantity") or 0) for o in sells) <= have:
            return
        stops = stop_orders.order_ids()
        keep = next((o for o in sells if str(o["order_id"]) in stops), None) or \
            sorted(sells, key=lambda o: str(o.get("order_timestamp") or ""))[0]
        cancelled = []
        for o in sells:
            if o is keep:
                continue
            try:
                broker.kite().cancel_order(variety=o.get("variety") or "regular", order_id=o["order_id"])
                cancelled.append(str(o["order_id"]))
            except Exception as e:
                print(f"[RECON] cancel duplicate sell {o['order_id']} failed: {e}")
        if cancelled:
            alerts.raise_alert(f"dup:{sym}", f"{sym}: cancelled {len(cancelled)} duplicate SELL order(s) "
                               f"({', '.join(cancelled)}); kept {keep['order_id']}.", level="warning", symbol=sym)


reconciler = Reconciler()
